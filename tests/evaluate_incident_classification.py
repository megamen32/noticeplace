#!/usr/bin/env python3
"""One-shot synthetic evaluation through NoticePlace's existing classifier."""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from notification_center.incident_classifier import (  # noqa: E402
    CLASSES,
    DIGEST,
    MODEL,
    PROMPT,
    _queue_transport,
    classify,
    normalize_decision,
)

FIXTURE = ROOT / "tests/fixtures/incident-classification.json"
DEFAULT_OUTPUT = ROOT / ".agents/tasks/20261107-noticeplace-quiet-operations/evidence/gemma-classification-eval.json"
ALLOWED_KEYS = {"class", "confidence", "reason"}


def event_for(case: dict) -> dict:
    families = case["source_families"]
    health = {
        "host_id": "synthetic-host",
        "signal_type": "synthetic-health",
        "correlation_id": f"synthetic:{case['id']}",
    }
    if case["context_complete"]:
        health["source_id"] = families[0]
    else:
        # Deliberately missing source identity drives the classifier's own
        # fail-closed path; no model request is made for incomplete context.
        health["source_id"] = ""
    return {
        "incident": {
            "project": "synthetic-noticeplace-evaluation",
            "severity": case["producer_severity"],
            "title": case["id"],
            "body": case["observation"],
        },
        "health": health,
    }


def safe_decision(value: object) -> dict | None:
    if not isinstance(value, dict) or set(value) != ALLOWED_KEYS:
        return None
    confidence = value.get("confidence")
    if (value.get("class") not in CLASSES or type(confidence) not in (int, float)
            or not math.isfinite(confidence) or not 0 <= confidence <= 1
            or not isinstance(value.get("reason"), str)):
        return None
    reason = " ".join(value["reason"].split())[:400]
    return {"class": value["class"], "confidence": confidence, "reason": reason}


def guard_checks(cases: list[dict]) -> dict:
    incomplete = next(c for c in cases if not c["context_complete"])
    complete = next(c for c in cases if c["context_complete"] and c["producer_severity"] != "emergency")
    emergency = next(c for c in cases if c["producer_severity"] == "emergency")
    low = normalize_decision(event_for(complete), {
        "class": "log", "confidence": 0.79, "reason": "Ниже порога доверия",
    })
    missing = normalize_decision(event_for(incomplete), {
        "class": "log", "confidence": 0.99, "reason": "Не должен пройти неполный контекст",
    })
    floor = normalize_decision(event_for(emergency), {
        "class": "log", "confidence": 0.99, "reason": "Не должен понизить emergency",
    })
    checks = {
        "low_confidence_to_repair": low["class"] == "repair" and low["status"] == "low_confidence",
        "incomplete_to_repair": missing["class"] == "repair" and missing["status"] == "insufficient_context",
        "producer_emergency_floor": floor["class"] == "emergency" and floor["status"] == "emergency_floor",
    }
    return {"checks": checks, "passed": all(checks.values())}


