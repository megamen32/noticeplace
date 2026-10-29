"""Production task-mandate verification against the canonical Kanban card.

The project token authenticates the caller to NoticePlace. It does not prove a
current Board task, so this adapter independently reads the manager-only card
and binds a question to its exact active execution receipt/version.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from typing import Any, Callable

from .core import AuthorizationError, ValidationError


_MAX_RESPONSE_BYTES = 1_000_000
_MAX_MESSAGE_CHARS = 4_000
_ALLOWED_PHASES = frozenset({"running", "waiting"})
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _clean_text(value: Any, *, limit: int, field: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"task mandate {field} is invalid")
    result = value.strip()
    if not result or len(result) > limit or any(ord(ch) < 32 and ch not in "\n\t" for ch in result):
        raise ValidationError(f"task mandate {field} is invalid")
    return result


def _latest_accepted_direct_input(receipt: Mapping[str, Any]) -> tuple[str, str, str] | None:
    """Return the exact latest accepted direct input, never an older attempt."""
    controls = receipt.get("sessionControls")
    direct = controls.get("direct") if isinstance(controls, Mapping) else None
    if not isinstance(direct, list) or not direct:
        return None
    latest = direct[-1]
    if not isinstance(latest, Mapping) or latest.get("phase") != "accepted":
        return None
    request_hash = latest.get("requestHash")
    input_id = latest.get("nativeInputMessageId")
    job_id = latest.get("nativeDispatchJobId")
    if (not isinstance(request_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", request_hash)
            or not isinstance(input_id, str) or not input_id.strip() or len(input_id) > 512
            or any(ord(ch) < 32 for ch in input_id)
            or not isinstance(job_id, str) or not job_id.strip() or len(job_id) > 512
            or any(ord(ch) < 32 for ch in job_id)):
        return None
    return request_hash, input_id, job_id


def _canonical_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _board_task_decision_hashes(
    *, task_ref: str, operation_id: str, project: str, recipient: str, purpose: str,
    version: int, message: str, choices: list[dict[str, str]], timeout_value: str,
    executor_question: Mapping[str, Any],
) -> frozenset[str]:
    """Rebuild Board's semantic and insertion-order legacy question hashes."""
    semantic_question: dict[str, Any] = {
        key: executor_question[key] for key in (
            "humanRequestId", "sourceMessageId", "sourceMessageHash",
            "sourceInputMessageId", "sourceJobId", "text", "choices", "timeoutValue",
        )
    }
    semantic_question["contextRef"] = executor_question["contextRef"]
    if "registrationRef" in executor_question:
        semantic_question["registrationRef"] = dict(executor_question["registrationRef"])
    else:
        semantic_question["registrationMessageId"] = executor_question["registrationMessageId"]
        semantic_question["registrationCallId"] = executor_question["registrationCallId"]
    semantic_question["registrationTarget"] = dict(executor_question["registrationTarget"])
    semantic_question["questionHash"] = executor_question["questionHash"]
    material = {
        "taskRef": task_ref, "operationId": operation_id, "project": project,
        "recipient": recipient, "purpose": purpose, "version": version,
        "message": message, "choices": choices, "timeoutValue": timeout_value,
        "executorQuestion": semantic_question,
    }
    # Board's older constructor serialized this nested proof in a fixed field
    # order. Recreate that supported contract from the already validated proof.
    legacy_question: dict[str, Any] = {
        key: executor_question[key] for key in (
            "humanRequestId", "sourceMessageId", "sourceMessageHash",
            "sourceInputMessageId", "sourceJobId", "text", "choices", "timeoutValue",
        )
    }
    legacy_question["contextRef"] = executor_question["contextRef"]
    if "registrationRef" in executor_question:
        registration = executor_question["registrationRef"]
        if not isinstance(registration, Mapping):
            return frozenset()
        legacy_question["registrationRef"] = {
            "kind": registration["kind"],
            "requestId": registration["requestId"],
            "contextRef": registration["contextRef"],
        }
    else:
        legacy_question["registrationMessageId"] = executor_question["registrationMessageId"]
        legacy_question["registrationCallId"] = executor_question["registrationCallId"]
    legacy_question["registrationTarget"] = {
        "harness": executor_question["registrationTarget"]["harness"],
        "sessionId": executor_question["registrationTarget"]["sessionId"],
        "cwd": executor_question["registrationTarget"]["cwd"],
    }
    legacy_question["questionHash"] = executor_question["questionHash"]
    legacy_material = {**material, "executorQuestion": legacy_question}
    legacy = json.dumps(legacy_material, ensure_ascii=False, separators=(",", ":"))
    semantic = _canonical_digest(material)
    return frozenset((semantic, hashlib.sha256(legacy.encode("utf-8")).hexdigest()))


