"""Exercise the real shell payload builder with a local curl stub; never send events."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run_cli(problem="cpu", observed="1780000000", *, missing_host=False, resolve=False):
    (ROOT / ".tmp").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="typed-cli-", dir=ROOT / ".tmp") as directory:
        fixture = Path(directory)
        stub = fixture / "curl"
        stub.write_text("#!/usr/bin/env python3\nimport os,sys\nfrom pathlib import Path\na=sys.argv\nPath(os.environ['CLI_CAPTURE']).write_text(a[a.index('--data-binary')+1])\n")
        stub.chmod(0o755)
        capture = fixture / "payload.json"
        args = [str(ROOT / "bin/notify-producer"), "--project", "qa", "--dedupe-key", "unit", "--idempotency-key", "unit",
                "--severity", "important", "--title", "Изолированная проверка", "--source-id", "unit-source",
                "--signal-type", "service_failure", "--problem-type", problem, "--correlation-id", "unit-correlation", "--observed-at", observed]
        if not missing_host:
            args += ["--host-id", "unit-host"]
        if resolve:
            args += ["--resolve"]
        env = {**os.environ, "PATH": str(fixture) + os.pathsep + os.environ["PATH"],
               "NOTIFY_CENTER_EVENT_URL": "http://invalid.test/v1/events", "NOTIFY_CENTER_TOKEN": "unit-only",
               "CLI_CAPTURE": str(capture)}
        result = subprocess.run(args, env=env, capture_output=True, text=True)
        return result.returncode, json.loads(capture.read_text()) if capture.exists() else None


@pytest.mark.parametrize("problem", ["cpu", "engine", "read_post"])
@pytest.mark.parametrize("resolve", [False, True])
def test_typed_payload_supports_consumer_problem_types_without_a_request(problem, resolve):
    code, payload = run_cli(problem, resolve=resolve)
    assert code == 0
    assert payload["problem_type"] == problem
    assert payload["event_type"] == ("health.recovered" if resolve else "health.degraded")
    assert payload["observed_at"] == 1780000000.0


@pytest.mark.parametrize("kwargs", [{"missing_host": True}, {"observed": "nan"}, {"observed": "-1"},
                                    {"problem": "a" * 129}, {"problem": "cpu\nforged"}])
def test_invalid_identity_never_reaches_the_transport(kwargs):
    code, payload = run_cli(**kwargs)
    assert code != 0
    assert payload is None
