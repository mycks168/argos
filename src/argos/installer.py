"""ARGOS一式インストーラの計画生成CLI。"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


DEFAULT_MANIFEST = Path(__file__).resolve().parents[2] / "installer" / "services.json"


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
) -> InstallPlan:
    """サービス定義からインストール計画を作る。"""
    steps: list[InstallStep] = [
        InstallStep("check", str(project_dir), "ARGOS本体の作業ディレクトリを確認する"),
        InstallStep("sync", str(project_dir), "uv syncでARGOS本体の仮想環境を作成する"),
        InstallStep("env", str(project_dir / ".env"), ".envがなければ.env.exampleから作成する"),
    ]
    for service in services:
        steps.extend(_service_steps(service, project_dir=project_dir, system_unit_dir=system_unit_dir, user_unit_dir=user_unit_dir))
    return InstallPlan(
        project_dir=str(project_dir),
        system_unit_dir=str(system_unit_dir),
        user_unit_dir=str(user_unit_dir),
        service_user=service_user,
        service_group=service_group,
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


def apply_plan(plan: InstallPlan, *, enable: bool = True, runner=subprocess.run) -> None:
    """インストール計画を実行する。"""
    project_dir = Path(plan.project_dir)
    _ensure_project(project_dir)
    _copy_env_example(project_dir)
    _uv_sync(project_dir, runner=runner)
    for service in plan.services:
        _apply_service(service, plan, enable=enable, runner=runner)
    if enable:
        _reload_systemd(plan, runner=runner)


def _ensure_project(project_dir: Path) -> None:
    """ARGOSプロジェクトらしいディレクトリか確認する。"""
    if not (project_dir / "pyproject.toml").exists():
        raise FileNotFoundError(f"pyproject.toml が見つかりません: {project_dir}")


def _copy_env_example(directory: Path) -> None:
    """`.env` がなければ `.env.example` から作成する。"""
    env_path = directory / ".env"
    example_path = directory / ".env.example"
    if env_path.exists() or not example_path.exists():
        return
    shutil.copyfile(example_path, env_path)


def _uv_sync(directory: Path, *, runner=subprocess.run) -> None:
    """指定ディレクトリでuv syncを実行する。"""
    if not (directory / "pyproject.toml").exists():
        return
    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    runner(["uv", "sync"], cwd=directory, check=True, env=env)


def _apply_service(service: BundledService, plan: InstallPlan, *, enable: bool, runner=subprocess.run) -> None:
    """サービス定義ごとのインストール処理を行う。"""
    project_dir = Path(plan.project_dir)
    if service.source and service.source != ".":
        source_path = project_dir / service.source
        if not source_path.exists():
            if service.bundle == "optional":
                return
            raise FileNotFoundError(f"サービスソースが見つかりません: {source_path}")
        _copy_env_example(source_path)
        if service.kind not in ("data", "external"):
            _uv_sync(source_path, runner=runner)
    if service.unit:
        target_dir = Path(plan.user_unit_dir) if service.kind == "user" else Path(plan.system_unit_dir)
        target_path = target_dir / Path(service.unit).name
        content = render_unit_template(Path(plan.project_dir) / service.unit, plan)
        _write_unit(target_path, content, runner=runner)
    if enable and service.unit and service.enabled_by_default:
        if service.kind == "user":
            runner(["systemctl", "--user", "enable", "--now", Path(service.unit).name], check=True)
        else:
            runner(["systemctl", "enable", "--now", Path(service.unit).name], check=True)


def render_unit_template(template_path: Path, plan: InstallPlan) -> str:
    """systemd unitテンプレートのプレースホルダを置換する。"""
    text = template_path.read_text(encoding="utf-8")
    return (
        text.replace("@PROJECT_DIR@", plan.project_dir)
        .replace("@ARGOS_USER@", plan.service_user)
        .replace("@ARGOS_GROUP@", plan.service_group)
        .replace("@USER_HOME@", str(Path.home()))
    )


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


def _reload_systemd(plan: InstallPlan, *, runner=subprocess.run) -> None:
    """systemd daemon-reloadを実行する。"""
    runner(["systemctl", "daemon-reload"], check=True)
    runner(["systemctl", "--user", "daemon-reload"], check=True)


def main(argv: list[str] | None = None) -> int:
    """ARGOSインストール計画CLIを実行する。"""
    parser = argparse.ArgumentParser(description="ARGOS一式のインストール計画を表示する")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST, help="サービスマニフェストのパス")
    parser.add_argument("--project-dir", type=Path, default=Path.cwd(), help="ARGOSプロジェクトディレクトリ")
    parser.add_argument("--system-unit-dir", type=Path, default=Path("/etc/systemd/system"), help="system unit出力先")
    parser.add_argument(
        "--user-unit-dir",
        type=Path,
        default=Path.home() / ".config/systemd/user",
        help="user unit出力先",
    )
    parser.add_argument("--user", default=os.environ.get("USER", "argos"), help="systemdサービス実行ユーザー")
    parser.add_argument("--group", default="", help="systemdサービス実行グループ。未指定なら--userと同じ")
    parser.add_argument("--json", action="store_true", help="計画をJSONで出力する")
    parser.add_argument("--apply", action="store_true", help="計画を実行する")
    parser.add_argument("--no-enable", action="store_true", help="unit生成だけ行い、enable/startは行わない")
    args = parser.parse_args(argv)

    services = load_manifest(args.manifest)
    plan = build_install_plan(
        services,
        project_dir=args.project_dir.resolve(),
        system_unit_dir=args.system_unit_dir,
        user_unit_dir=args.user_unit_dir.expanduser(),
        service_user=args.user,
        service_group=args.group or args.user,
    )
    if args.json:
        print(json.dumps(plan_to_dict(plan), ensure_ascii=False, indent=2))
    else:
        print(format_plan(plan))
    if args.apply:
        apply_plan(plan, enable=not args.no_enable)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
