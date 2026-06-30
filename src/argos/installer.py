"""ARGOS一式インストーラの計画生成CLI。"""

from __future__ import annotations

import argparse
import json
import os
import pwd
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable


DEFAULT_MANIFEST = Path(__file__).resolve().parents[2] / "installer" / "services.json"
DEFAULT_OS_PACKAGES = (
    "alsa-utils",
    "chromium-browser|chromium",
    "curl",
    "git",
    "tmux",
)


@dataclass(frozen=True)
class BundledService:
    """ARGOS一式で扱うサービス定義。"""

    name: str
    description: str
    kind: str
    bundle: str
    source: str = ""
    unit: str = ""
    current_path: str = ""
    endpoint: str = ""
    enabled_by_default: bool = False


@dataclass(frozen=True)
class InstallStep:
    """インストーラが実行する1手順。"""

    action: str
    target: str
    detail: str
    service: str = ""


@dataclass(frozen=True)
class InstallPlan:
    """ARGOSインストール計画。"""

    project_dir: str
    system_unit_dir: str
    user_unit_dir: str
    service_user: str
    service_group: str
    service_home: str
    service_uid: str
    bootstrap: bool
    os_packages: list[str]
    steps: list[InstallStep]
    services: list[BundledService]


def load_manifest(path: Path = DEFAULT_MANIFEST) -> list[BundledService]:
    """サービスマニフェストを読み込む。"""
    with path.open(encoding="utf-8") as fp:
        data = json.load(fp)
    services = data.get("services", [])
    if not isinstance(services, list):
        raise ValueError("services は配列で指定してください")
    return [_service_from_dict(item) for item in services]


def _service_from_dict(item: dict[str, Any]) -> BundledService:
    """辞書からサービス定義を作る。"""
    required = ("name", "description", "kind", "bundle")
    missing = [key for key in required if not item.get(key)]
    if missing:
        raise ValueError(f"サービス定義に必須項目がありません: {', '.join(missing)}")
    return BundledService(
        name=str(item["name"]),
        description=str(item["description"]),
        kind=str(item["kind"]),
        bundle=str(item["bundle"]),
        source=str(item.get("source", "")),
        unit=str(item.get("unit", "")),
        current_path=str(item.get("current_path", "")),
        endpoint=str(item.get("endpoint", "")),
        enabled_by_default=bool(item.get("enabled_by_default", False)),
    )


def build_install_plan(
    services: list[BundledService],
    *,
    project_dir: Path,
    system_unit_dir: Path,
    user_unit_dir: Path,
    service_user: str,
    service_group: str,
    service_home: Path | None = None,
    bootstrap: bool = False,
    os_packages: list[str] | None = None,
) -> InstallPlan:
    """サービス定義からインストール計画を作る。"""
    home = service_home or Path("/home") / service_user
    packages = list(os_packages or DEFAULT_OS_PACKAGES)
    steps: list[InstallStep] = [
        InstallStep("check", str(project_dir), "ARGOS本体の作業ディレクトリを確認する"),
        InstallStep("sync", str(project_dir), "uv syncでARGOS本体の仮想環境を作成する"),
        InstallStep("env", str(project_dir / ".env"), ".envがなければ.env.exampleから作成する"),
    ]
    if bootstrap:
        steps = [
            InstallStep("user", service_user, "ARGOS専用ユーザーを作成する"),
            InstallStep("group", service_user, "audio/video/input/render/gpio/i2c/spiグループへ追加する"),
            InstallStep("apt", " ".join(packages), "ARGOS実行に必要なOSパッケージを導入する"),
            InstallStep("linger", service_user, "user systemdを起動時から使えるようにする"),
            InstallStep("chown", str(project_dir), "ARGOSプロジェクトを専用ユーザー所有にする"),
            *steps,
        ]
    for service in services:
        steps.extend(_service_steps(service, project_dir=project_dir, system_unit_dir=system_unit_dir, user_unit_dir=user_unit_dir))
    return InstallPlan(
        project_dir=str(project_dir),
        system_unit_dir=str(system_unit_dir),
        user_unit_dir=str(user_unit_dir),
        service_user=service_user,
        service_group=service_group,
        service_home=str(home),
        service_uid=_lookup_uid(service_user),
        bootstrap=bootstrap,
        os_packages=packages,
        steps=steps,
        services=services,
    )


