from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "noticeplace"
SYNC_CALLBACK_TOKENS = ROOT / "scripts" / "sync-health-callback-tokens.py"


def run_deploy(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["NOTICEPLACE_DESTDIR"] = str(tmp_path / "root")
    env["NOTICEPLACE_SOURCE_MODE"] = "tree"
    return subprocess.run(
        [str(DEPLOY), *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )


def current_release(root: Path) -> str:
    return os.readlink(root / "opt" / "noticeplace")


def test_release_archives_include_the_deploy_lifecycle() -> None:
    builder = (ROOT / "scripts" / "build-release-assets.sh").read_text()
    assert "docs deploy assets" in builder


def test_persistent_units_have_explicit_resource_budgets() -> None:
    main = (ROOT / "deploy" / "notification-center.service").read_text()
    admin = (ROOT / "deploy" / "notification-center-admin.service").read_text()
    for unit in (main, admin):
        for directive in ("MemoryHigh=", "MemoryMax=", "MemorySwapMax=", "CPUQuota=", "TasksMax=", "IOWeight="):
            assert directive in unit


def test_health_callback_map_receives_every_configured_project_token(tmp_path: Path) -> None:
    environment = tmp_path / "notification-center.env"
    callback = tmp_path / "health-callback.json"
    environment.write_text('NOTIFY_CENTER_TOKENS_JSON={"token-a":{"project":"hermes","max_severity":"critical"},"token-b":{"project":"fleet-health","max_severity":"critical","agent_jobs":["health-diagnosis"]}}\n')
    callback.write_text('{"url":"http://127.0.0.1:8091","token":"legacy","tokens":{"health-monitor":"health-token"}}\n')
    callback.chmod(0o600)

    subprocess.run([str(SYNC_CALLBACK_TOKENS), str(environment), str(callback)], check=True, text=True, capture_output=True)

    configured = __import__("json").loads(callback.read_text())
    assert configured["tokens"] == {
        "fleet-health": "token-b",
        "health-monitor": "health-token",
        "hermes": "token-a",
    }
    assert callback.stat().st_mode & 0o777 == 0o600


def test_managed_deploy_upgrade_rollback_uninstall_and_purge(tmp_path: Path) -> None:
    staged_root = tmp_path / "root"

    run_deploy(tmp_path, "install", "--no-start")
    first = current_release(staged_root)

    assert (staged_root / "opt" / "noticeplace" / "bin" / "notify-center").exists()
    assert (staged_root / "opt" / "noticeplace" / "deploy" / "noticeplace").exists()
    assert (staged_root / "etc" / "notification-center.env").exists()
    assert "/opt/noticeplace/bin/notify-center" in (
        staged_root / "etc" / "systemd" / "system" / "notification-center.service"
    ).read_text()

    primary_env = staged_root / "etc" / "notification-center.env"
    primary_env.write_text("NOTIFY_TEST_MARKER=preserved\n")
    run_deploy(tmp_path, "upgrade", "--no-start")
    second = current_release(staged_root)

    assert second != first
    assert os.readlink(staged_root / "opt" / "noticeplace-releases" / "previous") == first
    assert primary_env.read_text() == "NOTIFY_TEST_MARKER=preserved\n"

    run_deploy(tmp_path, "rollback", "--no-start")
    assert current_release(staged_root) == first

    status = run_deploy(tmp_path, "status")
    assert "managed release:" in status.stdout
    assert "configuration: present" in status.stdout

    state_dir = staged_root / "var" / "lib" / "notification-center"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "keep.sqlite3").write_text("state")
    run_deploy(tmp_path, "uninstall")

    assert not (staged_root / "opt" / "noticeplace").exists()
    assert not (staged_root / "etc" / "systemd" / "system" / "notification-center.service").exists()
    assert primary_env.exists()
    assert (state_dir / "keep.sqlite3").exists()

    run_deploy(tmp_path, "purge", "--yes")
    assert not primary_env.exists()
    assert not state_dir.exists()
