#!/usr/bin/env python3
"""Publish the committed public landing without restarting any NoticePlace worker.

The exact nginx root location serves an immutable HTML with a SHA-256 CSP.
Only this owned include and the selected TLS vhost are changed. No API, SSO,
mesh, delivery policy, credentials, or runtime release is modified.
"""
from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "web/landing/dist/NoticePlace.html"
HOST = "notify.bezrabotnyi.com"
WEBROOT = Path("/var/www/noticeplace-landing")
SNIPPET = Path("/etc/nginx/snippets/noticeplace-landing.conf")
BACKUPS = Path("/var/backups/noticeplace-landing")
MARKER = "# Managed by noticeplace/deploy/landing.py; public root only."


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(*args: str) -> bytes:
    return subprocess.check_output(
        ["git", "-c", f"safe.directory={ROOT}", "-C", str(ROOT), *args], timeout=20
    )


def script_hashes(html: bytes) -> list[str]:
    scripts = re.findall(rb"<script(?:\s[^>]*)?>(.*?)</script>", html, re.S)
    if len(scripts) != 1:
        raise ValueError("Expected exactly one reviewed embedded script")
    return ["'sha256-" + base64.b64encode(hashlib.sha256(s).digest()).decode() + "'" for s in scripts]


def nginx_snippet(html: bytes, release: Path) -> bytes:
    if not re.fullmatch(r"/[A-Za-z0-9/_.-]+", str(release)):
        raise ValueError("Unsafe release path")
    scripts = " ".join(script_hashes(html))
    csp = (f"default-src 'none'; script-src {scripts}; style-src 'unsafe-inline'; "
           "img-src 'self' data:; connect-src 'none'; object-src 'none'; "
           "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
    return (f"{MARKER}\n"
            "location = / {\n"
            f"    root {release};\n"
            "    try_files /index.html =404;\n"
            "    default_type text/html;\n"
            "    charset utf-8;\n"
            "    etag on;\n"
            "    add_header Cache-Control \"no-cache\" always;\n"
            "    add_header X-Content-Type-Options \"nosniff\" always;\n"
            "    add_header Referrer-Policy \"strict-origin-when-cross-origin\" always;\n"
            "    add_header Strict-Transport-Security \"max-age=63072000\" always;\n"
            f"    add_header Content-Security-Policy \"{csp}\" always;\n"
            "}\n").encode()


def integrate_vhost(text: str, snippet: Path = SNIPPET) -> str:
    """Add our include only to the unique existing TLS vhost; preserve all else."""
    matches = []
    for match in re.finditer(r"(?m)^server\s*\{", text):
        start = match.start()
        depth, end = 1, match.end()
        while depth and end < len(text):
            depth += (text[end] == "{") - (text[end] == "}")
            end += 1
        block = text[start:end]
        if re.search(r"\bserver_name\s+" + re.escape(HOST) + r"\s*;", block) and re.search(r"\blisten\s+[^;]*\bssl\b[^;]*;", block):
            matches.append((start, end, block))
    if len(matches) != 1:
        raise ValueError("Expected exactly one NoticePlace TLS server block")
    start, end, block = matches[0]
    include = f"    include {snippet};"
    if include in block:
        if text.count(include) != 1:
            raise ValueError("Duplicate landing include")
        return text
    if re.search(r"\blocation\s*=\s*/\s*\{", block):
        raise ValueError("An existing exact root location requires operator review")
    updated = re.sub(r"(\bserver_name\s+" + re.escape(HOST) + r"\s*;)", r"\1\n\n" + include, block, count=1)
    return text[:start] + updated + text[end:]


def atomic_write(path: Path, data: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = path.stat() if path.exists() else None
    fd, name = tempfile.mkstemp(prefix=".noticeplace-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), stat.S_IMODE(previous.st_mode) if previous else mode)
            if previous:
                os.fchown(handle.fileno(), previous.st_uid, previous.st_gid)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def run(*cmd: str) -> None:
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=25)
    if result.returncode:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{result.stdout.decode(errors='replace')[-4000:]}")


def local_get(path: str) -> tuple[int, bytes]:
    result = subprocess.run(
        ["curl", "--silent", "--show-error", "--max-time", "8", "--connect-to",
         f"{HOST}:443:127.0.0.1:8444", "--write-out", "\n%{http_code}", f"https://{HOST}{path}"],
        capture_output=True, timeout=12,
    )
    if result.returncode:
        raise RuntimeError("Local HTTPS canary failed: " + result.stderr.decode(errors="replace")[:300])
    body, code = result.stdout.rsplit(b"\n", 1)
    return int(code), body


def publish(revision: str, site: Path, expected_site_hash: str) -> dict:
    if os.geteuid() != 0:
        raise PermissionError("Run this deployment as root")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Use the full committed revision")
    git("merge-base", "--is-ancestor", revision, "refs/remotes/origin/main")
    html = git("show", f"{revision}:{SOURCE}")
    if len(html) > 300_000 or b'lang="ru"' not in html or b"NOTICE PLACE" not in html:
        raise ValueError("Unexpected landing artifact")
    script_hashes(html)
    site = site.resolve(strict=True)
    old_site = site.read_bytes()
    if sha(old_site) != expected_site_hash:
        raise RuntimeError("Vhost changed since inspection; refusing to overwrite concurrent changes")
    new_site = integrate_vhost(old_site.decode()).encode()
    old_snippet = SNIPPET.read_bytes() if SNIPPET.exists() else None
    if old_snippet is not None and not old_snippet.startswith(MARKER.encode()):
        raise RuntimeError("Refusing to overwrite an unowned nginx snippet")
    before = {p: local_get(p)[0] for p in ("/admin/", "/health", "/v1/instructions")}
    if before != {"/admin/": 302, "/health": 401, "/v1/instructions": 200}:
        raise RuntimeError(f"Unexpected pre-deployment route baseline: {before}")
    release = WEBROOT / "releases" / revision
    release.mkdir(mode=0o755, parents=True, exist_ok=True)
    artifact = release / "index.html"
    if artifact.exists() and artifact.read_bytes() != html:
        raise RuntimeError("Immutable release content mismatch")
    if not artifact.exists():
        atomic_write(artifact, html)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    BACKUPS.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup = BACKUPS / f"{stamp}-{revision[:12]}"
    backup.mkdir(mode=0o700)
    shutil.copy2(site, backup / "vhost.before")
    if old_snippet is not None:
        shutil.copy2(SNIPPET, backup / "snippet.before")
    metadata = {"revision": revision, "html_sha256": sha(html), "site": str(site),
                "previous_site_sha256": sha(old_site), "snippet_preexisted": old_snippet is not None,
                "backup": str(backup), "release": str(release), "protected_routes_before": before}
    atomic_write(backup / "manifest.json", json.dumps(metadata, indent=2).encode(), 0o600)
    if site.read_bytes() != old_site or (SNIPPET.read_bytes() if SNIPPET.exists() else None) != old_snippet:
        raise RuntimeError("Nginx files changed during preparation; nothing applied")
    try:
        atomic_write(SNIPPET, nginx_snippet(html, release))
        atomic_write(site, new_site)
        run("nginx", "-t")
        run("systemctl", "reload", "nginx")
        last_status = None
        last_hash = None
        for attempt in range(60):
            status, actual = local_get("/")
            last_status = status
            last_hash = sha(actual)
            if status == 200 and last_hash == sha(html):
                break
            time.sleep(0.5)
        else:
            raise RuntimeError(
                f"Served root does not match the committed landing "
                f"after 30s (status={last_status}, sha256={last_hash})"
            )
        after = {p: local_get(p)[0] for p in before}
        if after != before:
            raise RuntimeError(f"Protected route regression: {after}")
    except Exception:
        atomic_write(site, old_site)
        if old_snippet is None:
            SNIPPET.unlink(missing_ok=True)
        else:
            atomic_write(SNIPPET, old_snippet)
        run("nginx", "-t")
        run("systemctl", "reload", "nginx")
        raise
    metadata["protected_routes_after"] = after
    metadata["deployed_at"] = datetime.now(timezone.utc).isoformat()
    atomic_write(release / "manifest.json", json.dumps(metadata, indent=2).encode())
    temporary = WEBROOT / f".current-{os.getpid()}"
    temporary.symlink_to(release)
    os.replace(temporary, WEBROOT / "current")
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--site", type=Path, default=Path("/etc/nginx/sites-enabled/notify.bezrabotnyi.com"))
    parser.add_argument("--expected-site-sha256", required=True)
    args = parser.parse_args()
    with open("/run/lock/noticeplace-landing.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print(json.dumps(publish(args.revision, args.site, args.expected_site_sha256), indent=2))


if __name__ == "__main__":
    main()
