"""Wire and SQLite assertions for the isolated Notice Place release gate."""

import json
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener


PROJECT = "release-contract"
MCP_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
PYTHON_CLIENT = """
import json, sys
from pathlib import Path
installed, operation, argument = sys.argv[1:]
sys.path.insert(0, installed)
import notify_center_client
from notify_center_client import NotificationCenterClient, SourceVerifier
if not Path(notify_center_client.__file__).resolve().is_relative_to(Path(installed)):
    raise RuntimeError("Python SDK was not loaded from the wheel installation")
client = NotificationCenterClient.from_environment(request_timeout=3)
if operation == "emit":
    result = client.emit(**json.loads(argument))
else:
    result = client.wait_for_response(argument, timeout_seconds=0.5, poll_interval_seconds=0.05)
print(json.dumps(result))
"""
NODE_CLIENT = """
import { NotificationCenterClient } from 'notify-mcp/notification-center';
const [operation, argument] = process.argv.slice(1);
const client = NotificationCenterClient.fromEnvironment({ requestTimeoutMs: 3000 });
let result;
if (operation === "emit") {
    const { dedup_key, idempotency_key, ...event } = JSON.parse(argument);
    result = await client.emit({ ...event, dedupKey: dedup_key, idempotencyKey: idempotency_key });
} else {
    result = await client.waitForResponse(argument, { timeoutMs: 500, pollIntervalMs: 50 });
}
console.log(JSON.stringify(result));
"""


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def http(base, method, path, *, status=200, token=None, body=None, key=None):
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if key:
        headers["Idempotency-Key"] = key
    request = Request(base + path, headers=headers, method=method,
                      data=json.dumps(body).encode() if body is not None else None)
    try:
        response = build_opener(ProxyHandler({})).open(request, timeout=3)
    except HTTPError as error:
        response = error
    with response:
        data = response.read()
        require(response.code == status,
                f"{method} {path}: expected HTTP {status}, got {response.code}: {data[:300]!r}")
        return json.loads(data) if data else None


def sdk(kind, package, operation, argument, env):
    if kind == "python":
        command = ["python3", "-I", "-c", PYTHON_CLIENT, str(package / "python")]
    else:
        command = ["node", "--input-type=module", "--eval", NODE_CLIENT]
    output = subprocess.check_output(
        [*command, operation, argument], env=env, cwd=package, text=True, timeout=10,
    )
    return json.loads(output)


def rpc(method, request_id=None, **params):
    message = {"jsonrpc": "2.0", "method": method, "params": params}
    if request_id is not None:
        message["id"] = request_id
    return message


def mcp(base, token, env, expected_mutes, package=None, *, mute_incident=None):
    def initialize(version):
        return rpc("initialize", 1, protocolVersion=version, capabilities={},
                   clientInfo={"name": PROJECT, "version": "1"})

    version = MCP_VERSIONS[0] if package is not None else MCP_VERSIONS[-1]
    requests = [
        initialize(version),
        rpc("notifications/initialized"),
        rpc("tools/list", 10),
        rpc("tools/call", 11, name="noticeplace", arguments={"action": "noticeplace_list_mutes"}),
    ]
    if mute_incident:
        requests.append(rpc("tools/call", 12, name="noticeplace", arguments={
            "action": "noticeplace_set_notifications",
            "params": {"incident_id": mute_incident, "enabled": False},
        }))

    if package is None:
        http(base, "POST", "/mcp", status=401, body=requests[0])
        for supported in MCP_VERSIONS[:-1]:
            response = http(base, "POST", "/mcp", token=token, body=initialize(supported))
            require(response.get("id") == 1 and response["result"]["protocolVersion"] == supported,
                    f"MCP negotiation changed for {supported}")
        responses = []
        for request in requests:
            response = http(base, "POST", "/mcp", status=200 if "id" in request else 204,
                            token=token, body=request)
            if response is not None:
                responses.append(response)
    else:
        output = subprocess.check_output(
            [str(package / "node_modules/.bin/notify-mcp")], env=env, cwd=package,
            input="".join(json.dumps(request) + "\n" for request in requests),
            text=True, timeout=15,
        )
        responses = [json.loads(line) for line in output.splitlines()]
    require(
        [(response.get("jsonrpc"), response.get("id")) for response in responses]
        == [("2.0", request["id"]) for request in requests if "id" in request],
        "MCP JSON-RPC response order or notification semantics changed",
    )
    for response in responses:
        require("result" in response and "error" not in response, f"MCP call failed: {response}")
    results = {response["id"]: response["result"] for response in responses}
    require(results[1]["protocolVersion"] == version, f"MCP negotiation changed for {version}")
    tools = {tool["name"]: tool["inputSchema"] for tool in results[10]["tools"]}
    schema = tools["noticeplace"]
    require(schema["type"] == "object" and "action" in schema["required"]
            and {"noticeplace_list_mutes", "noticeplace_set_notifications"}
            <= set(schema["properties"]["action"]["enum"]), "public MCP action schema changed")
    for request_id in (11, 12) if mute_incident else (11,):
        result = results[request_id]
        require(json.loads(result["content"][0]["text"]) == result["structuredContent"],
                "MCP text and structuredContent disagree")
    mutes = results[11]["structuredContent"]
    require(mutes["project"] == PROJECT
            and {row["dedup_key"] for row in mutes["mutes"]} == expected_mutes,
            f"MCP mute readback changed: {mutes}")
    if mute_incident:
        muted = results[12]["structuredContent"]
        require(muted["incident_id"] == mute_incident and muted["notifications_muted"] is True,
                f"MCP mute was not applied: {muted}")
        return muted["dedup_key"]
    return None


