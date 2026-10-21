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
    assert "KillMode=mixed" in main
    assert "KillSignal=SIGINT" in main
    assert "TimeoutStopSec=infinity" in main


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


def test_upgrade_preserves_mesh_alias_credentials_and_release_override(tmp_path: Path) -> None:
    """Focused packaging: prevent standalone writer after mesh upgrade; <2s/max20s."""
    root = tmp_path / 'root'
    run_deploy(tmp_path, 'install', '--no-start')
    units = root / 'etc/systemd/system'
    alias = units / 'notification-center.service'
    alias.unlink()
    alias.symlink_to('/etc/systemd/system/noticeplace-mesh-api.service')
    mesh = units / 'noticeplace-mesh-api.service'
    mesh.write_text('[Service]\nEnvironmentFile=/etc/noticeplace-mesh-api.env\nExecStart=/previous/bin/notify-center\n')
    before = mesh.read_bytes()
    run_deploy(tmp_path, 'upgrade', '--no-start')
    assert alias.is_symlink()
    assert mesh.read_bytes() == before
    override = (units / 'noticeplace-mesh-api.service.d/98-managed-release.conf').read_text()
    assert current_release(root) + '/bin/notify-center' in override
    assert 'ExecStart=\n' in override
    assert 'KillMode=mixed' in override
    assert 'KillSignal=SIGINT' in override
    assert 'TimeoutStopSec=infinity' in override


def test_mesh_rollback_restart_does_not_restart_local_admin_service() -> None:
    script = DEPLOY.read_text()
    rollback_restart = script.split("restart_services_after_rollback() {", 1)[1].split("\n}", 1)[0]
    assert "if mesh_api_alias; then" in rollback_restart
    assert "systemctl restart noticeplace-mesh-api.service || true" in rollback_restart
    assert "else" in rollback_restart
    assert "systemctl restart notification-center.service notification-center-admin.service || true" in rollback_restart
    failure_path = script.split("if ! start_services; then", 1)[1].split("\n        fi", 1)[0]
    assert "restart_services_after_rollback" in failure_path