def write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def evaluate(output_path: Path) -> int:
    if output_path.exists():
        raise SystemExit(f"Refusing to overwrite prior evaluation receipt: {output_path}")
    fixture = json.loads(FIXTURE.read_text())
    cases = fixture["cases"]
    if not 12 <= len(cases) <= 16:
        raise SystemExit(f"Corpus size must be 12..16, got {len(cases)}")
    expected_families = set(fixture["source_families"])
    used_families = {family for case in cases for family in case["source_families"]}
    if used_families != expected_families:
        raise SystemExit(f"Family coverage mismatch: unused={sorted(expected_families-used_families)} unknown={sorted(used_families-expected_families)}")
    for case in cases:
        if case["expected"]["class"] not in CLASSES or type(case["expected"]["needs_human"]) is not bool:
            raise SystemExit(f"Invalid expected label: {case['id']}")

    floors = guard_checks(cases)
    receipt = {
        "schema_version": 1,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "model": MODEL,
        "expected_digest": DIGEST,
        "prompt_source": "notification_center.incident_classifier.PROMPT",
        "prompt_sha256": __import__("hashlib").sha256(PROMPT.encode()).hexdigest(),
        "decision_schema": {"required": ["class", "confidence", "reason"], "additional_properties": False},
        "parameters_source": "notification_center.incident_classifier.classify",
        "transport": "existing server-44 SSH -> loopback:11437; module digest check",
        "model_output_has_needs_human": False,
        "needs_human_scoring": "fixture policy label only; model has no notification/closure/tool authority",
        "guard_checks": floors,
        "cases": [],
        "stopped_after_unknown": False,
    }
    write_atomic(output_path, receipt)
    if not floors["passed"]:
        raise SystemExit(f"Deterministic floor check failed; local receipt saved: {output_path}")

    stop_inference = False
    for case in cases:
        event = event_for(case)
        transport_receipt: dict = {"attempted": False}

        def capture_default_transport(payload: dict) -> dict:
            if stop_inference:
                raise RuntimeError("evaluation stopped after prior unknown transport outcome")
            transport_receipt["attempted"] = True
            transport_receipt["started_at"] = datetime.now(timezone.utc).isoformat()
            started = time.monotonic()
            try:
                decision = _queue_transport(payload)
            except BaseException as error:
                transport_receipt["elapsed_ms"] = int((time.monotonic() - started) * 1000)
                transport_receipt["status"] = "unknown"
                transport_receipt["error_type"] = type(error).__name__
                raise
            transport_receipt["elapsed_ms"] = int((time.monotonic() - started) * 1000)
            transport_receipt["status"] = "response_received"
            transport_receipt["raw_decision"] = safe_decision(decision)
            if transport_receipt["raw_decision"] is None:
                transport_receipt["raw_decision_invalid"] = True
            return decision

        normalized = classify(event, transport=capture_default_transport)
        if transport_receipt.get("status") == "unknown":
            stop_inference = True
            receipt["stopped_after_unknown"] = True
        elif stop_inference and not transport_receipt["attempted"] and normalized.get("status") == "unavailable":
            transport_receipt["status"] = "not_attempted_after_unknown"

        raw = transport_receipt.get("raw_decision")
        normalized_class = normalized.get("class")
        if normalized_class not in CLASSES:
            raise RuntimeError(f"Classifier returned invalid normalized class for {case['id']}")
        if case["producer_severity"] == "emergency" and normalized_class != "emergency":
            raise RuntimeError(f"Emergency floor violated for {case['id']}")
        if not case["context_complete"] and normalized_class != "repair":
            raise RuntimeError(f"Incomplete context did not fail closed for {case['id']}")
        if raw and raw["confidence"] < fixture["evaluation_policy"]["minimum_confidence"] and normalized_class != "repair":
            raise RuntimeError(f"Low-confidence floor violated for {case['id']}")

        row = {
            "id": case["id"],
            "split": case["split"],
            "source_families": case["source_families"],
            "expected": case["expected"],
            "raw_decision": raw,
            "normalized": {key: normalized.get(key) for key in ("class", "confidence", "reason", "status", "model", "elapsed_ms")},
            "transport_receipt": transport_receipt,
            "class_match": normalized_class == case["expected"]["class"],
            "needs_human_model_score": None,
        }
        receipt["cases"].append(row)
        receipt["updated_at"] = datetime.now(timezone.utc).isoformat()
        write_atomic(output_path, receipt)

    attempted = [r for r in receipt["cases"] if r["transport_receipt"].get("attempted")]
    matched = sum(r["class_match"] for r in receipt["cases"])
    raw_scored = [r for r in receipt["cases"] if r.get("raw_decision") is not None]
    raw_matched = sum(r["raw_decision"]["class"] == r["expected"]["class"] for r in raw_scored)
    per_class = {
        label: {
            "total": sum(r["expected"]["class"] == label for r in receipt["cases"]),
            "normalized_correct": sum(r["expected"]["class"] == label and r["normalized"]["class"] == label for r in receipt["cases"]),
            "raw_scored": sum(r["expected"]["class"] == label and r.get("raw_decision") is not None for r in receipt["cases"]),
        }
        for label in CLASSES
    }
    latencies = [r["transport_receipt"]["elapsed_ms"] for r in attempted if "elapsed_ms" in r["transport_receipt"]]
    receipt["summary"] = {
        "cases_total": len(receipt["cases"]),
        "model_requests_attempted": len(attempted),
        "model_requests_unknown": sum(r["transport_receipt"].get("status") == "unknown" for r in receipt["cases"]),
        "class_matches_normalized": matched,
        "class_accuracy_normalized": matched / len(receipt["cases"]),
        "class_accuracy_raw_model": raw_matched / len(raw_scored) if raw_scored else None,
        "raw_model_scored_cases": len(raw_scored),
        "per_class": per_class,
        "missed_emergency_normalized": sum(r["expected"]["class"] == "emergency" and r["normalized"]["class"] != "emergency" for r in receipt["cases"]),
        "needs_human_matches": None,
        "needs_human_note": "needs_human is intentionally outside the model decision schema",
        "latencies_ms": latencies,
        "latency_max_ms": max(latencies) if latencies else None,
        "heldout_matches": sum(r["class_match"] for r in receipt["cases"] if r["split"] == "holdout"),
        "heldout_total": sum(r["split"] == "holdout" for r in receipt["cases"]),
    }
    receipt["completed_at"] = datetime.now(timezone.utc).isoformat()
    write_atomic(output_path, receipt)
    print(json.dumps(receipt["summary"], ensure_ascii=False, sort_keys=True))
    return 0 if not receipt["stopped_after_unknown"] and matched == len(cases) else 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    return evaluate(args.output)


if __name__ == "__main__":
    raise SystemExit(main())
