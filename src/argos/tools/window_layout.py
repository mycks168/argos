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


DEFAULT_STATE = {"ratio": 50, "side": "left", "mode": "split", "pane": None, "panel_hidden": False, "return_mode": None}
ARGOS_MATCH = "title:ARGOS Dashboard"
# 起動直後はダッシュボードが立ち上がるまで接続待ち画面のままなので、長めに待つ。
BOOT_WAIT_SECONDS = 300


# ダッシュボード(dashboard.html)の.dashboardが使うgrid-template-columnsと同じ値。
# 画面幅ごとに (最小幅px, fr) の3列になる。CSSを変えたらここも合わせる。
GRID_WIDE = ((250, 0.82), (660, 2.0), (320, 1.0))
GRID_NARROW = ((178, 0.74), (345, 1.55), (235, 0.95))
GRID_GAP = 1
# 状態を示す枠(body::before/after)は、画面の端から5px内側に幅6pxで描かれる。
# 地図が上下いっぱいまで広がると枠の上下が隠れるため、その分だけ内側に収める。
FRAME_MARGIN = 12
PANEL_BINARY = "/usr/bin/wf-panel-pi"


def center_pane(width, height):
    """ダッシュボード中央ペインの位置と大きさを、画面幅からCSS gridと同じ計算で求める。

    上下は状態を示す枠を隠さないよう、FRAME_MARGINだけ内側に収める。

    minmax(最小幅, fr)の列は、fr按分で最小幅を下回る列を最小幅に固定して残りを再按分する。
    通常レイアウト（幅901px以上と761〜900px）だけが対象で、それより狭い表示は未対応。
    """
    if width <= 760:
        raise ValueError("画面幅が狭く、3分割の通常レイアウトではありません")
    if height <= 2 * FRAME_MARGIN:
        raise ValueError("画面の高さが足りません")
    tracks = GRID_WIDE if width > 900 else GRID_NARROW
    free = width - GRID_GAP * (len(tracks) - 1)
    if free < sum(minimum for minimum, _ in tracks):
        raise ValueError("画面幅が3列の最小幅の合計に足りません")
    fixed = {}
    while True:
        rest = free - sum(fixed.values())
        total = sum(fr for index, (_, fr) in enumerate(tracks) if index not in fixed)
        unit = rest / total
        low = [index for index, (minimum, fr) in enumerate(tracks) if index not in fixed and unit * fr < minimum]
        if not low:
            break
        fixed[low[0]] = tracks[low[0]][0]
    sizes = [fixed.get(index, unit * fr) for index, (_, fr) in enumerate(tracks)]
    left = sizes[0] + GRID_GAP
    return [round(left), FRAME_MARGIN, round(left + sizes[1]) - round(left), height - 2 * FRAME_MARGIN]


