"""labwc上のAndroidとARGOSの画面配置を操作する。"""

import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import time
import urllib.request
import xml.etree.ElementTree as ET

from argos.yaml_config import load_yaml_environment

# 実機で動作を確認したアプリだけを登録する。増やすときはウィンドウの出現・全画面・分割を確認してから足す。
ANDROID_APPS = {"maps": "com.google.android.apps.maps"}
APP_SETTING = "ARGOS_WINDOW_LAYOUT_ANDROID_APP"
PROJECT_CONFIG = Path(__file__).resolve().parents[3] / "config.yaml"
STYLE_SETTING = "ARGOS_WINDOW_LAYOUT_STYLE"
RATIO_SETTING = "ARGOS_WINDOW_LAYOUT_SPLIT_RATIO"
PANEL_HEIGHT_SETTING = "ARGOS_WINDOW_LAYOUT_PANEL_HEIGHT"
RESTART_SERVICES_SETTING = "ARGOS_WINDOW_LAYOUT_RESTART_SERVICES"
DASHBOARD_LAYOUT_SETTING = "ARGOS_WINDOW_LAYOUT_DASHBOARD_LAYOUT"
DASHBOARD_LAYOUTS = ("standard", "sp", "grid")
# 表示方式ごとの、ダッシュボードのレイアウト。ここにないモード(argos・android)は今のまま変えない。
AUTO_DASHBOARD_LAYOUT = {"split": "sp", "pane": "standard"}
KIOSK_UNIT = "argos-dashboard-kiosk.service"
LOCK_WAIT_SECONDS = 300
SWAP_SETTING = "ARGOS_WINDOW_LAYOUT_SWAP_CONVERSATION"
VIEW_SETTING = "ARGOS_WINDOW_LAYOUT_ANDROID_VIEW"
# Androidの見せ方。app=アプリごとに別のウィンドウ、full=Android全体を1つのウィンドウにして、中でアプリを切り替える。
ANDROID_VIEWS = ("app", "full")
FULL_UI_APP_ID = "Waydroid"
LXC_ATTACH = ["sudo", "-n", "lxc-attach", "-P", "/var/lib/waydroid/lxc", "-n", "waydroid", "--"]
# 表示方式の名前と、対応するモード。overlayはダッシュボードの中央ペインへ重ねる。
STYLE_MODES = {"overlay": "pane", "split": "split"}


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


def android_view():
    """Androidの見せ方(app/full)を返す。未設定はapp。不明な値は、黙って無視せず、エラーにする。"""
    view = setting(VIEW_SETTING).lower() or "app"
    if view not in ANDROID_VIEWS:
        raise RuntimeError(f"android_viewはapp/fullで指定してください: {view}")
    return view


def android_id(package):
    """Waydroidがウィンドウへ付けるapp_idを返す。fullなら、アプリによらず、Android全体で1つのウィンドウ。"""
    return FULL_UI_APP_ID if android_view() == "full" else f"waydroid.{package}"


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


def start_android_app(package):
    """Androidの中で、指定アプリを前面に出す。full表示のとき、再起動後にホーム画面のままにならないように使う。"""
    subprocess.run(
        [*LXC_ATTACH, "am", "start", "-a", "android.intent.action.MAIN", "-c", "android.intent.category.LAUNCHER", "-p", package],
        check=False,
        capture_output=True,
        timeout=20,
    )


def ensure_android(package, attempts=3, wait=15):
    """凍結の解除とAndroidのウィンドウ表示を保証する。

    app表示: `waydroid app launch` が、内部で凍結を解除してから、アプリを起動する。
    full表示: `waydroid show-full-ui` が、Android全体のウィンドウを出す。ウィンドウを新しく出したときは、
    アプリもAndroidの中で前面に出す。
    どちらも、起動成功でもホーム画面だけが出る場合があるため、ウィンドウの出現まで確認して再試行する。
    """
    match = "app_id:" + android_id(package)
    full = android_view() == "full"
    for _ in range(attempts):
        if not container_frozen() and window_exists(match):
            return
        if full:
            subprocess.run(["waydroid", "show-full-ui"], check=True, timeout=60)
        else:
            subprocess.run(["waydroid", "app", "launch", package], check=True, timeout=60)
        if wait_window(match, wait):
            if full:
                start_android_app(package)
            return
    raise RuntimeError(f"Androidのウィンドウ({package})を表示できませんでした")


