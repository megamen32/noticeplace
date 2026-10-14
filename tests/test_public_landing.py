"""Small offline tests for the approved landing and isolated nginx deployment.

No real incidents, agents, notifications, root writes, or external requests.
One process, <1 GiB address-space limit in the recorded validation command.
"""
import base64
import hashlib
import importlib.util
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web" / "landing"
spec = importlib.util.spec_from_file_location("public_landing_deploy", ROOT / "deploy" / "landing.py")
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)

TLS = """upstream noticeplace_mesh_api { server 127.0.0.1:11492; }
server {
    listen 127.0.0.1:8444 ssl;
    server_name notify.bezrabotnyi.com;
    location ^~ /admin/ { auth_request /check; proxy_pass https://noticeplace_mesh_api; }
    location / { proxy_pass https://noticeplace_mesh_api; }
}
server {
    listen 80;
    server_name notify.bezrabotnyi.com;
    location / { return 301 https://$host$request_uri; }
}
"""


def test_build_reproduces_approved_artifact(tmp_path):
    src = tmp_path / "landing"
    shutil.copytree(WEB, src)
    result = subprocess.run([sys.executable, str(src / "build.py")], capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr.decode()
    assert (src / "dist" / "NoticePlace.html").read_bytes() == (WEB / "dist" / "NoticePlace.html").read_bytes()
    assert (src / "index.html").read_bytes() == (WEB / "index.html").read_bytes()


def test_preserves_all_existing_routes_and_http_redirect():
    result = deploy.integrate_vhost(TLS)
    include = f"    include {deploy.SNIPPET};"
    assert result.count(include) == 1
    assert result.replace("\n\n" + include, "") == TLS
    assert "include" not in result.split("listen 80;")[1]


def test_include_is_idempotent():
    once = deploy.integrate_vhost(TLS)
    assert deploy.integrate_vhost(once) == once


def test_rejects_existing_exact_root():
    with pytest.raises(ValueError, match="existing exact root"):
        deploy.integrate_vhost(TLS.replace("location / { proxy_pass", "location = / { proxy_pass"))


def test_rejects_ambiguous_tls_vhost():
    with pytest.raises(ValueError, match="exactly one"):
        deploy.integrate_vhost(TLS + TLS)


def test_rejects_wrong_hostname():
    with pytest.raises(ValueError, match="exactly one"):
        deploy.integrate_vhost(TLS.replace("notify.bezrabotnyi.com", "other.example"))


def test_script_hash_is_exact_and_csp_is_root_only():
    html = (WEB / "dist" / "NoticePlace.html").read_bytes()
    script = re.findall(rb"<script>(.*?)</script>", html, re.S)
    assert len(script) == 1
    expected = base64.b64encode(hashlib.sha256(script[0]).digest()).decode()
    snippet = deploy.nginx_snippet(html, Path("/var/www/noticeplace-landing/releases/" + "a" * 40)).decode()
    assert "script-src 'sha256-" + expected + "'" in snippet
    assert "script-src 'unsafe-inline'" not in snippet
    assert "connect-src 'none'" in snippet
    assert snippet.count("location") == 1
    assert "location = / {" in snippet
    assert "auth_request" not in snippet and "proxy_pass" not in snippet
    assert "Strict-Transport-Security" in snippet
    assert "frame-ancestors 'none'" in snippet


@pytest.mark.parametrize("payload", [b"<html></html>", b"<script>a</script><script>b</script>"])
def test_refuses_unexpected_scripts(payload):
    with pytest.raises(ValueError):
        deploy.script_hashes(payload)


def test_refuses_unsafe_nginx_path():
    with pytest.raises(ValueError):
        deploy.nginx_snippet(b"<script>x</script>", Path("/tmp/unsafe;path"))


def test_demo_stays_offline_and_installation_link_preserved():
    html = (WEB / "index.html").read_text()
    js = (WEB / "app.js").read_text()
    assert 'https://github.com/megamen32/noticeplace/blob/main/deploy/README.md' in html
    assert 'Открыть консоль' not in html
    assert 'Данные вымышлены. Реальных запросов к серверам нет.' in html
    assert 'data-plan="repair"' in html
    assert not re.search(r"\b(fetch|XMLHttpRequest|WebSocket|EventSource)\s*\(", js)
    assert 'prefers-reduced-motion' in js


@pytest.mark.parametrize("source", ["index.html", "dist/NoticePlace.html"])
def test_ecosystem_product_links_match_owner_urls(source):
    from html.parser import HTMLParser

    class ProductLinks(HTMLParser):
        def __init__(self):
            super().__init__()
            self.links = []
            self.current = None

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "a" and "ecosystem-link" in attrs.get("class", "").split():
                self.current = {"attrs": attrs, "text": ""}

        def handle_data(self, data):
            if self.current is not None:
                self.current["text"] += data

        def handle_endtag(self, tag):
            if tag == "a" and self.current is not None:
                self.links.append(self.current)
                self.current = None

    parser = ProductLinks()
    parser.feed((WEB / source).read_text())
    expected = {
        "ШТАТНО": "https://shtatno.bezrabotnyi.com",
        "GPTAdmin": "https://became.bezrabotnyi.com",
        "Agent Herder": "https://agentherder.bezrabotnyi.com",
        "UserIO": "https://msg.bezrabotnyi.com",
    }
    assert len(parser.links) == len(expected)
    assert {link["text"].strip(): link["attrs"]["href"] for link in parser.links} == expected
    for link in parser.links:
        assert link["attrs"]["target"] == "_blank"
        assert {"noopener", "noreferrer"} <= set(link["attrs"]["rel"].split())


@pytest.mark.parametrize("source", ["index.html", "dist/NoticePlace.html"])
def test_shtatno_affiliation_is_in_ecosystem_heading(source):
    html = (WEB / source).read_text()
    section = re.search(r'<section\b[^>]*\bid="ecosystem"[^>]*>(.*?)</section>', html, re.S)
    assert section is not None
    heading = re.search(r'<h2\b[^>]*\bid="ecosystem-title"[^>]*>(.*?)</h2>', section.group(1), re.S)
    assert heading is not None
    assert re.sub(r'<[^>]+>', '', heading.group(1)).strip() == "Notice Place — часть ШТАТНО"
    assert 'href="https://shtatno.bezrabotnyi.com"' in heading.group(1)
    assert section.group(1).index('id="ecosystem-title"') < section.group(1).index('class="ecosystem-map"')


def test_atomic_write_preserves_mode(tmp_path):
    p = tmp_path / "config"
    p.write_bytes(b"before")
    p.chmod(0o640)
    deploy.atomic_write(p, b"after")
    assert p.read_bytes() == b"after"
    assert p.stat().st_mode & 0o777 == 0o640


@pytest.mark.skipif(shutil.which("nginx") is None, reason="nginx not installed")
def test_real_nginx_root_and_query_serve_html_without_affecting_other_paths(tmp_path):
    """One finite, rootless nginx process group on an ephemeral loopback port."""
    import os
    import signal
    import socket
    import time
    import urllib.error
    import urllib.request

    html = (WEB / "dist" / "NoticePlace.html").read_bytes()
    release = tmp_path / "release"
    release.mkdir()
    (release / "index.html").write_bytes(html)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    snippet = deploy.nginx_snippet(html, release).decode()
    config = tmp_path / "nginx.conf"
    temporary_paths = "\n".join(f"{kind}_temp_path {tmp_path}/{kind};" for kind in
                                ("client_body", "proxy", "fastcgi", "uwsgi", "scgi"))
    config.write_text(
        f"daemon off; worker_processes 1; pid {tmp_path}/nginx.pid; error_log stderr;\n"
        "events { worker_connections 32; }\n"
        "http { access_log off;\n" + temporary_paths + "\n"
        f"server {{ listen 127.0.0.1:{port};\n" + snippet +
        "location / { return 404; }\n} }\n"
    )
    proc = subprocess.Popen([shutil.which("nginx"), "-c", str(config), "-p", str(tmp_path)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True)
    try:
        for attempt in range(60):
            if proc.poll() is not None:
                pytest.fail(proc.stderr.read().decode())
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1) as response:
                    assert response.status == 200
                    assert response.read() == html
                    assert "script-src 'sha256-" in response.headers["Content-Security-Policy"]
                break
            except urllib.error.URLError as error:
                if isinstance(error, urllib.error.HTTPError):
                    raise
                time.sleep(0.05)
        else:
            pytest.fail("Rootless nginx did not start")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/?campaign=acceptance", timeout=2) as response:
            assert response.read() == html
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/admin/", timeout=2)
        assert caught.value.code == 404
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
        proc.communicate(timeout=5)