def _service_steps(
    service: BundledService,
    *,
    project_dir: Path,
    system_unit_dir: Path,
    user_unit_dir: Path,
) -> list[InstallStep]:
    """サービスごとのインストール手順を作る。"""
    if service.kind == "external":
        return [
            InstallStep(
                "configure",
                service.endpoint,
                "外部サービスとしてURLだけを.envへ設定する",
                service=service.name,
            )
        ]
    steps: list[InstallStep] = []
    if service.source and service.source != "." and service.kind not in ("data", "external"):
        source_path = project_dir / service.source
        steps.append(InstallStep("sync", str(source_path), "サブプロジェクトをuv syncする", service=service.name))
    elif service.source and service.source != "." and service.kind == "data":
        steps.append(InstallStep("check", str(project_dir / service.source), "データディレクトリの存在を確認する", service=service.name))
    if service.unit:
        unit_dir = user_unit_dir if service.kind == "user" else system_unit_dir
        unit_target = unit_dir / Path(service.unit).name
        steps.append(InstallStep("render-unit", str(unit_target), "systemd unitをテンプレートから生成する", service=service.name))
        if service.enabled_by_default:
            steps.append(InstallStep("enable", service.name, "systemd enableを行う", service=service.name))
    return steps


def plan_to_dict(plan: InstallPlan) -> dict[str, Any]:
    """インストール計画をJSON化しやすい辞書へ変換する。"""
    return {
        "project_dir": plan.project_dir,
        "system_unit_dir": plan.system_unit_dir,
        "user_unit_dir": plan.user_unit_dir,
        "service_user": plan.service_user,
        "service_group": plan.service_group,
        "service_home": plan.service_home,
        "service_uid": plan.service_uid,
        "bootstrap": plan.bootstrap,
        "os_packages": plan.os_packages,
        "services": [asdict(service) for service in plan.services],
        "steps": [asdict(step) for step in plan.steps],
    }


def format_plan(plan: InstallPlan) -> str:
    """人が読むためのインストール計画テキストを作る。"""
    lines = [
        "ARGOSインストール計画",
        f"- project_dir: {plan.project_dir}",
        f"- system_unit_dir: {plan.system_unit_dir}",
        f"- user_unit_dir: {plan.user_unit_dir}",
        f"- service_user: {plan.service_user}",
        f"- service_group: {plan.service_group}",
        f"- service_home: {plan.service_home}",
        f"- service_uid: {plan.service_uid or '(未作成)'}",
        f"- bootstrap: {'有効' if plan.bootstrap else '無効'}",
        "",
        "対象サービス:",
    ]
    for service in plan.services:
        enabled = "既定ON" if service.enabled_by_default else "任意"
        lines.append(f"- {service.name}: {service.bundle}/{service.kind} {enabled} - {service.description}")
    lines.append("")
    lines.append("手順:")
    for index, step in enumerate(plan.steps, 1):
        prefix = f"{index}. {step.action}: {step.target}"
        if step.service:
            prefix += f" ({step.service})"
        lines.append(f"{prefix} - {step.detail}")
    return "\n".join(lines)


def apply_plan(
    plan: InstallPlan,
    *,
    enable: bool = True,
    configure: bool = False,
    runner=subprocess.run,
    input_func: Callable[[str], str] = input,
    output_func: Callable[[str], None] = print,
) -> None:
    """インストール計画を実行する。"""
    project_dir = Path(plan.project_dir)
    if plan.bootstrap:
        _bootstrap_host(plan, runner=runner)
        plan = _refresh_plan_uid(plan)
    _ensure_project(project_dir)
    _copy_env_example(project_dir, runner=runner)
    if configure:
        configure_env(project_dir / ".env", runner=runner, input_func=input_func, output_func=output_func)
    _uv_sync(project_dir, runner=runner)
    for service in plan.services:
        _apply_service(service, plan, runner=runner)
    if plan.bootstrap:
        _ensure_project_owner(project_dir, plan.service_user, plan.service_group, runner=runner)
    if enable:
        _reload_systemd(plan, runner=runner)
        for service in plan.services:
            _enable_service(service, plan, runner=runner)


