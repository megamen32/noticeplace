"""Official Hermes loader + HTTP transport with fake profile credentials only.

No live server, runtime config, plugin install, notification or question replay.
Run with unittest; HTTP integration uses the official Hermes PM-selected interpreter.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ROOT / 'noticeplace-plugin/packages/noticeplace'
OLD = PACKAGES / '0.1.1'
NEW = PACKAGES / '0.1.2'
HERMES = Path(os.environ.get('NOTICEPLACE_HERMES_SOURCE', '/home/roomhacker/.hermes/hermes-agent'))
ENDPOINT = 'https://notify.bezrabotnyi.com/mcp'


def loader():
    if not (HERMES / 'hermes_cli/agent_plugins.py').is_file():
        raise RuntimeError('official Hermes checkout required for this integration gate')
    sys.path.insert(0, str(HERMES))
    from hermes_cli.agent_plugins import load_agent_plugin
    return load_agent_plugin


# The only substituted network layer is httpx MockTransport. Official loader,
# connect-time interpolation, MCPServerTask and SDK handshake/tools/list run.
TRANSPORT_PROOF = r'''
import asyncio, json, os, sys
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock, patch
sys.path.insert(0, sys.argv[1])
from hermes_cli.agent_plugins import load_agent_plugin
from tools import mcp_tool as core, mcp_tool_discovery as discovery
from tools import mcp_tool_transport as transport
core._ensure_mcp_sdk()
httpx = core.sdk_httpx()
package = load_agent_plugin(Path(sys.argv[2]), Path(os.environ['HERMES_HOME']) / 'data')
assert not package.diagnostics, [(d.scope, d.message) for d in package.diagnostics]
config = package.mcp_servers['NoticePlace']
assert config['headers']['Authorization'] == 'Bearer ${NOTIFY_MCP_TOKEN}'
seen = []

def wire(request):
    # No request leaves this process, including if an accidental URL changes.
    assert str(request.url) == 'https://notify.bezrabotnyi.com/mcp'
    assert request.headers.get('Authorization') == 'Bearer fixture-only-token'
    if request.method == 'GET':
        return httpx.Response(405)
    if request.method == 'DELETE':
        return httpx.Response(200)
    body = json.loads(request.content)
    method = body.get('method')
    seen.append(method)
    if 'id' not in body:
        return httpx.Response(202)
    if method == 'initialize':
        result = {'protocolVersion': body['params']['protocolVersion'],
                  'capabilities': {'tools': {}},
                  'serverInfo': {'name': 'isolated-notice-fixture', 'version': '1'}}
    elif method == 'tools/list':
        result = {'tools': [{'name': 'noticeplace_instructions', 'description': 'Isolated fixture',
                             'inputSchema': {'type': 'object', 'properties': {}}}]}
    elif method == 'tools/call':
        assert body['params']['name'] == 'noticeplace_instructions'
        result = {'content': [{'type': 'text', 'text': 'fixture instructions'}]}
    elif method == 'ping':
        result = {}
    else:
        raise AssertionError('unexpected fixture method: ' + str(method))
    return httpx.Response(200, json={'jsonrpc': '2.0', 'id': body['id'], 'result': result})

async def run():
    with patch.object(httpx, 'AsyncHTTPTransport', side_effect=lambda **kw: httpx.MockTransport(wire)), \
         patch.object(transport, '_mcp_proxy_mounts', return_value=None), \
         patch.object(core.MCPServerTask, '_preflight_content_type', new=AsyncMock()), \
         patch.object(core.MCPServerTask, '_refresh_remote_config', side_effect=lambda cfg: cfg):
        if sys.argv[3] == 'missing':
            try:
                await discovery._connect_server('noticeplace-fixture', config)
            except ValueError as error:
                assert getattr(error, 'failure_class', None) == 'missing_credentials'
                assert 'NOTIFY_MCP_TOKEN' in str(error)
            else:
                raise AssertionError('missing token reached transport')
            assert not seen
            print(json.dumps({'missing_credentials': True, 'requests': 0}))
            return
        server = await asyncio.wait_for(discovery._connect_server('noticeplace-fixture', config), 10)
        try:
            result = await asyncio.wait_for(server.session.call_tool('noticeplace_instructions', {}), 5)
            assert result.content[0].text == 'fixture instructions'
            assert server._config['strict_redirect_headers'] is True
        finally:
            await server.shutdown()
    assert {'initialize', 'tools/list', 'tools/call'}.issubset(seen)
    print(json.dumps({'authenticated': True, 'methods': seen, 'transport': 'mock-http'}))

if sys.argv[3] == 'merge':
    from tools import mcp_tool_config
    native = {'url': 'https://notify.bezrabotnyi.com/mcp',
              'headers': {'Authorization': 'Bearer fixture-native-token'}}
    manager = SimpleNamespace(get_portable_mcp_servers=lambda: {'noticeplace': config})
    with patch.dict(os.environ, {'HERMES_SAFE_MODE': '0'}), \
         patch('hermes_cli.config.load_config', return_value={'mcp_servers': {'noticeplace': native}}), \
         patch('hermes_cli.plugins.discover_plugins'), \
         patch('hermes_cli.plugins.get_plugin_manager', return_value=manager), \
         patch('hermes_cli.env_loader.load_hermes_dotenv'):
        resolved = mcp_tool_config._load_mcp_config()
    assert resolved == {'noticeplace': native}
    print(json.dumps({'native_wins': True, 'servers': 1}))
else:
    asyncio.run(run())
'''


class HermesPortablePluginTests(unittest.TestCase):
    def package(self, data):
        # Before the fix, exercise the existing release (not a missing-file error).
        return loader()(NEW if NEW.exists() else OLD, data)

    def test_historical_011_reproduces_official_loader_rejection(self):
        with tempfile.TemporaryDirectory() as temp:
            package = loader()(OLD, Path(temp) / 'data')
        self.assertFalse(package.mcp_servers)
        self.assertTrue(any(d.scope == 'mcp' and 'top-level shape' in d.message for d in package.diagnostics))

    def test_historical_bearer_field_is_rejected_even_without_comment(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'historical'
            shutil.copytree(OLD, root)
            path = root / 'mcp.json'
            config = json.loads(path.read_text())
            config.pop('$comment', None)
            path.write_text(json.dumps(config))
            package = loader()(root, Path(temp) / 'data')
        self.assertFalse(package.mcp_servers)
        self.assertTrue(any('unknown remote field' in d.message for d in package.diagnostics))

    def test_actual_loader_registers_shared_http_and_literal_header_reference(self):
        with tempfile.TemporaryDirectory() as temp:
            package = self.package(Path(temp) / 'data')
        self.assertFalse(package.diagnostics, [(d.scope, d.message) for d in package.diagnostics])
        self.assertEqual('0.1.2', package.version)
        self.assertEqual(1, len(package.skills))
        self.assertEqual({'url': ENDPOINT, 'strict_redirect_headers': True,
                          'headers': {'Authorization': 'Bearer ${NOTIFY_MCP_TOKEN}'}},
                         package.mcp_servers['NoticePlace'])

    def test_http_package_loads_from_path_with_spaces(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'installed plugin'
            shutil.copytree(NEW if NEW.exists() else OLD, root)
            package = loader()(root, Path(temp) / 'data')
            self.assertFalse(package.diagnostics)
            self.assertEqual(ENDPOINT, package.mcp_servers['NoticePlace']['url'])
            self.assertNotIn('command', package.mcp_servers['NoticePlace'])

    def transport_proof(self, mode):
        sys.path.insert(0, str(HERMES))
        from pm.environments import selected_venv, venv_command
        command = venv_command(HERMES, selected_venv(HERMES), ('-I', '-B'))
        self.assertTrue(Path(command[0]).is_file(), 'official PM-selected environment required')
        with tempfile.TemporaryDirectory() as temp:
            # Never read the installed profile's config/.env or inherit credentials.
            env = {'PATH': os.defpath, 'HERMES_HOME': temp, 'PYTHONDONTWRITEBYTECODE': '1',
                   'HERMES_SAFE_MODE': '1', 'NO_PROXY': '*'}
            if mode == 'present':
                env['NOTIFY_MCP_TOKEN'] = 'fixture-only-token'
            result = subprocess.run([*command, '-c', TRANSPORT_PROOF, str(HERMES),
                                     str(NEW if NEW.exists() else OLD), mode],
                                    env=env, cwd=temp, capture_output=True, text=True, timeout=25)
        self.assertEqual(0, result.returncode, result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_official_connect_renders_fake_env_at_real_http_transport(self):
        proof = self.transport_proof('present')
        self.assertTrue(proof['authenticated'])
        self.assertIn('tools/call', proof['methods'])

    def test_missing_env_is_rejected_before_transport(self):
        self.assertEqual({'missing_credentials': True, 'requests': 0}, self.transport_proof('missing'))

    def test_existing_native_http_entry_wins_without_duplicate_registration(self):
        self.assertEqual({'native_wins': True, 'servers': 1}, self.transport_proof('merge'))

    def test_native_projections_preserve_shared_http_auth_and_skill(self):
        package = NEW if NEW.exists() else OLD
        self.assertEqual((OLD / 'mcp.codex.json').read_bytes(), (package / 'mcp.codex.json').read_bytes())
        for name in ('opencode.json', 'fast-agent.yaml', 'skills/noticeplace/SKILL.md'):
            self.assertEqual((OLD / name).read_bytes(), (package / name).read_bytes())
        self.assertFalse((package / 'bin').exists())
        for name in ('plugin.json', '.codex-plugin/plugin.json', '.zcode-plugin/plugin.json'):
            self.assertEqual('0.1.2', json.loads((package / name).read_text())['version'])
        for path in ('noticeplace-plugin/.agents/plugins/marketplace.json',
                     'noticeplace-plugin/.claude-plugin/marketplace.json'):
            catalog = json.loads((ROOT / path).read_text())
            source = catalog['plugins'][0]['source']
            self.assertEqual('./packages/noticeplace/0.1.2', source['path'] if isinstance(source, dict) else source)

    def test_package_validator(self):
        package = NEW if NEW.exists() else OLD
        result = subprocess.run([sys.executable, str(package / 'scripts/validate_plugin.py')],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual('PASS noticeplace 0.1.2', result.stdout.strip())