def load_state(path):
    """保存済みの配置を読み、未保存の項目は設定に基づく既定値で補う。旧形式の余分な項目は捨てる。"""
    saved = json.loads(path.read_text()) if path.exists() else {}
    defaults = dict(DEFAULT_STATE, mode=default_mode(), ratio=configured_int(RATIO_SETTING, 50, 20, 80))
    return {key: saved.get(key, default) for key, default in defaults.items()}


def setting(name):
    """設定値を返す。空でない環境変数を優先し、なければconfig.yamlを読む。

    古い.envの空欄がconfig.yamlの設定を打ち消さないよう、空の環境変数は無視する。
    """
    value = os.environ.get(name, "").strip()
    if not value:
        config = Path(os.environ.get("ARGOS_CONFIG_FILE") or PROJECT_CONFIG).expanduser()
        value = load_yaml_environment(config).get(name, "").strip()
    return value


def configured_package():
    """設定されたAndroidアプリのパッケージ名を返す。未設定ならNone。登録のない名前は拒否する。"""
    name = setting(APP_SETTING)
    if not name:
        return None
    if name not in ANDROID_APPS:
        raise RuntimeError(f"未対応のAndroidアプリ設定です: {name}（対応: {', '.join(ANDROID_APPS)}）")
    return ANDROID_APPS[name]


def configured_int(name, default, minimum, maximum):
    """整数の設定値を返す。未設定なら既定値。範囲外や数値でなければエラー。"""
    raw = setting(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name}は整数で指定してください: {raw}") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name}は{minimum}〜{maximum}で指定してください: {raw}")
    return value


def default_mode():
    """設定の表示方式(overlay/split)に対応する、最初に使うモードを返す。未設定はsplit。"""
    style = setting(STYLE_SETTING) or "split"
    if style not in STYLE_MODES:
        raise RuntimeError(f"表示方式はoverlayまたはsplitで指定してください: {style}")
    return STYLE_MODES[style]


def effective_panel_height():
    """分割時に窓が使えない上部の高さ。パネル(wf-panel-pi)がない端末では0。"""
    if not shutil.which("wf-panel-pi"):
        return 0
    return configured_int(PANEL_HEIGHT_SETTING, 36, 0, 200)


def detect_display():
    """labwcの出力から画面サイズ(幅, 高さ)を取得する。複数画面・拡大・回転は未対応。"""
    try:
        outputs = json.loads(subprocess.check_output(["wlr-randr", "--json"], text=True, timeout=10))
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise RuntimeError("画面サイズを取得できません。--widthと--heightで指定してください") from exc
    outputs = [item for item in outputs if item.get("enabled")]
    if len(outputs) != 1:
        raise RuntimeError("有効な画面が1つではありません。複数画面は未対応です")
    output = outputs[0]
    if output.get("scale", 1) != 1 or output.get("transform", "normal") != "normal":
        raise RuntimeError("画面の拡大率が1.0でない、または回転している構成は未対応です")
    mode = next((item for item in output.get("modes", []) if item.get("current")), None)
    if not mode:
        raise RuntimeError("現在の画面モードを取得できません。--widthと--heightで指定してください")
    return mode["width"], mode["height"]


def resolve_display(args):
    """画面サイズ(幅, 高さ)を返す。--widthと--heightの両方があればそれを使い、なければ自動取得する。"""
    if args.width and args.height:
        return args.width, args.height
    if args.width or args.height:
        raise RuntimeError("--widthと--heightは両方指定してください")
    return detect_display()


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