def _ensure_project(project_dir: Path) -> None:
    """ARGOSプロジェクトらしいディレクトリか確認する。"""
    if not (project_dir / "pyproject.toml").exists():
        raise FileNotFoundError(f"pyproject.toml が見つかりません: {project_dir}")


def _copy_env_example(
    directory: Path,
    *,
    runner=subprocess.run,
    user: str | None = None,
    home: str | None = None,
) -> None:
    """`.env` がなければ `.env.example` から作成する。"""
    env_path = directory / ".env"
    example_path = directory / ".env.example"
    if env_path.exists() or not example_path.exists():
        return
    if user:
        env = os.environ.copy()
        if home:
            env["HOME"] = home
        runner(["sudo", "-u", user, "cp", str(example_path), str(env_path)], check=True, env=env)
        return
    shutil.copyfile(example_path, env_path)


def configure_env(
    env_path: Path,
    *,
    runner=subprocess.run,
    input_func: Callable[[str], str] = input,
    output_func: Callable[[str], None] = print,
) -> None:
    """対話式に実機依存の.env設定を更新する。"""
    values = _read_env_values(env_path)
    output_func("ARGOS実機設定を行います。空入力なら現在値を維持します。")

    _ask_url(values, "STT_GATEWAY_URL", "STTゲートウェイURL", input_func=input_func)
    _ask_url(values, "VOICEVOX_URL", "VOICEVOX URL", input_func=input_func)
    _ask_url(values, "OSRM_URL", "OSRM URL", input_func=input_func)
    _ask_url(values, "ARGOS_REMOTE_LOCATION_URL", "GPS API URL", input_func=input_func)
    _ask_bool(values, "ARGOS_WAKEWORD_ENABLED", "ウェイクワードを有効にする", input_func=input_func)
    _ask_bool(values, "ARGOS_AGENT_RUNNER_URL", "Agent Runnerを使う", true_value="http://127.0.0.1:28765", false_value="", input_func=input_func)
    _ask_audio_device(values, "AUDIO_INPUT_DEVICES", "入力マイク", ["arecord", "-L"], runner=runner, input_func=input_func, output_func=output_func)
    _ask_audio_device(values, "AUDIO_OUTPUT_DEVICE", "出力デバイス", ["aplay", "-L"], runner=runner, input_func=input_func, output_func=output_func)

    _write_env_values(env_path, values)


def _read_env_values(path: Path) -> dict[str, str]:
    """envファイルからKEY=VALUE形式の値を読む。"""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key and key[0].isalpha():
            values[key] = value
    return values


def _write_env_values(path: Path, values: dict[str, str]) -> None:
    """既存のコメントを残しつつenvファイルへ値を書き戻す。"""
    lines = path.read_text(encoding="utf-8").splitlines()
    written: set[str] = set()
    output: list[str] = []
    for line in lines:
        if not line or line.lstrip().startswith("#") or "=" not in line:
            output.append(line)
            continue
        key, _value = line.split("=", 1)
        if key in values and key and key[0].isalpha():
            output.append(f"{key}={values[key]}")
            written.add(key)
        else:
            output.append(line)
    for key in sorted(set(values) - written):
        output.append(f"{key}={values[key]}")
    path.write_text("\n".join(output) + "\n", encoding="utf-8")


def _ask_url(values: dict[str, str], key: str, label: str, *, input_func: Callable[[str], str]) -> None:
    """URL文字列を対話入力で更新する。"""
    current = values.get(key, "")
    answer = input_func(f"{label} [{current or '未設定'}]: ").strip()
    if answer:
        values[key] = answer


def _ask_bool(
    values: dict[str, str],
    key: str,
    label: str,
    *,
    true_value: str = "true",
    false_value: str = "false",
    input_func: Callable[[str], str],
) -> None:
    """yes/no入力で設定値を更新する。"""
    current = values.get(key, "")
    answer = input_func(f"{label}? y/n [{current or '未設定'}]: ").strip().lower()
    if answer in {"y", "yes", "1", "true"}:
        values[key] = true_value
    elif answer in {"n", "no", "0", "false"}:
        values[key] = false_value


