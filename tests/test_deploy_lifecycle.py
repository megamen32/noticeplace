from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "noticeplace"


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
