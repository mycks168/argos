"""labwc上のAndroidとARGOSの画面配置を操作する。"""

import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET

from argos.yaml_config import load_yaml_environment

# 実機で動作を確認したアプリだけを登録する。増やすときはウィンドウの出現・全画面・分割を確認してから足す。
ANDROID_APPS = {"maps": "com.google.android.apps.maps"}
APP_SETTING = "ARGOS_WINDOW_LAYOUT_ANDROID_APP"
PROJECT_CONFIG = Path(__file__).resolve().parents[3] / "config.yaml"


DEFAULT_STATE = {"ratio": 50, "side": "left", "mode": "split"}
ARGOS_MATCH = "title:ARGOS Dashboard"


def android_id(package):
    """Waydroidがウィンドウへ付けるapp_idを返す。"""
    return f"waydroid.{package}"


def window_exists(match, *extra):
    """指定条件のウィンドウがあるか確認する。"""
    return subprocess.run(["wlrctl", "toplevel", "find", match, *extra], check=False, timeout=5).returncode == 0


def container_frozen():
    """Waydroidのコンテナが凍結中か確認する。状態が取れなければ凍結とみなさない。"""
    try:
        output = subprocess.check_output(["waydroid", "status"], text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return any(line.split()[:2] == ["Container:", "FROZEN"] for line in output.splitlines())


def wait_window(match, seconds):
    """ウィンドウが現れるまで最大seconds秒待つ。"""
    for _ in range(seconds):
        if window_exists(match):
            return True
        time.sleep(1)
    return window_exists(match)


def ensure_android(package, attempts=3, wait=15):
    """凍結の解除とAndroidアプリのウィンドウ表示を保証する。

    `waydroid app launch` は内部で凍結を解除してから起動する。起動成功でも
    ホーム画面だけが出る場合があるため、ウィンドウの出現まで確認して再試行する。
    """
    match = "app_id:" + android_id(package)
    for _ in range(attempts):
        if not container_frozen() and window_exists(match):
            return
        subprocess.run(["waydroid", "app", "launch", package], check=True, timeout=60)
        if wait_window(match, wait):
            return
    raise RuntimeError(f"Androidアプリ {package} のウィンドウを表示できませんでした")


def load_state(path):
    """保存済みの配置を読み、未保存の項目は既定値で補う。旧形式の余分な項目は捨てる。"""
    saved = json.loads(path.read_text()) if path.exists() else {}
    return {key: saved.get(key, default) for key, default in DEFAULT_STATE.items()}


def configured_package():
    """設定されたAndroidアプリのパッケージ名を返す。未設定ならNone。

    空でない環境変数を優先し、なければconfig.yamlを読む。登録のない名前は拒否する。
    古い.envの空欄がconfig.yamlの設定を打ち消さないよう、空の環境変数は無視する。
    """
    name = os.environ.get(APP_SETTING, "").strip()
    if not name:
        config = Path(os.environ.get("ARGOS_CONFIG_FILE") or PROJECT_CONFIG).expanduser()
        name = load_yaml_environment(config).get(APP_SETTING, "")
    name = name.strip()
    if not name:
        return None
    if name not in ANDROID_APPS:
        raise RuntimeError(f"未対応のAndroidアプリ設定です: {name}（対応: {', '.join(ANDROID_APPS)}）")
    return ANDROID_APPS[name]


def preflight():
    """操作対象のlabwcセッションを確認し、labwcへ渡す環境変数を返す。"""
    if not all(shutil.which(tool) for tool in ("wtype", "wlrctl")) or not os.environ.get("WAYLAND_DISPLAY"):
        raise RuntimeError("labwcのWaylandセッションとwtype・wlrctlが必要です")
    try:
        pids = subprocess.check_output(["pgrep", "-u", str(os.getuid()), "-x", "labwc"], text=True, timeout=5).split()
    except subprocess.CalledProcessError:
        pids = []
    if len(pids) != 1:
        raise RuntimeError("操作対象のlabwcセッションを一意に決められません")
    return dict(os.environ, LABWC_PID=pids[0])


def geometry(width, height, ratio, side):
    """指定比率から重なりのない左右の矩形を求める。"""
    if not 20 <= ratio <= 80 or side not in ("left", "right"):
        raise ValueError("Androidの幅は20〜80%、位置はleftまたはrightです")
    if width < 2 or height < 1:
        raise ValueError("画面サイズが不正です")
    android = round(width * ratio / 100)
    if side == "left":
        return (0, 0, android, height), (android, 0, width-android, height)
    return (width-android, 0, android, height), (0, 0, width-android, height)


def build_binding(root, state, width, height, fullscreen=()):
    """専用キーだけを置換し、他のlabwc設定を保持する。"""
    ns = root.tag.partition("}")[0] + "}" if "}" in root.tag else ""
    if ns:
        ET.register_namespace("", ns[1:-1])
    def add(parent, tag, **attrs):
        """同じ名前空間で要素を追加する。"""
        return ET.SubElement(parent, ns+tag, attrs)
    keyboard = root.find(ns+"keyboard")
    if keyboard is None:
        keyboard = add(root, "keyboard")
    key = "W-C-A-F12"
    for child in list(keyboard):
        if child.get("key") == key:
            keyboard.remove(child)
    binding = add(keyboard, "keybind", key=key)
    package = state.get("android_app")
    rectangles = geometry(width, height, state["ratio"], state["side"])
    for target, rect in zip(("android", "argos"), rectangles):
        if target == "android" and not package:
            continue
        action = add(binding, "action", name="ForEach")
        if target == "android":
            add(action, "query", identifier=android_id(package))
        else:
            add(action, "query", identifier="*chrom*", title="ARGOS Dashboard")
        then = add(action, "then")
        if target in fullscreen:
            add(then, "action", name="ToggleFullscreen")
        add(then, "action", name="UnMaximize")
        # windowRuleのSnapToEdgeで並んだウィンドウはタイル状態のため、解除しないと移動できない。
        add(then, "action", name="UnSnap")
        x,y,w,h = rect
        add(then, "action", name="ResizeTo", width=str(w), height=str(h))
        add(then, "action", name="MoveTo", x=str(x), y=str(y))
    if state["mode"] != "split":
        action = add(binding, "action", name="ForEach")
        if state["mode"] == "android":
            add(action, "query", identifier=android_id(package))
        else:
            add(action, "query", identifier="*chrom*", title="ARGOS Dashboard")
        then = add(action, "then")
        add(then, "action", name="ToggleFullscreen")
        add(then, "action", name="Raise")
        add(then, "action", name="Focus")
    return root


def main():
    """保存済みの分割設定を使って配置を切り替える。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["split", "android", "argos", "restore", "swap", "boot", "status"])
    parser.add_argument("--ratio", type=int)
    parser.add_argument("--side", choices=["left", "right"])
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=440)
    args = parser.parse_args()
    path = Path.home()/".local/state/argos/window-layout.json"
    if args.mode == "status":
        print(json.dumps(load_state(path), ensure_ascii=False))
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # ロック取得後に読み直し、同時操作による状態の取り違えを防ぐ。
        apply_layout(args, load_state(path), path)


def apply_layout(args, state, path):
    """一時的なキー割り当てで配置し、終了時に元の設定を復元する。

    bootは保存済みの状態をそのまま再適用する。Androidアプリが未設定、または
    labwcのセッションでない端末では何もしないため、Waydroidを使わない端末の
    起動サービスに入れても失敗しない。
    """
    if args.mode != "boot":
        state["mode"] = "split" if args.mode in ("restore", "swap") else args.mode
    if args.ratio is not None:
        state["ratio"] = args.ratio
    if args.side:
        state["side"] = args.side
    if args.mode == "swap":
        state["side"] = "right" if state["side"] == "left" else "left"
    package = configured_package()
    needs_android = state["mode"] != "argos"
    if needs_android and not package:
        if args.mode == "boot":
            print(json.dumps(dict(state, skipped="Androidアプリ未設定"), ensure_ascii=False))
            return
        raise RuntimeError(f"Androidアプリが未設定です。config.yamlのwindow_layout.android_appに{'/'.join(ANDROID_APPS)}を指定してください")
    try:
        env = preflight()
    except RuntimeError as exc:
        if args.mode != "boot":
            raise
        print(json.dumps(dict(state, skipped=str(exc)), ensure_ascii=False))
        return
    fullscreen = []
    if args.mode == "boot" and not wait_window(ARGOS_MATCH, 60):
        raise RuntimeError("ARGOSのウィンドウが現れませんでした")
    if needs_android:
        ensure_android(package)
    targets = [("argos", ARGOS_MATCH)]
    if package and window_exists("app_id:" + android_id(package)):
        targets.insert(0, ("android", "app_id:" + android_id(package)))
    for target, match in targets:
        subprocess.run(["wlrctl", "toplevel", "find", match], check=True, timeout=5)
        if window_exists(match, "state:fullscreen"):
            fullscreen.append(target)
    config = Path.home()/".config/labwc/rc.xml"
    original = config.read_bytes()
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    root = ET.fromstring(original, parser=parser)
    if any(node.get("key") == "W-C-A-F12" for node in root.iter()):
        raise RuntimeError("専用キーW-C-A-F12が既に使われています")
    root = build_binding(root, dict(state, android_app=package), args.width, args.height, fullscreen)
    backup = config.with_suffix(".xml.before-argos-layout")
    if not backup.exists():
        shutil.copy2(config, backup)
    temp = config.with_suffix(".xml.tmp")
    try:
        ET.ElementTree(root).write(temp, encoding="utf-8", xml_declaration=True)
        temp.replace(config)
        subprocess.run(["labwc", "-r"], check=True, env=env, timeout=5)
        time.sleep(0.5)
        subprocess.run(["wtype", "-M", "logo", "-M", "ctrl", "-M", "alt", "-k", "F12", "-m", "alt", "-m", "ctrl", "-m", "logo"], check=True, timeout=5)
        time.sleep(0.5)
    finally:
        temp.write_bytes(original)
        temp.replace(config)
        subprocess.run(["labwc", "-r"], check=True, env=env, timeout=5)
    saved = path.with_suffix(".tmp")
    saved.write_text(json.dumps(state))
    saved.replace(path)
    print(json.dumps(state, ensure_ascii=False))


if __name__ == "__main__":
    main()
