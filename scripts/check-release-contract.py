#!/usr/bin/env python3
"""Build and rehearse two committed Notice Place releases on one disposable store."""

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path
from urllib.error import URLError
from uuid import uuid4

from release_contract_probe import MCP_VERSIONS, PROJECT, http, mcp, read_events, read_sqlite, require, write_event


ROOT = Path(__file__).resolve().parents[1]
IDENTITY_FILES = (
    "VERSION", "bin/notify-center", "notification_center/core.py", "notification_center/http_api.py",
    "mcp/notify_mcp.py", "deploy/notification-center.service",
)


def run(*args, cwd, env, timeout=60):
    return subprocess.check_output(args, cwd=cwd, env=env, text=True, timeout=timeout).strip()


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def local_environment(scratch):
    # Allowlist environment variables rather than copying deployment or proxy credentials.
    return {
        "PATH": os.environ.get("PATH", os.defpath),
        "LC_ALL": "C.UTF-8",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "PIP_CONFIG_FILE": os.devnull,
        "PIP_NO_INDEX": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "npm_config_userconfig": str(scratch / "npm-user.conf"),
        "npm_config_globalconfig": str(scratch / "npm-global.conf"),
        "npm_config_cache": str(scratch / "npm-cache"),
        "npm_config_loglevel": "error",
        "npm_config_update_notifier": "false",
        "npm_config_offline": "true",
        "npm_config_ignore_scripts": "true",
        "NOTIFY_SECRETS_FILE": str(scratch / "unused.env"),
        "NOTIFY_MCP_STATE_DIR": str(scratch / "mcp-state"),
    }


def only_file(directory, pattern):
    matches = list(directory.glob(pattern))
    require(len(matches) == 1, f"expected one {pattern} in {directory}, found {matches}")
    return matches[0]


def check_legacy_mcp(directory, env):
    entrypoint = only_file(directory, "*/mcp/notify_mcp.py")
    setup = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": MCP_VERSIONS[0], "capabilities": {},
        "clientInfo": {"name": PROJECT, "version": "1"},
    }}
    listing = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    output = subprocess.check_output(
        ["python3", str(entrypoint)], cwd=directory, env=env,
        input=json.dumps(setup) + "\n" + json.dumps(listing) + "\n", text=True, timeout=15,
    )
    initialized, tools = (json.loads(line) for line in output.splitlines())
    require(initialized["result"]["protocolVersion"] == MCP_VERSIONS[0]
            and "noticeplace" in {tool["name"] for tool in tools["result"]["tools"]},
            "standalone MCP archive did not initialize its tools")
    return True


