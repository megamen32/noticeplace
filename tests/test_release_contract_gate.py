"""Failure paths must leave an unambiguous release-gate receipt."""

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "scripts/check-release-contract.py"


def run_gate(output):
    return subprocess.run(
        [sys.executable, "-B", str(GATE), "--previous", "HEAD", "--candidate", "HEAD", "--output", str(output)],
        cwd=ROOT, capture_output=True, text=True, timeout=10,
    )


def test_same_source_fails_before_building(tmp_path):
    result = run_gate(tmp_path)
    assert result.returncode == 1, result.stderr
    receipt = json.loads((tmp_path / "receipt.json").read_text())
    assert (receipt["status"], receipt["step"], receipt["releases"], receipt["phases"]) == (
        "fail", "resolve", {}, {},
    )
    assert receipt["error"] == "previous and candidate must be different commits"
    assert list(tmp_path.iterdir()) == [tmp_path / "receipt.json"]


def test_existing_receipt_is_preserved(tmp_path):
    receipt = tmp_path / "receipt.json"
    receipt.write_text('{"status":"pass"}\n')
    result = run_gate(tmp_path)
    assert result.returncode == 2
    assert "--output must be a new or empty directory" in result.stderr
    assert receipt.read_text() == '{"status":"pass"}\n'