def _ask_audio_device(
    values: dict[str, str],
    key: str,
    label: str,
    command: list[str],
    *,
    runner=subprocess.run,
    input_func: Callable[[str], str],
    output_func: Callable[[str], None],
) -> None:
    """ALSAデバイス候補を表示して選択入力を受け付ける。"""
    current = values.get(key, "")
    candidates = _list_alsa_devices(command, runner=runner)
    output_func(f"{label}候補:")
    if candidates:
        for index, candidate in enumerate(candidates, 1):
            output_func(f"  {index}. {candidate}")
    else:
        output_func("  候補を取得できませんでした。直接入力できます。")
    answer = input_func(f"{label}番号または直接入力 [{current or '未設定'}]: ").strip()
    if not answer:
        return
    if answer.isdigit():
        index = int(answer)
        if 1 <= index <= len(candidates):
            values[key] = candidates[index - 1]
            return
    values[key] = answer


def _list_alsa_devices(command: list[str], *, runner=subprocess.run) -> list[str]:
    """arecord/aplayの出力からALSAデバイス名候補を抽出する。"""
    try:
        result = runner(command, check=False, capture_output=True, text=True)
    except OSError:
        return []
    if getattr(result, "returncode", 1) != 0:
        return []
    candidates: list[str] = []
    for line in (result.stdout or "").splitlines():
        stripped = line.strip()
        if not stripped or line.startswith(" ") or stripped.startswith("#"):
            continue
        if stripped in {"null", "pipewire", "pulse"}:
            continue
        candidates.append(stripped)
    return _unique(candidates)


def _unique(values: list[str]) -> list[str]:
    """順序を保って重複を除く。"""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _lookup_uid(user: str) -> str:
    """ユーザーが存在する場合はuidを返し、未作成なら空文字を返す。"""
    try:
        return str(pwd.getpwnam(user).pw_uid)
    except KeyError:
        return ""


def _refresh_plan_uid(plan: InstallPlan) -> InstallPlan:
    """bootstrap後にservice_uidを再解決した計画へ更新する。"""
    return InstallPlan(
        project_dir=plan.project_dir,
        system_unit_dir=plan.system_unit_dir,
        user_unit_dir=plan.user_unit_dir,
        service_user=plan.service_user,
        service_group=plan.service_group,
        service_home=plan.service_home,
        service_uid=_lookup_uid(plan.service_user),
        bootstrap=plan.bootstrap,
        os_packages=plan.os_packages,
        steps=plan.steps,
        services=plan.services,
    )


def _bootstrap_host(plan: InstallPlan, *, runner=subprocess.run) -> None:
    """ARGOS専用機として使うためのOS側初期設定を行う。"""
    _ensure_service_user(plan, runner=runner)
    _install_os_packages(plan.os_packages, runner=runner)
    _add_service_groups(plan.service_user, runner=runner)
    _enable_linger(plan.service_user, runner=runner)


def _ensure_service_user(plan: InstallPlan, *, runner=subprocess.run) -> None:
    """ARGOS専用ユーザーがなければ作成する。"""
    try:
        pwd.getpwnam(plan.service_user)
        return
    except KeyError:
        pass
    runner(
        [
            "sudo",
            "useradd",
            "--create-home",
            "--home-dir",
            plan.service_home,
            "--shell",
            "/bin/bash",
            "--user-group",
            plan.service_user,
        ],
        check=True,
    )


def _install_os_packages(packages: list[str], *, runner=subprocess.run) -> None:
    """aptで必要なOSパッケージを導入する。"""
    if not packages:
        return
    runner(["sudo", "apt-get", "update"], check=True)
    resolved = _resolve_os_packages(packages, runner=runner)
    if resolved:
        runner(["sudo", "apt-get", "install", "-y", *resolved], check=True)


def _resolve_os_packages(packages: list[str], *, runner=subprocess.run) -> list[str]:
    """ディストリ差があるパッケージ候補から導入可能な名前を選ぶ。"""
    resolved: list[str] = []
    for package in packages:
        choices = [choice.strip() for choice in package.split("|") if choice.strip()]
        if len(choices) <= 1:
            resolved.extend(choices)
            continue
        selected = _select_available_package(choices, runner=runner)
        if selected:
            resolved.append(selected)
    return resolved


