"""Pure fixture contracts; no server execution, network, namespaces or installs."""
# SPDX-License-Identifier: GPL-3.0-only

import ast
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import smoke_stalwart as smoke


class StalwartSmokeTests(unittest.TestCase):
    def test_current_management_schema_is_map_shaped_and_owner_limited(self):
        value = smoke.account_object('domain-id', 'synthetic-test-password')
        self.assertEqual(value['@type'], 'User')
        self.assertEqual(value['credentials'], {'0': {'@type': 'Password',
            'secret': 'synthetic-test-password'}})
        self.assertEqual(value['permissions']['@type'], 'Replace')
        self.assertEqual(set(value['permissions']['enabledPermissions']), set(smoke.PERMISSIONS))
        self.assertNotIn('emailSend', smoke.PERMISSIONS)
        self.assertFalse(any(p.startswith('sys') for p in smoke.PERMISSIONS))

    def test_namespace_cannot_use_host_network_or_writable_source(self):
        args = SimpleNamespace(binary=Path('/pinned/stalwart'), node=Path('/pinned/node'))
        with patch.object(smoke.os, 'getuid', return_value=1000), \
             patch.object(smoke.os, 'getgid', return_value=1000):
            command = smoke.command(args, Path('/fresh/fixture'), 'net:[123]')
        for flag in ('--unshare-net', '--unshare-user', '--unshare-pid', '--clearenv'):
            self.assertIn(flag, command)
        self.assertEqual(command.count('--bind'), 1)
        self.assertEqual(command[command.index('--bind') + 1:command.index('--bind') + 3],
                         ['/fresh/fixture', '/opt/work'])
        for hidden in ('/home', '/root', '/run', '/media'):
            self.assertIn(['--tmpfs', hidden], [command[i:i+2] for i in range(len(command)-1)])
        self.assertNotIn('HOME', command)
        self.assertEqual(command[-2:], ['--inside', 'net:[123]'])

    def test_real_extension_driver_never_injects_fetch_or_claims_sending(self):
        self.assertIn('new StalwartAccount(config)', smoke.NODE_DRIVER)
        self.assertNotIn('fetcher:', smoke.NODE_DRIVER)
        self.assertIn('await account.importMessage', smoke.NODE_DRIVER)
        self.assertIn('await account.mailboxes', smoke.NODE_DRIVER)
        self.assertIn('message_sent: false', smoke.NODE_DRIVER)
        self.assertIn('finally { account.disconnect(); }', smoke.NODE_DRIVER)
        self.assertIn('volparossa.test', smoke.MESSAGE.decode())

    def test_jmap_create_rejections_are_not_success(self):
        self.assertEqual(smoke.created({'created': {'x': {'id': 'a_b-3'}}}, 'x'), 'a_b-3')
        for response in ({'notCreated': {'x': {'type': 'forbidden'}}},
                         {'created': {'x': {'id': '../../escape'}}},
                         {'created': {'y': {'id': 'ok'}}}):
            with self.assertRaises(ValueError):
                smoke.created(response, 'x')

    def test_fixture_has_no_install_download_or_host_network_mutation(self):
        source = (ROOT / 'scripts/smoke_stalwart.py').read_text()
        ast.parse(source)
        for forbidden in ('apt-get', 'ip route', 'nft ', 'sysctl', 'curl ', 'pip install'):
            self.assertNotIn(forbidden, source)
        self.assertIn("'host-netns-refused'", source)
        self.assertIn("'non-loopback-interface-refused'", source)

    def test_server_error_metadata_is_closed_and_drops_descriptions(self):
        error = smoke.JmapRejected('jmap-create-rejected', {'type': 'invalidProperties',
            'properties': ['name', 'private-unknown'], 'description': 'secret password here'})
        self.assertEqual(error.diagnostic, {'type': 'invalidProperties', 'properties': ['name']})
        self.assertNotIn('secret', str(error.diagnostic))


if __name__ == '__main__':
    unittest.main()