def build(revision, artifacts, scratch, env):
    artifacts.mkdir()
    scratch.mkdir()
    archive = artifacts / "noticeplace-source.tar.gz"
    run("git", "archive", "--format=tar.gz", f"--output={archive}", revision, cwd=ROOT, env=env)
    trees = [scratch / "stage", scratch / "build"]
    for tree in trees:
        tree.mkdir()
        run("tar", "-xzf", str(archive), cwd=tree, env=env)
    stage, source = trees
    (stage / ".noticeplace-release").write_text(f"revision={revision}\n")

    run("bash", "scripts/build-release-assets.sh", cwd=source, env=env, timeout=120)
    dist = artifacts / "dist"
    shutil.move(source / "dist", dist)
    run("sha256sum", "--strict", "-c", "SHA256SUMS", cwd=dist, env=env)
    npm_tarball = only_file(dist, "*.tgz")
    legacy_tar = only_file(dist, "*.tar.gz")
    legacy_zip = only_file(dist, "*.zip")

    package = scratch / "installed-clients"
    package.mkdir()
    run("npm", "install", "--offline", "--ignore-scripts", "--no-audit", "--no-fund", "--no-package-lock",
        str(npm_tarball), cwd=package, env=env)
    run("python3", "-m", "pip", "wheel", "--no-index", "--no-deps", "--no-build-isolation",
        "--wheel-dir", str(artifacts), "./python", cwd=source, env=env, timeout=120)
    wheel = only_file(artifacts, "*.whl")
    run("python3", "-m", "pip", "install", "--no-index", "--no-deps", "--target", str(package / "python"),
        str(wheel), cwd=package, env=env)

    zip_mcp = tar_mcp = False
    with zipfile.ZipFile(legacy_zip) as legacy_zip_file:
        zip_core = any(name.endswith("/notification_center/core.py") for name in legacy_zip_file.namelist())
        if zip_core:
            extracted = scratch / "legacy-zip"
            legacy_zip_file.extractall(extracted)
            zip_mcp = check_legacy_mcp(extracted, env)
    tar_core = any(name.endswith("/notification_center/core.py") for name in
                   run("tar", "-tzf", str(legacy_tar), cwd=source, env=env).splitlines())
    if tar_core:
        extracted = scratch / "legacy-tar"
        extracted.mkdir()
        run("tar", "-xzf", str(legacy_tar), cwd=extracted, env=env)
        tar_mcp = check_legacy_mcp(extracted, env)
    files = (archive, npm_tarball, wheel, legacy_tar, legacy_zip)
    report = {
        "revision": revision,
        "versions": {
            "service": (stage / "VERSION").read_text().strip(),
            "npm": json.loads((source / "package.json").read_text())["version"],
            "python": wheel.name.split("-")[1],
        },
        "sha256": {str(path.relative_to(artifacts)): sha256(path) for path in files},
        "runtime_files_sha256": {path: sha256(stage / path) for path in IDENTITY_FILES},
        "legacy_archives": {"checksums_ok": True, "tar_mcp_initialized": tar_mcp, "zip_mcp_initialized": zip_mcp},
    }
    return stage, package, report


def deploy(source, operation, root, env):
    return run(str(source / "deploy/noticeplace"), operation, "--no-start", cwd=source,
               env={**env, "NOTICEPLACE_DESTDIR": str(root), "NOTICEPLACE_SOURCE_MODE": "tree"})


def check_install(active, root, release):
    require(active.is_symlink(), "managed release symlink is missing")
    installed = active.resolve(strict=True)
    require(installed.parent == root / "opt/noticeplace-releases"
            and f"-{release['revision']}-" in installed.name, "active release revision changed")
    require({path: sha256(installed / path) for path in IDENTITY_FILES}
            == release["runtime_files_sha256"], "installed runtime differs from the source artifact")
    unit = root / "etc/systemd/system/notification-center.service"
    require(sha256(unit) == release["runtime_files_sha256"]["deploy/notification-center.service"]
            and "ExecStart=/usr/bin/python3 /opt/noticeplace/bin/notify-center" in unit.read_text(),
            "staged unit is not wired to the active entrypoint")
    return installed


def rotation_operation(active, database, env, operation, *args):
    code = """
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
from notification_center import consumer_onboarding
from notification_center.core import NotificationCenter

center = NotificationCenter(sys.argv[1], {}, bootstrap_store=False)
if sys.argv[2] == 'issue':
    old = center.create_consumer(sys.argv[3], 'Rotation check', [{
        'platform': 'matrix', 'action': 'message', 'target': {'room_id': '!fixture:local.invalid'},
        'retry_interval_seconds': 30, 'max_repeats': 1,
    }])
    new = consumer_onboarding.rotate(center, old['id'], 'issue', old['token_fingerprint'], 'release-contract')
    print(json.dumps({
        'id': old['id'], 'old': old['intake_token'], 'new': new['intake_token'],
        'fingerprint': new['replacement']['fingerprint'],
    }))
else:
    consumer_onboarding.rotate(center, sys.argv[3], sys.argv[2], sys.argv[4], 'release-contract')
"""
    output = run("python3", "-I", "-c", code, str(database), operation, *args, cwd=active, env=env)
    return json.loads(output) if output else None