def android_size():
    """WaydroidのAndroid描画サイズ(幅, 高さ)を返す。取得できなければNone。"""
    values = []
    for prop in ("persist.waydroid.width", "persist.waydroid.height"):
        try:
            output = subprocess.check_output(["waydroid", "prop", "get", prop], text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return None
        lines = output.strip().splitlines()
        digits = "".join(char for char in (lines[-1] if lines else "") if char.isdigit())
        if not digits:
            return None
        values.append(int(digits))
    return values if all(value > 0 for value in values) else None


def fit_android(pane, size):
    """ペインの中に、Androidの実サイズを中央寄せで収める矩形を返す。

    ペインより大きいサイズは指定しても窓が縮まずはみ出すため、矩形はペイン内に収めて要求する。
    サイズが不明ならペイン全体を使う。
    """
    x, y, w, h = pane
    if not size:
        return list(pane)
    width, height = min(size[0], w), min(size[1], h)
    return [x + (w - width) // 2, y + (h - height) // 2, width, height]


def panel_running():
    """Raspberry Pi OSのパネル(wf-panel-pi)が動いているか確認する。"""
    result = subprocess.run(["pgrep", "-u", str(os.getuid()), "-x", "wf-panel-pi"], capture_output=True, check=False, timeout=5)
    return result.returncode == 0


def hide_panel():
    """パネルを止める。パネルが確保する上部の領域があると、窓を画面の最上端に置けない。

    lwrespawnが再起動してしまうため、先に見張り役を止める。
    """
    uid = str(os.getuid())
    subprocess.run(["pkill", "-u", uid, "-f", f"^/bin/sh .*lwrespawn {PANEL_BINARY}$"], check=False, timeout=5)
    subprocess.run(["pkill", "-u", uid, "-x", "wf-panel-pi"], check=False, timeout=5)


def show_panel():
    """止めたパネルを、元と同じ見張り役付きで起動し直す。"""
    respawn = shutil.which("lwrespawn")
    command = [respawn, PANEL_BINARY] if respawn else [PANEL_BINARY]
    subprocess.Popen(command, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# labwcのToggleAlwaysOnTop/Bottomは、ForEachで選んだ窓ではなく「フォーカス中の窓」に効く。
# そのため先にFocusで対象の窓へフォーカスを移す。また切り替えは現在の層から変わるだけで、
# 層の取得はできない。層は 通常・最前面・最背面 の3つで、次の並びなら現在の層によらず結果が決まる。
LAYER_TOP = ("Focus", "ToggleAlwaysOnBottom", "ToggleAlwaysOnBottom", "ToggleAlwaysOnTop")
LAYER_NORMAL = ("Focus", "ToggleAlwaysOnBottom", "ToggleAlwaysOnTop", "ToggleAlwaysOnTop")


def parse_pane(value, width, height):
    """"x,y,w,h形式の矩形を検証して整数のリストで返す。autoならNone（画面幅から自動計算）。画面外の指定は拒否する。"""
    if str(value).strip() == "auto":
        return None
    try:
        x, y, w, h = (int(part) for part in str(value).split(","))
    except ValueError as exc:
        raise ValueError("--paneはx,y,w,hの整数4つで指定します") from exc
    if x < 0 or y < 0 or w < 1 or h < 1 or x + w > width or y + h > height:
        raise ValueError("--paneが画面の範囲外です")
    return [x, y, w, h]


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
    if state["mode"] == "pane":
        # ARGOSを全画面にし、その上の指定範囲へAndroidを重ねる。重なり順は最後のRaiseで決まる。
        for target in ("argos", "android"):
            action = add(binding, "action", name="ForEach")
            if target == "android":
                add(action, "query", identifier=android_id(package))
            else:
                add(action, "query", identifier="*chrom*", title="ARGOS Dashboard")
            then = add(action, "then")
            if target == "argos":
                if target not in fullscreen:
                    add(then, "action", name="ToggleFullscreen")
            else:
                x, y, w, h = state["pane"]
                if target in fullscreen:
                    add(then, "action", name="ToggleFullscreen")
                # ARGOS側をタッチして前面に出ても、地図が隠れないよう最前面に固定する。
                for name in LAYER_TOP:
                    add(then, "action", name=name)
                add(then, "action", name="UnMaximize")
                add(then, "action", name="UnSnap")
                add(then, "action", name="ResizeTo", width=str(w), height=str(h))
                add(then, "action", name="MoveTo", x=str(x), y=str(y))
            add(then, "action", name="Raise")
        return root
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
        if target == "android":
            # paneモードで最前面にした場合に備え、通常の層へ戻す。
            for name in LAYER_NORMAL:
                add(then, "action", name=name)
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
    parser.add_argument("mode", choices=["split", "android", "argos", "pane", "show", "hide", "restore", "swap", "boot", "status"])
    parser.add_argument("--ratio", type=int)
    parser.add_argument("--side", choices=["left", "right"])
    parser.add_argument("--pane", help="paneモードでAndroidを重ねる範囲 x,y,w,h。autoで画面幅から自動計算")
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
    previous = state["mode"]
    if args.mode == "show":
        # 隠す前の配置へ戻す。隠していなければ今の配置を再適用する。
        state["mode"] = (state["return_mode"] or "split") if previous == "argos" else previous
    elif args.mode == "hide":
        state["mode"] = "argos"
    elif args.mode != "boot":
        state["mode"] = "split" if args.mode in ("restore", "swap") else args.mode
    if state["mode"] == "argos" and previous != "argos":
        state["return_mode"] = previous
    if args.ratio is not None:
        state["ratio"] = args.ratio
    if args.side:
        state["side"] = args.side
    if args.mode == "swap":
        state["side"] = "right" if state["side"] == "left" else "left"
    if args.pane is not None:
        state["pane"] = parse_pane(args.pane, args.width, args.height)
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
    # Androidの復旧はARGOSの起動を待たずに先に行う。ARGOSのダッシュボードは起動が遅いことがある。
    if needs_android:
        ensure_android(package)
    if args.mode == "boot" and not wait_window(ARGOS_MATCH, BOOT_WAIT_SECONDS):
        raise RuntimeError("ARGOSのウィンドウが現れませんでした")
    android_rect = size = None
    if state["mode"] == "pane":
        pane = state["pane"] or center_pane(args.width, args.height)
        size = android_size()
        android_rect = fit_android(pane, size)
        # 上部を占有するパネルがあると窓を最上端に置けないため、先に止める。
        if shutil.which("wf-panel-pi") and (state["panel_hidden"] or panel_running()):
            hide_panel()
            state["panel_hidden"] = True
            time.sleep(0.5)
    elif state["panel_hidden"]:
        # 止めたのはこのツールなので、通常の配置に戻すときは再開する。
        if not panel_running():
            show_panel()
            time.sleep(1)
        state["panel_hidden"] = False
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
    root = build_binding(root, dict(state, android_app=package, pane=android_rect), args.width, args.height, fullscreen)
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
    report = dict(state)
    if android_rect:
        report.update(
            pane_rect=pane,
            android_rect=android_rect,
            # Waydroidの窓はAndroidの描画サイズより小さくできないため、サイズが違うとはみ出す。
            android_fit=android_rect == pane and (not size or size == pane[2:]),
        )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
