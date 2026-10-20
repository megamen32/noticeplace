#!/usr/bin/env python3
"""Stage a committed public landing and its managed nginx snippet artifact.

Live nginx configuration is owned by the canonical nginx-dev repository.
This script writes only an immutable release under the landing webroot.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "web/landing/dist/NoticePlace.html"
HOST = "notify.bezrabotnyi.com"
WEBROOT = Path("/var/www/noticeplace-landing")
SNIPPET = Path("/etc/nginx/snippets/noticeplace-landing.conf")
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


def stage_release(revision: str) -> dict:
    """Stage the committed HTML and emit its nginx-dev snippet artifact."""
    if os.geteuid() != 0:
        raise PermissionError("Run release staging as root")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Use the full committed revision")
    git("merge-base", "--is-ancestor", revision, "refs/remotes/origin/main")
    html = git("show", f"{revision}:{SOURCE}")
    if len(html) > 300_000 or b'lang="ru"' not in html or b"NOTICE PLACE" not in html:
        raise ValueError("Unexpected landing artifact")
    script_hashes(html)
    release = WEBROOT / "releases" / revision
    release.mkdir(mode=0o755, parents=True, exist_ok=True)
    artifact = release / "index.html"
    if artifact.exists() and artifact.read_bytes() != html:
        raise RuntimeError("Immutable release content mismatch")
    if not artifact.exists():
        atomic_write(artifact, html)
    snippet = nginx_snippet(html, release)
    snippet_artifact = release / "noticeplace-landing.conf"
    if snippet_artifact.exists() and snippet_artifact.read_bytes() != snippet:
        raise RuntimeError("Immutable release snippet mismatch")
    if not snippet_artifact.exists():
        atomic_write(snippet_artifact, snippet)
    return {
        "revision": revision,
        "html_sha256": sha(html),
        "snippet_sha256": sha(snippet),
        "release": str(release),
        "snippet_artifact": str(snippet_artifact),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--stage-only", action="store_true", required=True,
                        help="Stage files; apply nginx changes through nginx-dev.")
    args = parser.parse_args()
    print(json.dumps(stage_release(args.revision), indent=2))


if __name__ == "__main__":
    main()