def geometry(width, height, ratio, side, top=0):
    """指定比率から重なりのない左右の矩形を求める。topは上部でふさがっている高さ（パネルなど）。"""
    if not 20 <= ratio <= 80 or side not in ("left", "right"):
        raise ValueError("Androidの幅は20〜80%、位置はleftまたはrightです")
    if width < 2 or height - top < 1:
        raise ValueError("画面サイズが不正です")
    android = round(width * ratio / 100)
    usable = height - top
    if side == "left":
        return (0, top, android, usable), (android, top, width-android, usable)
    return (width-android, top, android, usable), (0, top, width-android, usable)


def build_binding(root, state, width, height, fullscreen=(), top=0):
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
    rectangles = geometry(width, height, state["ratio"], state["side"], top)
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


def plan_android(state, width, height, panel_height, size):
    """モードごとの、Androidの窓の矩形と、その矩形に必要なAndroidの描画サイズを返す。

    Waydroidの窓はAndroidの描画サイズより小さくできないため、窓の大きさと描画サイズは揃える。
    ARGOSだけを出すモードはAndroidを使わないのでNone。
    """
    mode = state["mode"]
    if mode == "argos":
        return None, None
    if mode == "android":
        return [0, 0, width, height], [width, height]
    if mode == "pane":
        pane = state["pane"] or center_pane(width, height)
        return fit_android(pane, size), pane[2:]
    android = list(geometry(width, height, state["ratio"], state["side"], panel_height)[0])
    return android, android[2:]


def user_unit_exists(unit):
    """ユーザーのsystemdにunitがあるか確認する。"""
    result = subprocess.run(["systemctl", "--user", "cat", unit], capture_output=True, check=False, timeout=10)
    return result.returncode == 0


def session_running():
    """Waydroidのセッションが動いているか確認する。"""
    try:
        output = subprocess.check_output(["waydroid", "status"], text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return any(line.split()[:2] == ["Session:", "RUNNING"] for line in output.splitlines())


def desired_dashboard_layout(mode):
    """モードに合わせてキオスクが開くダッシュボードのレイアウトを返す。変えないならNone。

    設定dashboard_layoutがstandard/sp/gridなら、モードによらずそれを使う。既定(auto)は、
    左右分割ならSP（狭い幅向け）、重ねる表示なら通常（中央ペインの計算が前提）にする。
    """
    fixed = setting(DASHBOARD_LAYOUT_SETTING).lower()
    if fixed and fixed != "auto":
        if fixed not in DASHBOARD_LAYOUTS:
            raise RuntimeError(f"dashboard_layoutはauto/standard/sp/gridで指定してください: {fixed}")
        return fixed
    return AUTO_DASHBOARD_LAYOUT.get(mode)


def apply_dashboard_layout(mode):
    """必要ならキオスクのレイアウトを切り替える。キオスクを再起動したらTrueを返す。

    キオスクの起動スクリプトが読むファイルへレイアウトを書き、変わったときだけキオスクを
    再起動する（Chromiumの再読み込みで数秒かかる）。キオスクをこのユーザーが動かしていない
    端末では何もしない。
    """
    wanted = desired_dashboard_layout(mode)
    if not wanted or not user_unit_exists(KIOSK_UNIT):
        return False
    path = Path.home()/".local/state/argos/dashboard-layout"
    if path.exists() and path.read_text().strip() == wanted:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(wanted + "\n")
    temp.replace(path)
    subprocess.run(["systemctl", "--user", "restart", KIOSK_UNIT], check=True, timeout=60)
    return True


def dashboard_call(path, payload=None):
    """ダッシュボードのAPIを呼ぶ。payloadがあればBearer認証付きのPOST、なければGETでJSONを返す。

    ダッシュボードのHTTPSが有効なら、自己署名の証明書を信頼して接続する。
    """
    port = setting("ARGOS_DASHBOARD_PORT") or "8765"
    secure = setting("ARGOS_DASHBOARD_SSL").lower() in ("1", "true", "yes", "on")
    url = f"{'https' if secure else 'http'}://127.0.0.1:{port}{path}"
    context = None
    if secure:
        certificate = Path(setting("ARGOS_DASHBOARD_SSL_CERT_PATH") or "~/.config/argos/tls/dashboard.crt").expanduser()
        if not certificate.is_file():
            raise RuntimeError(f"ダッシュボードTLS証明書が見つかりません: {certificate}")
        context = ssl.create_default_context(cafile=certificate)
    if payload is None:
        view_key = setting("ARGOS_DASHBOARD_VIEW_KEY")
        if view_key:
            url += ("&" if "?" in url else "?") + "key=" + view_key
        request = urllib.request.Request(url)
    else:
        token = setting("ARGOS_DASHBOARD_TOKEN")
        if not token:
            raise RuntimeError("ARGOS_DASHBOARD_TOKENが未設定です")
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
            method="POST",
        )
    with urllib.request.urlopen(request, timeout=10, context=context) as response:
        return json.loads(response.read().decode())