def write_event(base, env, package, stage, kind):
    key = f"{stage}-{kind}"
    event = {"project": PROJECT, "severity": "important", "title": f"Проверка {key}",
             "dedup_key": key, "body": "Текст для чтения после отката"}
    if kind == "http":
        payload = {"schema": "notify.event.v1", "kind": "incident", "recipient": "me", **event}
        receipt = http(base, "POST", "/v1/events", status=202, token=env["NOTIFY_CENTER_TOKEN"],
                       body=payload, key=key)
    else:
        payload = None
        receipt = sdk(kind, package, "emit", json.dumps({**event, "idempotency_key": key}), env)
        require(receipt["idempotency_key"] == key, f"{kind} SDK lost the supplied idempotency key")
    require(receipt["state"] == "open", f"new incident {key} was not open")
    incident_id = receipt["incident_id"]
    ack = http(base, "POST", f"/v1/incidents/{incident_id}/ack", token=env["NOTIFY_CENTER_TOKEN"],
               body={"actor": PROJECT})
    require(ack["state"] == "acknowledged", f"ack failed for {key}")
    return {"key": key, "event_id": receipt["event_id"], "incident_id": incident_id,
            "title": event["title"], "body": event["body"], "payload": payload}


def read_events(base, env, other_token, packages, events):
    token = env["NOTIFY_CENTER_TOKEN"]
    for event in events:
        incident_id = event["incident_id"]
        path = f"/v1/incidents/{incident_id}"
        incident = http(base, "GET", path, token=token)
        views = [incident]
        for package in packages:
            for kind in ("python", "node"):
                views.append(sdk(kind, package, "wait", incident_id, env))
        expected = (incident_id, event["title"], event["body"], "acknowledged", 1)
        for view in views:
            require(tuple(view[field] for field in ("id", "title", "body", "state", "occurrences")) == expected,
                    f"incident readback changed for {event['key']}")
        if event["payload"] is not None:
            http(base, "GET", path, status=401, token=other_token)
            replay = http(base, "POST", "/v1/events", status=202, token=token,
                          body=event["payload"], key=event["key"])
            require(replay["idempotent"] is True and (replay["event_id"], replay["incident_id"])
                    == (event["event_id"], incident_id), f"replay changed identity for {event['key']}")
            http(base, "POST", "/v1/events", status=409, token=token, key=event["key"],
                 body={**event["payload"], "title": "Conflicting replay"})


def read_sqlite(database: Path, events, mutes):
    with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as connection:
        require(connection.execute("PRAGMA quick_check").fetchall() == [("ok",)], "SQLite quick_check failed")
        rows = connection.execute("""
            SELECT idempotency_key, event_id, incident_id, incidents.title, incidents.body,
                   incidents.state, incidents.occurrences
            FROM events JOIN incidents ON incidents.id = events.incident_id
        """).fetchall()
        require(len(rows) == connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
                == connection.execute("SELECT COUNT(*) FROM incidents").fetchone()[0],
                "fixture has orphan or duplicate events/incidents")
        expected = {
            (event["key"], event["event_id"], event["incident_id"], event["title"], event["body"],
             "acknowledged", 1)
            for event in events
        }
        require(set(rows) == expected, "SQLite event and incident readback disagrees with the consumers")
        actual_mutes = connection.execute(
            "SELECT project, dedup_key FROM notification_mutes WHERE unmuted_at IS NULL"
        ).fetchall()
        require(set(actual_mutes) == {(PROJECT, key) for key in mutes}, "SQLite mute readback changed")
        delivery_states = dict(connection.execute("SELECT status, COUNT(*) FROM deliveries GROUP BY status"))
        require(delivery_states.keys() <= {"queued", "cancelled"},
                f"unexpected delivery activity in workers-disabled fixture: {delivery_states}")
    return {"events": len(rows), "incidents": len(rows), "active_mutes": len(actual_mutes), "quick_check": "ok"}
