#!/usr/bin/env python3
"""Small dependency-free Agent Plugins 1.0 package validator."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


PACKAGE = Path(__file__).resolve().parents[1]
SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
SEMVER = re.compile(r"^0\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
TOOLS = ("noticeplace_ask_human", "noticeplace_pending_questions", "noticeplace_answer",
        "noticeplace_question_result", "noticeplace_incident_progress")


def load(name: str) -> dict:
    value = json.loads((PACKAGE / name).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{name}: object required")
    return value


def validate() -> str:
    manifest = load("plugin.json")
    if manifest.get("$schema") != SCHEMA:
        raise ValueError("plugin.json: Agent Plugins 1.0 schema is required")
    if not isinstance(manifest.get("name"), str) or not NAME.fullmatch(manifest["name"]):
        raise ValueError("plugin.json: invalid name")
    if not isinstance(manifest.get("version"), str) or not SEMVER.fullmatch(manifest["version"]):
        raise ValueError("plugin.json: invalid version")
    if PACKAGE.name != manifest["version"] or PACKAGE.parent.name != manifest["name"]:
        raise ValueError("package directory must be packages/<name>/<version>")
    for field in ("description", "license"):
        if not isinstance(manifest.get(field), str) or not manifest[field].strip():
            raise ValueError(f"plugin.json: {field} is required")

    mcp = load("mcp.json")
    if mcp.get("$schema") != MCP_SCHEMA or not isinstance(mcp.get("mcpServers"), dict):
        raise ValueError("mcp.json: Agent Plugins 1.0 MCP schema is required")
    for server_name, server in mcp["mcpServers"].items():
        if not isinstance(server, dict):
            raise ValueError(f"mcp.json: {server_name} must be an object")
        if server.get("type") == "streamable-http":
            if not isinstance(server.get("url"), str) or not server["url"].startswith("https://"):
                raise ValueError(f"mcp.json: {server_name} must use HTTPS")
            if "headers" in server or "env" in server:
                raise ValueError(f"mcp.json: {server_name} must not embed credentials")
        elif server.get("type") == "stdio":
            command = server.get("command")
            if not isinstance(command, str) or not command.startswith("/"):
                raise ValueError(f"mcp.json: {server_name} must use an absolute command path")
            # Only protected file paths are named; no secret values, ever.
            for key, value in (server.get("env") or {}).items():
                if not isinstance(value, str) or not value.startswith("/") or len(value) > 4096:
                    raise ValueError(f"mcp.json: {server_name}.env.{key} must be a protected path")
                if re.search(r"(?i)(bearer|token|secret|password)\s*=", value):
                    raise ValueError(f"mcp.json: {server_name}.env.{key} must not embed a credential")
        else:
            raise ValueError(f"mcp.json: {server_name} has an unsupported transport")

    skill_dirs = list((PACKAGE / "skills").iterdir())
    if len(skill_dirs) != 1 or not (skill_dirs[0] / "SKILL.md").is_file():
        raise ValueError("skills: exactly one SKILL.md package is required")
    skill_text = (skill_dirs[0] / "SKILL.md").read_text(encoding="utf-8")
    if "---" not in skill_text or "opaque" not in skill_text:
        raise ValueError("skills: expected frontmatter and opaque handling rules")
    for tool in TOOLS:
        if tool not in skill_text:
            raise ValueError(f"skills: {tool} contract is missing")
    if "human_request_create" not in skill_text:
        raise ValueError("skills: the supported native question binding is missing")
    if "one creation means one card" not in skill_text:
        raise ValueError("skills: must keep exactly one question creation path")
    if "value=choice_id" not in skill_text or "cwd=binding.native_target.cwd" not in skill_text:
        raise ValueError("skills: choice mapping and bound cwd are required")

    # Native loaders resolve components from the package root. Keep one source
    # skill and one MCP projection inside this same versioned bundle.
    for filename in (".codex-plugin/plugin.json", ".zcode-plugin/plugin.json"):
        native = load(filename)
        if any(native.get(key) != manifest[key] for key in ("name", "version")):
            raise ValueError(f"{filename}: identity differs from the portable manifest")
        if native.get("skills") != "./skills/" or native.get("mcpServers") != "./mcp.codex.json":
            raise ValueError(f"{filename}: native skill/MCP source must stay in this package")
    native_mcp = load("mcp.codex.json")
    server = native_mcp["mcpServers"]["noticeplace"]
    portable = mcp["mcpServers"]["NoticePlace"]
    if any(server.get(key) != portable.get(key) for key in ("command", "args", "env")):
        raise ValueError("native and portable MCP must use the same authenticated stdio server")
    root = PACKAGE.parents[2]
    codex_market = json.loads((root / ".agents/plugins/marketplace.json").read_text())
    zcode_market = json.loads((root / ".claude-plugin/marketplace.json").read_text())
    expected_path = f"./packages/{manifest['name']}/{manifest['version']}"
    if codex_market["plugins"][0]["source"] != {"source": "local", "path": expected_path}:
        raise ValueError("Codex marketplace must use the local versioned bundle")
    if zcode_market["plugins"][0]["source"] != expected_path:
        raise ValueError("ZCode marketplace must use the local versioned bundle")

    secret_patterns = (
        re.compile(r"sk-[A-Za-z0-9]{16,}"),
        re.compile(r"Bearer\s+[A-Za-z0-9._-]{20,}"),
        re.compile(r"AGE-SECRET-KEY-1-[A-Za-z0-9]+"),
        re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----"),
    )
    for path in PACKAGE.rglob("*"):
        text = path.read_text(encoding="utf-8", errors="ignore") if path.is_file() else ""
        if any(pattern.search(text) for pattern in secret_patterns):
            raise ValueError(f"secret-like content found in {path.relative_to(PACKAGE)}")
    return f"PASS {manifest['name']} {manifest['version']}"


if __name__ == "__main__":
    try:
        print(validate())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"FAIL {error}", file=sys.stderr)
        raise SystemExit(1)
