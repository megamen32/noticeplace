"""Focused integration: installed official Hermes portable loader contract.

Detects skipped Notice MCP after c573316. Expected <1s/max5s per loader check;
release including setup is capped at180s. No native question/callback replay.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
PACKAGES=ROOT/'noticeplace-plugin/packages/noticeplace'
OLD=PACKAGES/'0.1.0'
NEW=PACKAGES/'0.1.1'
HERMES=Path(os.environ.get('NOTICEPLACE_HERMES_SOURCE','/home/roomhacker/.hermes/hermes-agent'))


def loader():
    if not (HERMES/'hermes_cli/agent_plugins.py').is_file():
        raise RuntimeError('focused integration requires an installed official Hermes checkout')
    sys.path.insert(0,str(HERMES))
    from hermes_cli.agent_plugins import load_agent_plugin
    return load_agent_plugin


class HermesPortablePluginTests(unittest.TestCase):
    def test_actual_loader_registers_portable_notice_server(self):
        with tempfile.TemporaryDirectory() as temp:
            # RED runs against current v0.1.0 until the new immutable bundle exists.
            package=loader()(NEW if NEW.exists() else OLD,Path(temp)/'data')
        self.assertIn('NoticePlace',package.mcp_servers,[(d.scope,d.message) for d in package.diagnostics])
        self.assertFalse(package.diagnostics)
        self.assertEqual('0.1.1',package.version)
        self.assertEqual(1,len(package.skills))

    def test_relative_command_resolves_when_installed_root_has_spaces(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'installed plugin';shutil.copytree(NEW,root,symlinks=True)
            package=loader()(root,Path(temp)/'data')
            server=package.mcp_servers['NoticePlace']
            self.assertEqual(str(root/'bin/ask-human-mcp'),server['command'])
            self.assertEqual(str(root),server['cwd'])
            self.assertEqual([],server['args'])
            self.assertEqual('/home/roomhacker/.config/ask-human/env',server['env']['ASK_HUMAN_ENV_FILE'])
            self.assertEqual(str(root),server['env']['PLUGIN_ROOT'])
            self.assertTrue(os.access(server['command'],os.X_OK))
            subprocess.run(['sh','-n',server['command']],check=True)

    def test_native_projections_keep_the_same_existing_auth_stdio_seam(self):
        old=json.loads((OLD/'mcp.codex.json').read_text())['mcpServers']['noticeplace']
        new=json.loads((NEW/'mcp.codex.json').read_text())['mcpServers']['noticeplace']
        self.assertEqual(old,new)
        for name in ('opencode.json','fast-agent.yaml','skills/noticeplace/SKILL.md'):
            self.assertEqual((OLD/name).read_bytes(),(NEW/name).read_bytes())
        wrapper=(NEW/'bin/ask-human-mcp').read_text()
        self.assertEqual('#!/bin/sh\nset -eu\nexec /opt/noticeplace/bin/ask-human-mcp "$@"\n',wrapper)

    def test_package_validator_and_native_manifests(self):
        result=subprocess.run([sys.executable,str(NEW/'scripts/validate_plugin.py')],capture_output=True,text=True)
        self.assertEqual(0,result.returncode,result.stderr)
        self.assertEqual('PASS noticeplace 0.1.1',result.stdout.strip())
        for name in ('plugin.json','.codex-plugin/plugin.json','.zcode-plugin/plugin.json'):
            self.assertEqual('0.1.1',json.loads((NEW/name).read_text())['version'])
        for name in ('mcp.json','mcp.codex.json'):
            text=(NEW/name).read_text()
            self.assertNotIn('Bearer ',text);self.assertNotIn('ASK_HUMAN_ALLOWED_ACTORS',text)