@contextmanager
def service(active, database, env, tokens, health_token, mcp_token, version, scratch, stage):
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    service_env = {
        **env,
        "NOTIFY_CENTER_DB": str(database),
        "NOTIFY_CENTER_HOST": "127.0.0.1",
        "NOTIFY_CENTER_PORT": str(port),
        "NOTIFY_CENTER_HEALTH_TOKEN": health_token,
        "NOTIFY_MCP_TOKEN": mcp_token,
        "NOTIFY_CENTER_TOKENS_JSON": json.dumps(tokens),
        "NOTIFY_CENTER_MESSAGE_WORKERS": "0",
        "NOTIFY_CENTER_AGENT_WORKERS": "0",
        "NOTIFY_CENTER_CALL_WORKERS": "0",
    }
    log = scratch / f"{stage}.log"
    with log.open("w") as output:
        process = subprocess.Popen(["python3", str(active / "bin/notify-center")],
                                   cwd=active, env=service_env, stdout=output, stderr=output)
        try:
            deadline = time.monotonic() + 10
            while True:
                require(process.poll() is None, f"{stage} process exited: {log.read_text()[-3000:]}")
                try:
                    health = http(base, "GET", "/health", token=health_token)
                    break
                except URLError:
                    require(time.monotonic() < deadline, f"{stage} HTTP startup timed out")
                    time.sleep(0.05)
            require(health["schema"] == "notify.health.v1" and health["storage_ready"] is True
                    and health["version"] == version, f"{stage} health identity mismatch: {health}")
            yield base, process.pid, health
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    require(process.returncode == 0, f"{stage} did not shut down cleanly: {log.read_text()[-3000:]}")