def _select_available_package(choices: list[str], *, runner=subprocess.run) -> str:
    """apt-cacheで見つかった最初のパッケージ名を返す。"""
    for choice in choices:
        result = runner(["apt-cache", "show", choice], check=False, capture_output=True, text=True)
        if getattr(result, "returncode", 1) == 0:
            return choice
    return choices[0]


def _add_service_groups(user: str, *, runner=subprocess.run) -> None:
    """音声、画面、GPIO系デバイスへアクセスするためのグループを付与する。"""
    groups = [group for group in ("audio", "video", "input", "render", "gpio", "i2c", "spi") if _group_exists(group)]
    if groups:
        runner(["sudo", "usermod", "-aG", ",".join(groups), user], check=True)


def _group_exists(group: str) -> bool:
    """OSグループが存在するか確認する。"""
    try:
        import grp

        grp.getgrnam(group)
        return True
    except KeyError:
        return False


def _enable_linger(user: str, *, runner=subprocess.run) -> None:
    """ログイン前からuser serviceを動かすためlingerを有効化する。"""
    runner(["sudo", "loginctl", "enable-linger", user], check=True)
    uid = _lookup_uid(user)
    if uid:
        runner(["sudo", "systemctl", "start", f"user@{uid}.service"], check=True)


def _ensure_project_owner(project_dir: Path, user: str, group: str, *, runner=subprocess.run) -> None:
    """systemd実行ユーザーが状態ファイルや仮想環境を書けるよう所有者を揃える。"""
    runner(["sudo", "chown", "-R", f"{user}:{group}", str(project_dir)], check=True)


def _uv_sync(directory: Path, *, runner=subprocess.run, user: str | None = None, home: str | None = None) -> None:
    """指定ディレクトリでuv syncを実行する。"""
    if not (directory / "pyproject.toml").exists():
        return
    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    if home:
        env["HOME"] = home
    command = ["uv", "sync"]
    if user:
        command = ["sudo", "-u", user, *command]
    runner(command, cwd=directory, check=True, env=env)


def _apply_service(service: BundledService, plan: InstallPlan, *, runner=subprocess.run) -> None:
    """サービス定義ごとのインストール処理を行う。"""
    project_dir = Path(plan.project_dir)
    if service.source and service.source != ".":
        source_path = project_dir / service.source
        if not source_path.exists():
            if service.bundle == "optional":
                return
            raise FileNotFoundError(f"サービスソースが見つかりません: {source_path}")
        _copy_env_example(source_path, runner=runner)
        if service.kind not in ("data", "external"):
            _uv_sync(source_path, runner=runner)
    if service.unit:
        target_dir = Path(plan.user_unit_dir) if service.kind == "user" else Path(plan.system_unit_dir)
        target_path = target_dir / Path(service.unit).name
        content = render_unit_template(Path(plan.project_dir) / service.unit, plan)
        _write_unit(target_path, content, runner=runner)
        if service.kind == "user":
            _ensure_user_unit_owner(target_dir, plan, runner=runner)


def _enable_service(service: BundledService, plan: InstallPlan, *, runner=subprocess.run) -> None:
    """既定有効のsystemdサービスをenable/startする。"""
    if not service.unit or not service.enabled_by_default:
        return
    if service.kind == "user":
        _run_user_systemctl(plan, ["enable", "--now", Path(service.unit).name], runner=runner)
    else:
        runner(["systemctl", "enable", "--now", Path(service.unit).name], check=True)


def render_unit_template(template_path: Path, plan: InstallPlan) -> str:
    """systemd unitテンプレートのプレースホルダを置換する。"""
    text = template_path.read_text(encoding="utf-8")
    return (
        text.replace("@PROJECT_DIR@", plan.project_dir)
        .replace("@ARGOS_USER@", plan.service_user)
        .replace("@ARGOS_GROUP@", plan.service_group)
        .replace("@USER_HOME@", plan.service_home)
        .replace("@ARGOS_UID@", plan.service_uid or _lookup_uid(plan.service_user) or "1000")
    )


