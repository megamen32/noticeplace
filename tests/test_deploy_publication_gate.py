"""The mesh production gate refuses an untagged or reused manual upgrade.

Purpose: reproduce the defect where a manual `deploy/noticeplace upgrade` on a
mesh node could move the active pointer to an untagged revision or to the
version that is already installed, bypassing the automatic rollout that owns
the expected release and the published version tag.

Expected 5 seconds; maximum 30 seconds. No network: the published remote is a
local bare repository, so the tag lookup and the publication fetch are offline.
"""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], check=True,
                            capture_output=True, text=True)
    return result.stdout.strip()


def fixture(tmp_path: Path) -> tuple[Path, Path]:
    """A source repository with two published versions and a local remote."""
    source = tmp_path / "source"
    (source / "bin").mkdir(parents=True)
    (source / "deploy").mkdir(parents=True)
    for item in (ROOT / "deploy").iterdir():
        if item.is_file():
            target = source / "deploy" / item.name
            target.write_bytes(item.read_bytes())
            target.chmod(item.stat().st_mode & 0o777)
    (source / "bin" / "notify-center").write_text("#!/usr/bin/env python3\n")
    (source / "package.json").write_text("{}\n")
    (source / "VERSION").write_text("0.1.22\n")
    git(source, "init", "-q", "-b", "main")
    git(source, "config", "user.email", "fixture@local")
    git(source, "config", "user.name", "fixture")
    git(source, "add", "-A")
    git(source, "commit", "-q", "-m", "fixture 0.1.22")
    git(source, "tag", "v0.1.22")
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(source, "remote", "add", "origin", str(remote))
    git(source, "push", "-q", "origin", "main", "--tags")
    return source, remote


def publish(source: Path, remote: Path, version: str) -> str:
    """Publish one new version and return its tagged revision."""
    (source / "VERSION").write_text(version + "\n")
    git(source, "add", "-A")
    git(source, "commit", "-q", "-m", f"fixture {version}")
    git(source, "tag", f"v{version}")
    git(source, "push", "-q", "origin", "main", "--tags")
    return git(source, "rev-parse", f"v{version}^{{}}")


def install(source: Path, root: Path, mesh: bool = True) -> Path:
    """Install the first release; a mesh node points at the mesh API unit."""
    env = {**os.environ, "NOTICEPLACE_DESTDIR": str(root)}
    subprocess.run([str(source / "deploy" / "noticeplace"), "install", "--no-start"],
                   cwd=source, env=env, check=True, capture_output=True, text=True)
    if mesh:
        unit = root / "etc" / "systemd" / "system" / "notification-center.service"
        unit.unlink()
        unit.symlink_to("noticeplace-mesh-api.service")
    return (root / "opt" / "noticeplace").resolve(strict=True)


def upgrade(source: Path, remote: Path, root: Path, revision: str | None = None, **extra: str):
    env = {
        **os.environ,
        "NOTICEPLACE_DESTDIR": str(root),
        "NOTICEPLACE_PUBLICATION_REMOTE": str(remote),
        # The production path always verifies the publication, even when the
        # release is staged below a temporary root.
        "NOTICEPLACE_REQUIRE_PUBLISHED": "1",
    }
    if revision:
        env["NOTICEPLACE_PUBLISHED_SHA"] = revision
    env.update(extra)
    return subprocess.run([str(source / "deploy" / "noticeplace"), "upgrade", "--no-start"],
                          cwd=source, env=env, capture_output=True, text=True)


def test_a_mesh_upgrade_without_the_rollout_expected_release_is_refused(tmp_path: Path) -> None:
    source, remote = fixture(tmp_path)
    root = tmp_path / "root"
    installed = install(source, root)
    revision = publish(source, remote, "0.1.23")

    result = upgrade(source, remote, root, revision)

    assert result.returncode != 0
    assert "upgrade was not started" in result.stderr
    assert (root / "opt" / "noticeplace").resolve(strict=True) == installed, \
        "the active pointer must not move when the gate refuses"


def test_a_mesh_upgrade_to_an_untagged_revision_is_refused(tmp_path: Path) -> None:
    source, remote = fixture(tmp_path)
    root = tmp_path / "root"
    installed = install(source, root)
    (source / "VERSION").write_text("0.1.24\n")
    git(source, "add", "-A")
    git(source, "commit", "-q", "-m", "untagged")
    git(source, "push", "-q", "origin", "main")
    revision = git(source, "rev-parse", "HEAD")

    result = upgrade(source, remote, root, revision,
                     NOTICEPLACE_EXPECTED_RELEASE=str(installed))

    assert result.returncode != 0
    assert "upgrade was not started" in result.stderr
    assert (root / "opt" / "noticeplace").resolve(strict=True) == installed


def test_a_mesh_upgrade_to_the_installed_version_is_refused(tmp_path: Path) -> None:
    source, remote = fixture(tmp_path)
    root = tmp_path / "root"
    installed = install(source, root)
    revision = git(source, "rev-parse", "v0.1.22^{}")

    result = upgrade(source, remote, root, revision,
                     NOTICEPLACE_EXPECTED_RELEASE=str(installed))

    assert result.returncode != 0
    assert "upgrade was not started" in result.stderr
    assert (root / "opt" / "noticeplace").resolve(strict=True) == installed


def test_a_mesh_upgrade_to_a_new_published_tag_is_accepted(tmp_path: Path) -> None:
    source, remote = fixture(tmp_path)
    root = tmp_path / "root"
    installed = install(source, root)
    revision = publish(source, remote, "0.1.23")

    result = upgrade(source, remote, root, revision,
                     NOTICEPLACE_EXPECTED_RELEASE=str(installed))

    assert result.returncode == 0, result.stderr
    assert "Verified published source" in result.stdout
    assert (root / "opt" / "noticeplace").resolve(strict=True) != installed
    assert (root / "opt" / "noticeplace" / "VERSION").read_text().strip() == "0.1.23"


def test_a_tagged_target_survives_a_later_main_runtime_edit(tmp_path: Path) -> None:
    source, remote = fixture(tmp_path)
    root = tmp_path / "root"
    installed = install(source, root)
    revision = publish(source, remote, "0.1.23")
    # main advances with a later runtime edit after the tag was published.
    (source / "bin" / "notify-center").write_text("#!/usr/bin/env python3\n# later edit\n")
    git(source, "add", "-A")
    git(source, "commit", "-q", "-m", "later runtime edit")
    git(source, "push", "-q", "origin", "main")

    result = upgrade(source, remote, root, revision,
                     NOTICEPLACE_EXPECTED_RELEASE=str(installed))

    assert result.returncode == 0, result.stderr
    assert (root / "opt" / "noticeplace" / "VERSION").read_text().strip() == "0.1.23"


def test_a_standalone_upgrade_is_not_gated_by_the_mesh_rule(tmp_path: Path) -> None:
    source, remote = fixture(tmp_path)
    root = tmp_path / "root"
    install(source, root, mesh=False)
    revision = publish(source, remote, "0.1.23")

    result = upgrade(source, remote, root, revision)

    assert result.returncode == 0, result.stderr
    assert (root / "opt" / "noticeplace" / "VERSION").read_text().strip() == "0.1.23"