class BoardTaskDecisionVerifier:
    """Verify task questions against current Board ownership and execution.

    The caller supplies question wording and choices, but cannot supply the
    task's project, allowed answer actors, continuation target, or execution
    receipt. Those values are taken from this process's existing operator
    configuration and the current protected Board response.
    """

    def __init__(
        self,
        board_url: str,
        manager_token: str,
        allowed_actors: list[str],
        *,
        operator_recipient: str,
        opener: Callable[..., Any] = urllib.request.urlopen,
        timeout_seconds: float = 3.0,
    ) -> None:
        parsed = urllib.parse.urlsplit(str(board_url).strip())
        host = (parsed.hostname or "").lower()
        if (parsed.scheme != "https" and host not in {"127.0.0.1", "::1", "localhost"}) or not parsed.netloc:
            raise ValueError("Board task verifier requires HTTPS or loopback")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Board task verifier URL must not contain credentials or query data")
        actors = sorted({str(actor).strip() for actor in allowed_actors if str(actor).strip()})
        recipient = str(operator_recipient).strip()
        if not str(manager_token).strip() or not actors or not recipient or len(recipient) > 256:
            raise ValueError("Board task verifier requires protected access, recipient, and actor allowlist")
        self._board_root = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))
        self._manager_token = str(manager_token).strip()
        self._allowed_actors = actors
        self._recipient = recipient
        self._opener = opener
        self._timeout = max(0.2, min(10.0, float(timeout_seconds)))

    @classmethod
    def from_environment(cls) -> "BoardTaskDecisionVerifier | None":
        """Use only existing Board and Notice operator configuration; absent refs deny."""
        board_url = os.environ.get("EXMANAGER_KANBAN_URL", "").strip()
        manager_token = os.environ.get("EXMANAGER_KANBAN_TOKEN", "").strip()
        operator_recipient = os.environ.get("NOTIFY_CENTER_RECIPIENT", "").strip()
        raw_actors = ""
        for name in ("ASK_HUMAN_ALLOWED_ACTORS", "NOTIFY_TELEGRAM_ALLOWED_USER_IDS", "TELEGRAM_CALLBACK_ALLOWED_USER_IDS"):
            candidate = os.environ.get(name, "").strip()
            if candidate:
                raw_actors = candidate
                break
        actors: list[str] = []
        if "*" not in raw_actors:
            for part in raw_actors.replace(";", ",").split(","):
                actor = part.strip()
                if actor:
                    actors.append(actor if ":" in actor else f"telegram:{actor}")
        actors = sorted(set(actors))
        if not board_url or not manager_token or not operator_recipient or not actors:
            return None
        try:
            return cls(board_url, manager_token, actors, operator_recipient=operator_recipient)
        except (TypeError, ValueError):
            return None

    def _current_execution(
        self, task_ref: str, operation_id: str, version: str | None = None,
        expected_continuation: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
        if not task_ref or len(task_ref) > 256 or any(ord(ch) < 32 for ch in task_ref):
            return None
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", operation_id):
            return None
        path = "/api/kanban/cards/" + urllib.parse.quote(task_ref, safe="")
        request = urllib.request.Request(
            self._board_root + path,
            headers={"Accept": "application/json", "x-kanban-manager-token": self._manager_token},
            method="GET",
        )
        try:
            response = self._opener(request, timeout=self._timeout)
            with response:
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                return None
            payload = json.loads(raw.decode("utf-8"))
        except (OSError, TimeoutError, urllib.error.URLError, UnicodeError, ValueError, TypeError):
            return None
        card = payload.get("card") if isinstance(payload, Mapping) else None
        if not isinstance(card, Mapping) or str(card.get("id") or "") != task_ref:
            return None
        current_version = card.get("version")
        if type(current_version) is not int or current_version < 0:
            return None
        if version is not None and str(current_version) != version:
            return None
        project = card.get("project")
        if not isinstance(project, str) or not project.strip():
            return None
        if card.get("needsReview") is True or str(card.get("column") or "") in {"review", "done", "archived", "blocked"}:
            return None
        history = card.get("taskExecutionHistory")
        if not isinstance(history, list) or not history or not isinstance(history[-1], Mapping):
            return None
        receipt = history[-1]
        if (receipt.get("operationId") != operation_id
                or type(receipt.get("lastBoardVersion")) is not int
                or receipt.get("lastBoardVersion") != current_version
                or receipt.get("phase") not in _ALLOWED_PHASES):
            return None
        started_by = receipt.get("startedBy")
        owner = card.get("owner") or "nikita"
        assignees = card.get("assignees") if isinstance(card.get("assignees"), list) else []
        valid_owners = {str(owner)}
        if isinstance(card.get("assignee"), str):
            valid_owners.add(card["assignee"])
        valid_owners.update(value for value in assignees if isinstance(value, str))
        if not isinstance(started_by, str) or started_by not in valid_owners:
            return None
        host_id = receipt.get("hostId")
        session_id = receipt.get("nativeSessionId")
        harness = receipt.get("harness")
        if (harness not in {"codex", "opencode"} or not isinstance(host_id, str) or not host_id.strip()
                or len(host_id) > 256 or not isinstance(session_id, str) or not session_id.strip()
                or len(session_id) > 512):
            return None
        continuation = {
            "task_ref": task_ref,
            "operation_id": operation_id,
            "board_version": current_version,
            "started_by": started_by,
            "host_id": host_id,
            "harness": str(harness),
            "session_id": session_id,
        }
        if harness == "codex":
            turn_id = receipt.get("nativeTurnId")
            if not isinstance(turn_id, str) or not turn_id.strip() or len(turn_id) > 512:
                return None
            continuation["turn_id"] = turn_id
        else:
            input_id = receipt.get("nativeInputMessageId")
            job_id = receipt.get("nativeDispatchJobId")
            direct_input = _latest_accepted_direct_input(receipt)
            if direct_input is not None:
                direct_request_hash, input_id, job_id = direct_input
                continuation["direct_request_hash"] = direct_request_hash
            binding = receipt.get("projectBinding")
            project_id = binding.get("projectId") if isinstance(binding, Mapping) else None
            binding_host = binding.get("hostId") if isinstance(binding, Mapping) else None
            binding_harness = binding.get("harness") if isinstance(binding, Mapping) else None
            cwd = binding.get("cwd") if isinstance(binding, Mapping) else None
            if (not isinstance(input_id, str) or not input_id.strip() or len(input_id) > 512
                    or not isinstance(job_id, str) or not job_id.strip() or len(job_id) > 512
                    or not isinstance(project_id, str) or not project_id.strip() or len(project_id) > 256
                    or binding_host != host_id or binding_harness != harness
                    or not isinstance(cwd, str) or not os.path.isabs(cwd)
                    or len(cwd) > 1024):
                return None
            continuation.update({
                "input_id": input_id,
                "job_id": job_id,
                "project_id": project_id,
                "cwd": os.path.normpath(cwd),
            })
            question = receipt.get("executorQuestion")
            if not isinstance(question, Mapping):
                return None
            source_message_id = question.get("sourceMessageId")
            source_message_hash = question.get("sourceMessageHash")
            source_input_id = question.get("sourceInputMessageId")
            source_job_id = question.get("sourceJobId")
            human_request_id = question.get("humanRequestId")
            context_ref = question.get("contextRef")
            registration_message_id = question.get("registrationMessageId")
            registration_call_id = question.get("registrationCallId")
            registration_ref = question.get("registrationRef")
            registration_target = question.get("registrationTarget")
            has_legacy_registration = "registrationMessageId" in question or "registrationCallId" in question
            has_registry_registration = "registrationRef" in question
            question_hash = question.get("questionHash")
            try:
                question_text = _clean_text(question.get("text"), limit=_MAX_MESSAGE_CHARS, field="executor question")
            except ValidationError:
                return None
            raw_question_choices = question.get("choices")
            if (not isinstance(source_message_id, str) or not source_message_id.strip() or len(source_message_id) > 512
                    or not isinstance(source_message_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", source_message_hash)
                    or source_input_id != input_id or source_job_id != job_id
                    or not isinstance(human_request_id, str) or not _UUID_RE.fullmatch(human_request_id)
                    or context_ref != f"exmanager-task:{task_ref}:{operation_id}"
                    or not isinstance(registration_target, Mapping)
                    or dict(registration_target) != {
                        "harness": harness,
                        "sessionId": session_id,
                        "cwd": continuation["cwd"],
                    }
                    or not isinstance(question_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", question_hash)
                    or not isinstance(raw_question_choices, list) or not 2 <= len(raw_question_choices) <= 4):
                return None
            if has_legacy_registration == has_registry_registration:
                return None
            if has_legacy_registration:
                if ("registrationMessageId" not in question or "registrationCallId" not in question
                        or not isinstance(registration_message_id, str) or not registration_message_id.strip()
                        or len(registration_message_id) > 512
                        or not isinstance(registration_call_id, str) or not registration_call_id.strip()
                        or len(registration_call_id) > 512):
                    return None
            else:
                if (not isinstance(registration_ref, Mapping)
                        or set(registration_ref) != {"kind", "requestId", "contextRef"}
                        or registration_ref.get("kind") != "herder-registry"
                        or registration_ref.get("requestId") != human_request_id
                        or registration_ref.get("contextRef") != context_ref):
                    return None
            question_choices: list[dict[str, str]] = []
            for raw_choice in raw_question_choices:
                if not isinstance(raw_choice, Mapping) or set(raw_choice) != {"value", "label"}:
                    return None
                try:
                    question_choices.append({
                        "value": _clean_text(raw_choice.get("value"), limit=128, field="executor choice value"),
                        "label": _clean_text(raw_choice.get("label"), limit=256, field="executor choice label"),
                    })
                except ValidationError:
                    return None
            if len({choice["value"] for choice in question_choices}) != len(question_choices):
                return None
            timeout_value = question.get("timeoutValue")
            if timeout_value not in {choice["value"] for choice in question_choices}:
                return None
            hash_fields = {
                "humanRequestId": human_request_id,
                "sourceMessageId": source_message_id,
                "sourceMessageHash": source_message_hash,
                "sourceInputMessageId": source_input_id,
                "sourceJobId": source_job_id,
                "text": question_text,
                "choices": question_choices,
                "timeoutValue": timeout_value,
            }
            expected_question_hash = hashlib.sha256(
                json.dumps(hash_fields, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            if question_hash != expected_question_hash:
                return None
            executor_question = {
                "source_message_id": source_message_id,
                "source_message_hash": source_message_hash,
                "source_input_id": source_input_id,
                "source_job_id": source_job_id,
                "human_request_id": human_request_id,
                "question_hash": question_hash,
                "context_ref": context_ref,
            }
            if has_legacy_registration:
                executor_question.update({
                    "registration_message_id": registration_message_id,
                    "registration_call_id": registration_call_id,
                })
            else:
                executor_question["registration_ref"] = dict(registration_ref)
            continuation["executor_question"] = executor_question
            continuation["human_request_id"] = human_request_id
            continuation["question_text"] = question_text
            continuation["question_choices"] = question_choices
            continuation["question_timeout_value"] = timeout_value
        if expected_continuation is not None:
            immutable_expected = {key: value for key, value in dict(expected_continuation).items()
                                  if key != "board_version"}
            immutable_current = {key: value for key, value in continuation.items() if key != "board_version"}
            if immutable_expected != immutable_current:
                return None
        return dict(card), dict(receipt), continuation

    def _task_reference_matches(
        self, context: Mapping[str, Any], card: Mapping[str, Any], receipt: Mapping[str, Any],
        target: Mapping[str, Any],
    ) -> bool:
        reference_id = str(context.get("task_decision_ref") or "")
        if not reference_id:
            return True  # Legacy Codex task decisions predate Board references.
        current = receipt.get("taskDecisionRequest")
        question = receipt.get("executorQuestion")
        continuation = context.get("continuation")
        actors = context.get("allowed_actors")
        if (not isinstance(current, Mapping) or not isinstance(question, Mapping)
                or not isinstance(continuation, Mapping) or not isinstance(actors, list)
                or not all(isinstance(actor, str) and actor.strip() for actor in actors)
                or actors != sorted(set(actors))):
            return False
        purpose = str(context.get("purpose") or "")
        version = str(context.get("version") or "")
        try:
            numeric_version = int(version)
        except (TypeError, ValueError):
            return False
        if str(numeric_version) != version or numeric_version < 0:
            return False
        identity = [str(card.get("id") or ""), purpose, version]
        expected_ref = "td-" + hashlib.sha256(
            json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:40]
        question_choices = target.get("question_choices")
        message = target.get("question_text")
        timeout_value = target.get("question_timeout_value")
        original_continuation = dict(continuation)
        if (not isinstance(question_choices, list) or not isinstance(message, str)
                or not isinstance(timeout_value, str) or expected_ref != reference_id
                or not purpose or not isinstance(current.get("requestHash"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", current["requestHash"])
                or current.get("requestId") != reference_id or current.get("purpose") != purpose
                or type(current.get("version")) is not int or current.get("version") != numeric_version
                or current.get("requestHash") not in _board_task_decision_hashes(
                    task_ref=str(card.get("id") or ""),
                    operation_id=str(receipt.get("operationId") or ""),
                    project=str(card.get("project") or ""), recipient=self._recipient,
                    purpose=purpose, version=numeric_version, message=message,
                    choices=question_choices, timeout_value=timeout_value,
                    executor_question=question,
                )):
            return False
        canonical = {
            "task_ref": identity[0], "purpose": purpose, "version": version,
            "mandate_ref": str(receipt.get("operationId") or ""),
            "project": str(card.get("project") or ""), "recipient": self._recipient,
            "message": message, "allowed_actors": actors, "choices": question_choices,
            "timeout_value": timeout_value, "continuation": original_continuation,
        }
        calculated = _canonical_digest(canonical)
        return (
            isinstance(context.get("request_hash"), str)
            and re.fullmatch(r"[0-9a-f]{64}", context["request_hash"]) is not None
            and context["request_hash"] == calculated
        )

    def verify(self, mandate: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(mandate, Mapping):
            raise AuthorizationError("verified task mandate unavailable")
        try:
            task_ref = _clean_text(mandate.get("task_ref"), limit=256, field="task_ref")
            purpose = _clean_text(mandate.get("purpose"), limit=128, field="purpose")
            version = _clean_text(str(mandate.get("version") or ""), limit=128, field="version")
            operation_id = _clean_text(mandate.get("mandate_ref"), limit=128, field="mandate_ref")
            supplied_recipient = _clean_text(mandate.get("recipient"), limit=256, field="recipient")
            supplied_message = _clean_text(mandate.get("message"), limit=_MAX_MESSAGE_CHARS, field="message")
            supplied_timeout_value = _clean_text(mandate.get("timeout_value"), limit=128, field="timeout_value")
        except ValidationError:
            raise
        except Exception as error:
            raise AuthorizationError("verified task mandate unavailable") from error
        if supplied_recipient != self._recipient:
            raise AuthorizationError("task recipient does not match the configured operator")
        current = self._current_execution(task_ref, operation_id, version)
        if current is None:
            raise AuthorizationError("current Board task execution is not authorized")
        card, receipt, continuation = current
        if continuation.get("harness") == "opencode":
            message = str(continuation["question_text"])
            raw_choices = continuation["question_choices"]
            timeout_value = str(continuation["question_timeout_value"])
        else:
            message = supplied_message
            raw_choices = mandate.get("choices")
            timeout_value = supplied_timeout_value
        supplied_project = mandate.get("project")
        if not isinstance(supplied_project, str) or supplied_project.strip() != str(card["project"]).strip():
            raise AuthorizationError("task project no longer matches the current Board card")
        if not isinstance(raw_choices, list) or not 2 <= len(raw_choices) <= 4:
            raise ValidationError("task decision requires two to four allowed choices")
        choices: list[dict[str, str]] = []
        for raw in raw_choices:
            if not isinstance(raw, Mapping):
                raise ValidationError("task decision choices must be objects")
            choices.append({
                "value": _clean_text(raw.get("value"), limit=128, field="choice value"),
                "label": _clean_text(raw.get("label"), limit=256, field="choice label"),
            })
        if len({choice["value"] for choice in choices}) != len(choices) or timeout_value not in {choice["value"] for choice in choices}:
            raise ValidationError("task timeout continuation must be one allowed choice")
        return {
            "task_ref": str(card["id"]),
            "purpose": purpose,
            "version": str(card["version"]),
            "mandate_ref": str(receipt["operationId"]),
            "project": str(card["project"]).strip(),
            "recipient": self._recipient,
            "message": message,
            "allowed_actors": list(self._allowed_actors),
            "choices": choices,
            "timeout_value": timeout_value,
            "continuation": continuation,
        }

    def is_actor_allowed(self, context: Mapping[str, Any], actor: str) -> bool:
        if not isinstance(context, Mapping) or actor not in self._allowed_actors:
            return False
        continuation = context.get("continuation")
        if not isinstance(continuation, Mapping):
            return False
        current = self._current_execution(
            str(context.get("task_ref") or ""), str(context.get("mandate_ref") or ""),
            expected_continuation=continuation,
        )
        return bool(current and self._task_reference_matches(context, current[0], current[1], current[2]))

    def verify_board_answer(
        self, context: Mapping[str, Any], task_decision_ref: str, proposal_ref: str,
    ) -> dict[str, Any] | None:
        """Read the exact current source-bound answer proposal from protected Board CAS."""
        if not isinstance(context, Mapping) or context.get("task_decision_ref") != task_decision_ref:
            return None
        continuation = context.get("continuation")
        if not isinstance(continuation, Mapping) or continuation.get("harness") != "opencode":
            return None
        current = self._current_execution(
            str(context.get("task_ref") or ""), str(context.get("mandate_ref") or ""),
            expected_continuation=continuation,
        )
        if current is None:
            return None
        card, receipt, current_target = current
        question = current_target.get("executor_question")
        decision_ref = receipt.get("taskDecisionRequest")
        proposal = receipt.get("taskDecisionProposal")
        if not isinstance(question, Mapping) or not isinstance(decision_ref, Mapping) or not isinstance(proposal, Mapping):
            return None
        expected_purpose = str(context.get("purpose") or "")
        expected_version = str(context.get("version") or "")
        if (not self._task_reference_matches(context, card, receipt, current_target)
                or decision_ref.get("requestId") != task_decision_ref
                or str(decision_ref.get("purpose") or "") != expected_purpose
                or str(decision_ref.get("version") or "") != expected_version):
            return None
        board_request_hash = str(decision_ref.get("requestHash") or "")
        if (proposal.get("proposalRef") != proposal_ref
                or proposal.get("requestId") != task_decision_ref
                or str(proposal.get("requestHash") or "") != board_request_hash
                or str(proposal.get("purpose") or "") != expected_purpose
                or str(proposal.get("version") or "") != expected_version
                or proposal.get("humanRequestId") != continuation.get("human_request_id")
                or proposal.get("sourceMessageId") != question.get("source_message_id")
                or proposal.get("sourceJobId") != question.get("source_job_id")
                or type(proposal.get("cardVersion")) is not int
                or proposal.get("cardVersion") != card.get("version")):
            return None
        if type(receipt.get("lastBoardVersion")) is not int or receipt.get("lastBoardVersion") != card.get("version"):
            return None
        try:
            actor = _clean_text(proposal.get("authenticatedActor"), limit=128, field="Board answer actor")
            value = _clean_text(proposal.get("value"), limit=128, field="Board answer value")
            stop_epoch = _clean_text(proposal.get("stopEpoch"), limit=256, field="Board answer stop epoch")
        except ValidationError:
            return None
        controls = receipt.get("sessionControls")
        direct = controls.get("direct") if isinstance(controls, Mapping) else None
        if isinstance(direct, list) and direct:
            latest_direct = direct[-1]
            if not isinstance(latest_direct, Mapping) or latest_direct.get("phase") != "accepted":
                return None
            direct_input = _latest_accepted_direct_input(receipt)
            if direct_input is None or continuation.get("direct_request_hash") != direct_input[0]:
                return None
            expected_stop_epoch = direct_input[0]
        else:
            expected_stop_epoch = str(receipt.get("messageInputId") or "")
        if not expected_stop_epoch or stop_epoch != expected_stop_epoch:
            return None
        if isinstance(controls, Mapping):
            stops = controls.get("stops")
            latest_stop = stops[-1] if isinstance(stops, list) and stops else controls.get("stop")
            if isinstance(latest_stop, Mapping) and latest_stop.get("phase") in {"intent", "unknown"}:
                return None
        owner = card.get("owner") or "nikita"
        valid_owners = {str(owner)}
        if isinstance(card.get("assignee"), str):
            valid_owners.add(str(card["assignee"]))
        assignees = card.get("assignees") if isinstance(card.get("assignees"), list) else []
        valid_owners.update(value for value in assignees if isinstance(value, str))
        if actor not in valid_owners:
            return None
        return {
            "request_id": task_decision_ref,
            "proposal_ref": proposal_ref,
            "actor": actor,
            "value": value,
            "card_version": int(card["version"]),
            "stop_epoch": stop_epoch,
            "source_message_id": str(question["source_message_id"]),
            "source_job_id": str(question["source_job_id"]),
            "human_request_id": str(continuation["human_request_id"]),
            "question_hash": str(question["question_hash"]),
        }

    def timeout_allowed_now(self, context: Mapping[str, Any]) -> bool:
        if not isinstance(context, Mapping):
            return False
        continuation = context.get("continuation")
        if not isinstance(continuation, Mapping):
            return False
        current = self._current_execution(
            str(context.get("task_ref") or ""), str(context.get("mandate_ref") or ""),
            expected_continuation=continuation,
        )
        return bool(current and self._task_reference_matches(context, current[0], current[1], current[2]))