def _run_user_systemctl(plan: InstallPlan, args: list[str], *, runner=subprocess.run) -> None:
    """ARGOS専用ユーザーのuser systemdを操作する。"""
    uid = plan.service_uid or _lookup_uid(plan.service_user)
    command = ["sudo", "-u", plan.service_user]
    if uid:
        command.extend(
            [
                "env",
                f"XDG_RUNTIME_DIR=/run/user/{uid}",
                f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus",
            ]
        )
    command.extend(["systemctl", "--user", *args])
    runner(command, check=True)


def _write_unit(path: Path, content: str, *, runner=subprocess.run) -> None:
    """systemd unitを書き込む。権限がなければsudo installを使う。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except PermissionError:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as fp:
            fp.write(content)
            temp_path = fp.name
        try:
            runner(["sudo", "install", "-D", "-m", "644", temp_path, str(path)], check=True)
        finally:
            Path(temp_path).unlink(missing_ok=True)


def _ensure_user_unit_owner(path: Path, plan: InstallPlan, *, runner=subprocess.run) -> None:
    """user unit配置先をARGOS専用ユーザーの所有にする。"""
    runner(["sudo", "chown", "-R", f"{plan.service_user}:{plan.service_group}", str(path)], check=True)


def _reload_systemd(plan: InstallPlan, *, runner=subprocess.run) -> None:
    """systemd daemon-reloadを実行する。"""
    runner(["systemctl", "daemon-reload"], check=True)
    _run_user_systemctl(plan, ["daemon-reload"], runner=runner)


def main(argv: list[str] | None = None) -> int:
    """ARGOSインストール計画CLIを実行する。"""
    parser = argparse.ArgumentParser(description="ARGOS一式のインストール計画を表示する")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST, help="サービスマニフェストのパス")
    parser.add_argument("--project-dir", type=Path, default=Path.cwd(), help="ARGOSプロジェクトディレクトリ")
    parser.add_argument("--system-unit-dir", type=Path, default=Path("/etc/systemd/system"), help="system unit出力先")
    parser.add_argument(
        "--user-unit-dir",
        type=Path,
        default=None,
        help="user unit出力先。未指定ならARGOS専用ユーザーの ~/.config/systemd/user",
    )
    parser.add_argument("--user", default="argos", help="systemdサービス実行ユーザー")
    parser.add_argument("--group", default="", help="systemdサービス実行グループ。未指定なら--userと同じ")
    parser.add_argument("--home", type=Path, default=None, help="ARGOS専用ユーザーのホームディレクトリ")
    parser.add_argument("--bootstrap", action="store_true", help="ARGOS専用ユーザー作成、OSパッケージ導入、linger設定も行う")
    parser.add_argument(
        "--os-package",
        action="append",
        default=None,
        help="bootstrap時にaptで入れるOSパッケージ。複数指定可。未指定なら標準セットを使う",
    )
    parser.add_argument("--json", action="store_true", help="計画をJSONで出力する")
    parser.add_argument("--apply", action="store_true", help="計画を実行する")
    parser.add_argument("--configure", action="store_true", help=".envを対話式に設定する")
    parser.add_argument("--no-enable", action="store_true", help="unit生成だけ行い、enable/startは行わない")
    args = parser.parse_args(argv)

    services = load_manifest(args.manifest)
    service_home = args.home or Path("/home") / args.user
    user_unit_dir = args.user_unit_dir or service_home / ".config/systemd/user"
    plan = build_install_plan(
        services,
        project_dir=args.project_dir.resolve(),
        system_unit_dir=args.system_unit_dir,
        user_unit_dir=user_unit_dir.expanduser(),
        service_user=args.user,
        service_group=args.group or args.user,
        service_home=service_home,
        bootstrap=args.bootstrap,
        os_packages=args.os_package,
    )
    if args.json:
        print(json.dumps(plan_to_dict(plan), ensure_ascii=False, indent=2))
    else:
        print(format_plan(plan))
    if args.apply:
        apply_plan(plan, enable=not args.no_enable, configure=args.configure)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