def ensure_conversation_right():
    """中央ペインに会話欄があれば、中央と右を入れ替えて会話欄を右へ移す。結果の文字列を返す。

    中央ペインには地図が重なるため、会話欄が中央にあると隠れる。中央が会話欄以外
    （通知欄や重ねた表示）なら何もしない。ダッシュボードが応答しなくても配置は止めない。
    """
    if setting(SWAP_SETTING).lower() in ("false", "no", "0", "off"):
        return "disabled"
    try:
        stacks = dashboard_call("/api/state")["slot_stacks"]
        if stacks["center"][-1]["type"] != "conversation":
            return "already"
        dashboard_call("/api/events", {"type": "swap_slots"})
    except (OSError, ValueError, KeyError, IndexError, RuntimeError) as exc:
        return f"skipped: {exc}"
    return "swapped"


def acquire_lock(lock, wait=None):
    """配置操作の排他ロックを取る。他の操作が終わるまで最大wait秒待つ。

    キオスクの再起動で配置サービス(boot)も動くため、失敗にせず順番を待つ。
    """
    for _ in range(LOCK_WAIT_SECONDS if wait is None else wait):
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            time.sleep(1)
    raise RuntimeError("別の画面配置の操作が続いているため、実行できません")


def restart_android(size, wait=90):
    """Androidの描画サイズを変えてWaydroidを再起動する。ナビなど実行中のAndroid側の処理は中断される。

    描画サイズは再起動しないと反映されない。コンテナの再起動にsudoが必要なため、
    パスワードなしで実行できない環境ではエラーにする。関連するユーザーサービス
    （GPS中継など）は、設定のrestart_servicesに書かれたものを止めてから、起動後に再開する。
    """
    services = [unit.strip() for unit in setting(RESTART_SERVICES_SETTING).split(",") if unit.strip()]
    for unit in services:
        subprocess.run(["systemctl", "--user", "stop", unit], check=False, timeout=30)
    subprocess.run(["waydroid", "prop", "set", "persist.waydroid.width", str(size[0])], check=True, timeout=30)
    subprocess.run(["waydroid", "prop", "set", "persist.waydroid.height", str(size[1])], check=True, timeout=30)
    subprocess.run(["waydroid", "session", "stop"], check=False, timeout=60)
    time.sleep(2)
    try:
        subprocess.run(["sudo", "-n", "systemctl", "restart", "waydroid-container"], check=True, timeout=120)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("Waydroidのコンテナを再起動できません。パスワードなしのsudoが必要です") from exc
    time.sleep(2)
    if user_unit_exists("waydroid-session.service"):
        subprocess.run(["systemctl", "--user", "start", "waydroid-session.service"], check=True, timeout=60)
    else:
        subprocess.Popen(["waydroid", "session", "start"], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(wait):
        if session_running():
            break
        time.sleep(1)
    else:
        raise RuntimeError("Waydroidのセッションが再起動後に立ち上がりませんでした")
    for unit in services:
        subprocess.run(["systemctl", "--user", "start", unit], check=False, timeout=30)


def main():
    """保存済みの配置設定を使って、ARGOSとAndroidの画面配置を切り替える。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["split", "android", "argos", "pane", "show", "hide", "restore", "swap", "boot", "status"])
    parser.add_argument("--ratio", type=int)
    parser.add_argument("--side", choices=["left", "right"])
    parser.add_argument("--pane", help="paneモードでAndroidを重ねる範囲 x,y,w,h。autoで画面幅から自動計算")
    parser.add_argument("--width", type=int, help="画面の幅。省略すると出力から自動取得する")
    parser.add_argument("--height", type=int, help="画面の高さ。省略すると出力から自動取得する")
    parser.add_argument("--restart-android", action="store_true", help="Androidの描画サイズが合わないとき、サイズを変えてWaydroidを再起動する（ナビが中断される）")
    args = parser.parse_args()
    path = Path.home()/".local/state/argos/window-layout.json"
    if args.mode == "status":
        print(json.dumps(load_state(path), ensure_ascii=False))
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("w") as lock:
        acquire_lock(lock)
        # ロック取得後に読み直し、同時操作による状態の取り違えを防ぐ。
        apply_layout(args, load_state(path), path)


def update_state(args, state):
    """コマンドに合わせて、モード・比率・左右・戻し先を更新する。"""
    previous = state["mode"]
    if args.mode == "show":
        # 隠す前の配置へ戻す。隠していなければ今の配置を再適用する。
        state["mode"] = (state["return_mode"] or default_mode()) if previous == "argos" else previous
    elif args.mode == "hide":
        state["mode"] = "argos"
    elif args.mode == "restore":
        state["mode"] = default_mode()
    elif args.mode == "swap":
        state["mode"] = "split"
    elif args.mode != "boot":
        state["mode"] = args.mode
    if state["mode"] == "argos" and previous != "argos":
        state["return_mode"] = previous
    if args.ratio is not None:
        state["ratio"] = args.ratio
    if args.side:
        state["side"] = args.side
    if args.mode == "swap":
        state["side"] = "right" if state["side"] == "left" else "left"


def apply_layout(args, state, path):
    """一時的なキー割り当てで配置し、終了時に元の設定を復元する。

    bootは保存済みの状態をそのまま再適用する。Androidアプリが未設定、または
    labwcのセッションでない端末では何もしないため、Waydroidを使わない端末の
    起動サービスに入れても失敗しない。
    """
    update_state(args, state)
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
    width, height = resolve_display(args)
    if args.pane is not None:
        state["pane"] = parse_pane(args.pane, width, height)
    panel_height = effective_panel_height()
    size = android_size() if needs_android else None
    android_rect, needed = plan_android(state, width, height, panel_height, size)
    restart_required = bool(needed and size and needed != size)
    if restart_required and args.restart_android:
        restart_android(needed)
        size, restart_required = needed, False
        android_rect, needed = plan_android(state, width, height, panel_height, size)
    fullscreen = []
    # Androidの復旧はARGOSの起動を待たずに先に行う。ARGOSのダッシュボードは起動が遅いことがある。
    if needs_android:
        ensure_android(package)
    # ダッシュボードのレイアウトが変わるときはキオスクが再起動するので、新しい窓が出るまで待つ。
    restarted = apply_dashboard_layout(state["mode"])
    if restarted and not wait_window(ARGOS_MATCH, 120):
        raise RuntimeError("ダッシュボードのレイアウト切り替え後に、ARGOSのウィンドウが現れませんでした")
    if args.mode == "boot" and not wait_window(ARGOS_MATCH, BOOT_WAIT_SECONDS):
        raise RuntimeError("ARGOSのウィンドウが現れませんでした")
    if state["mode"] == "pane":
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
    top = panel_height if state["mode"] in ("split", "android") else 0
    root = build_binding(root, dict(state, android_app=package, pane=android_rect), width, height, fullscreen, top)
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
    report = dict(state, display=[width, height], dashboard_layout=desired_dashboard_layout(state["mode"]), dashboard_restarted=restarted)
    if state["mode"] == "pane":
        report["conversation"] = ensure_conversation_right()
    if needs_android:
        report.update(android_rect=android_rect, android_needed=needed, android_current=size, restart_required=restart_required)
        if state["mode"] == "pane":
            report["pane_rect"] = state["pane"] or center_pane(width, height)
        # Waydroidの窓はAndroidの描画サイズより小さくできないため、サイズが違うとはみ出す。
        report["android_fit"] = not restart_required
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
