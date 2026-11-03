"""H5 离线测试：服务器配置的安全强制规则。不起服务，直接检验 load_config 决策；固化既有行为。"""
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'mcp'))
import server

DEFAULTS = {'host': '127.0.0.1', 'port': 8765, 'path': '/mcp',
            'name': 'mc-tools', 'version': '0.0.0', 'clientName': 'test-client'}


class ServerSecurityConfig(unittest.TestCase):
    def load(self, **env):
        return server.load_config(env=dict(env), defaults=dict(DEFAULTS))

    def test_loopback_without_token_is_allowed(self):
        config = self.load()
        self.assertEqual(config.token, '')
        self.assertEqual(config.authentication, {'implemented': False, 'type': 'none'})

    def test_non_loopback_without_token_is_refused(self):
        for host in ('0.0.0.0', '::', '192.168.1.10'):
            with self.assertRaises(ValueError, msg=host) as caught:
                self.load(MCP_HOST=host)
            self.assertIn('Non-loopback', str(caught.exception))

    def test_non_loopback_with_token_but_no_public_url_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            self.load(MCP_HOST='192.168.1.10', MCP_AUTH_TOKEN='secret-token-1')
        self.assertIn('Non-loopback', str(caught.exception))

    def test_non_loopback_full_requirements_accepted(self):
        config = self.load(MCP_HOST='192.168.1.10', MCP_AUTH_TOKEN='secret-token-1',
                           MCP_PUBLIC_URL='https://mc.example.com/mcp')
        self.assertEqual(config.token, 'secret-token-1')
        self.assertEqual(config.authentication['implemented'], True)
        self.assertEqual(config.url, 'https://mc.example.com/mcp')

    def test_connection_entry_uses_placeholder_not_secret(self):
        config = self.load(MCP_HOST='192.168.1.10', MCP_AUTH_TOKEN='secret-token-1',
                           MCP_PUBLIC_URL='https://mc.example.com/mcp')
        entry = config.connection[0]
        self.assertEqual(entry['headers']['Authorization'], 'Bearer ${MCP_AUTH_TOKEN}')
        self.assertNotIn('secret-token-1', str(entry))

    def test_public_url_loopback_while_host_non_loopback_is_refused(self):
        with self.assertRaises(ValueError):
            self.load(MCP_HOST='192.168.1.10', MCP_AUTH_TOKEN='secret-token-1',
                      MCP_PUBLIC_URL='https://127.0.0.1/mcp')

    def test_public_url_path_mismatch_is_refused(self):
        with self.assertRaises(ValueError):
            self.load(MCP_HOST='192.168.1.10', MCP_AUTH_TOKEN='secret-token-1',
                      MCP_PUBLIC_URL='https://mc.example.com/other')

    def test_public_url_wildcard_host_is_refused(self):
        with self.assertRaises(ValueError):
            self.load(MCP_HOST='192.168.1.10', MCP_AUTH_TOKEN='secret-token-1',
                      MCP_PUBLIC_URL='https://0.0.0.0/mcp')

    def test_token_with_whitespace_or_control_chars_is_refused(self):
        for bad in ('bad token', 'bad\ttoken', 'bad\x7ftoken'):
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.load(MCP_AUTH_TOKEN=bad)

    def test_hostname_as_mcp_host_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            self.load(MCP_HOST='mc.example.com')
        self.assertIn('IP address or localhost', str(caught.exception))

    def test_ipv6_non_loopback_full_requirements_accepted(self):
        config = self.load(MCP_HOST='2001:db8::1', MCP_AUTH_TOKEN='secret-token-1',
                           MCP_PUBLIC_URL='https://mc.example.com/mcp')
        self.assertEqual(config.host, '2001:db8::1')


if __name__ == '__main__':
    unittest.main()