def rehearse(scratch, env, sources, packages, receipt):
    root = scratch / "destdir"
    active = root / "opt/noticeplace"
    previous_link = root / "opt/noticeplace-releases/previous"
    database = root / "var/lib/notification-center/release-contract.sqlite3"
    previous, candidate = sources
    producer_token, other_token, health_token, mcp_token = (uuid4().hex for _ in range(4))
    tokens = {
        producer_token: {"project": PROJECT, "max_severity": "critical"},
        other_token: {"project": "another-project", "max_severity": "critical"},
    }
    env = {**env, "NOTIFY_CENTER_TOKEN": producer_token, "NOTIFY_CENTER_PROJECT": PROJECT}
    config = root / "etc/notification-center.env"
    marker = "NOTICEPLACE_RELEASE_CONTRACT=preserved\n"
    metadata = root / "var/lib/noticeplace-deploy/current-release"
    events = []
    mutes = set()
    installed_paths = {}
    rotation = None
    for stage, operation, deployed, expected, writer in (
        ("previous", "install", previous, "previous", packages[0]),
        ("candidate", "upgrade", candidate, "candidate", packages[1]),
        ("rollback", "rollback", candidate, "previous", packages[0]),
    ):
        receipt["step"] = stage
        print(f"Checking {stage} ({operation})", flush=True)
        deploy(deployed, operation, root, env)
        release = receipt["releases"][expected]
        installed = check_install(active, root, release)
        status = deploy(deployed, "status", root, env)
        require(f"managed release: {installed}" in status.splitlines(), "deploy status reports a different release")
        installed_paths[stage] = installed
        if stage == "previous":
            config.write_text(marker)
        else:
            before = "previous" if stage == "candidate" else "candidate"
            require(previous_link.resolve(strict=True) == installed_paths[before], "previous release link changed")
        if stage == "rollback":
            require(installed == installed_paths["previous"], "rollback did not restore the originally installed release")
        require(config.read_text() == marker, "operator configuration was overwritten")
        require(metadata.is_file() and metadata.read_text().strip() == str(installed),
                "release metadata differs from the active installation")
        with service(active, database, env, tokens, health_token, mcp_token,
                     release["versions"]["service"], scratch, stage) as (base, pid, health):
            client_env = {**env, "NOTIFY_CENTER_EVENT_URL": f"{base}/v1/events"}
            new = [write_event(base, client_env, writer, stage, kind) for kind in ("http", "python", "node")]
            events.extend(new)
            # Exercise both transports against the same persisted mute. The
            # candidate writes via HTTP MCP, the previous/rollback via npm stdio.
            use_http = stage == "candidate"
            key = mcp(base, mcp_token, client_env, mutes, None if use_http else writer,
                      mute_incident=new[0]["incident_id"])
            mutes.add(key)
            mcp(base, mcp_token, client_env, mutes)
            for package in packages:
                mcp(base, mcp_token, client_env, mutes, package)
            read_events(base, client_env, other_token, packages, events)
            if stage == "candidate" and (active / "notification_center/consumer_onboarding.py").is_file():
                rotation = rotation_operation(active, database, env, "issue", PROJECT)
                path = f"/v1/incidents/{events[0]['incident_id']}"
                for token in (rotation["old"], rotation["new"]):
                    http(base, "GET", path, token=token)
                rotation_operation(active, database, env, "activate",
                                   rotation["id"], rotation["fingerprint"])
            if stage in ("candidate", "rollback") and rotation:
                path = f"/v1/incidents/{events[0]['incident_id']}"
                http(base, "GET", path, status=401, token=rotation["old"])
                http(base, "GET", path, token=rotation["new"])
        sqlite = read_sqlite(database, events, mutes)
        receipt["phases"][stage] = {
            "active_release": installed.name,
            "current_release_metadata_matches_active": True,
            "version": health["version"],
            "pid": pid,
            "sqlite": sqlite,
            "active_mutes": sorted(mutes),
            "promoted_credential_verified": bool(rotation),
            "event_readback": [
                {key: event[key] for key in ("key", "event_id", "incident_id")} for event in events
            ],
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous", required=True, help="locally available trusted previous source commit")
    parser.add_argument("--candidate", default="HEAD", help="locally available trusted candidate source commit")
    parser.add_argument("--output", required=True, type=Path, help="new or empty directory for artifacts and receipt")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        parser.error("--output must be a new or empty directory")
    report_path = output / "receipt.json"
    try:
        with report_path.open("x") as report:
            report.write('{"status":"running"}\n')
    except FileExistsError:
        parser.error("--output is already in use")
    receipt = {"schema": "noticeplace.release-contract.v1", "status": "fail", "step": "resolve",
               "mcp_http_versions": MCP_VERSIONS, "releases": {}, "phases": {}}
    try:
        with tempfile.TemporaryDirectory(prefix="noticeplace-release-contract-") as name:
            scratch = Path(name)
            env = local_environment(scratch)
            refs = {
                label: run("git", "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}", cwd=ROOT, env=env)
                for label, ref in (("previous", args.previous), ("candidate", args.candidate))
            }
            require(len(set(refs.values())) == 2, "previous and candidate must be different commits")
            sources = []
            packages = []
            for label, revision in refs.items():
                receipt["step"] = f"build-{label}"
                print(f"Building {label} {revision}", flush=True)
                source, package, report = build(revision, output / label, scratch / label, env)
                if label == "candidate":
                    require(all(report["legacy_archives"].values()), "candidate standalone MCP archives are incomplete")
                sources.append(source)
                packages.append(package)
                receipt["releases"][label] = report
            rehearse(scratch, env, sources, packages, receipt)
        receipt["step"] = "complete"
        receipt["status"] = "pass"
    except Exception as error:
        receipt["error"] = str(error)
        print(f"{receipt['step']}: {error}", file=sys.stderr)
    finally:
        report_tmp = output / ".receipt.json.tmp"
        report_tmp.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
        report_tmp.replace(report_path)
    print(f"{receipt['status']}: {report_path}")
    return 0 if receipt["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
