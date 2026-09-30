#!/usr/bin/env python3
"""Real Stalwart/extension JMAP smoke in a disposable, loopback-only namespace.

No SMTP, real account, public listener, Thunderbird GUI, or overlay claim. The
upstream recovery listener binds [::]:8080 only INSIDE a fresh network namespace.
Run without --execute to print the intended boundary without starting anything.
"""
# SPDX-License-Identifier: GPL-3.0-only

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import resource
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
REVISION = "af37a234981722493b74623a983581691d2b70b6"
CORE = "urn:ietf:params:jmap:core"
MAIL = "urn:ietf:params:jmap:mail"
STALWART = "urn:stalwart:jmap"
ORIGIN = "http://127.0.0.1:8080"
PERMISSIONS = ("authenticate", "jmapMailboxGet", "jmapMailboxCreate",
               "jmapEmailGet", "jmapEmailImport", "jmapBlobGet", "jmapBlobUpload")
ERROR_CODES = frozenset(('host-netns-refused', 'non-loopback-interface-refused',
    'route-present', 'root-server-refused', 'server-exited', 'server-start-timeout',
    'http-status', 'http-response-bound', 'redirect-rejected', 'jmap-method-rejected',
    'jmap-create-rejected', 'invalid-id', 'owner-account-binding',
    'extension-live-import-failed', 'extension-receipt-binding', 'readback-count',
    'readback-mailbox-binding', 'readback-blob-id', 'raw-message-mismatch'))
JMAP_ERROR_TYPES = frozenset(('forbidden', 'invalidProperties', 'invalidArguments',
    'invalidResultReference', 'serverFail', 'serverUnavailable', 'notFound',
    'overQuota', 'rateLimit', 'validationFailed', 'primaryKeyViolation',
    'foreignKeyViolation', 'invalidPatch', 'unknownMethod'))
JMAP_PROPERTIES = frozenset(('name', 'isEnabled', 'domainId', 'createdAt', 'roles',
    'permissions', 'credentials', 'certificateManagement', 'dnsManagement',
    'dkimManagement', 'reportAddressUri', 'directoryId', 'memberTenantId', 'secret'))
MESSAGE = (b"From: fixture@volparossa.test\r\nTo: owner@volparossa.test\r\n"
           b"Subject: VOLPAROSSA disposable JMAP fixture\r\n"
           b"Message-ID: <stalwart-native-fixture@volparossa.test>\r\n"
           b"Date: Tue, 01 Sep 2026 12:00:00 +0000\r\n"
           b"MIME-Version: 1.0\r\nContent-Type: text/plain; charset=utf-8\r\n"
           b"\r\nPublic synthetic fixture. No real mail or user data.\r\n")
NODE_DRIVER = r'''
import {readFile, writeFile} from 'node:fs/promises';
import {StalwartAccount} from '/opt/jmap.mjs';
const config = JSON.parse(await readFile('/opt/work/private/owner.json', 'utf8'));
const account = new StalwartAccount(config); // Genuine fetch; no injected double.
let phase = 'extension-connect';
try {
  const accounts = await account.connect();
  if (accounts.length !== 1 || accounts[0].id !== config.accountId) throw Error('account');
  phase = 'extension-mailboxes';
  const boxes = await account.mailboxes(config.accountId, true);
  if (!boxes.some(b => b.id === config.mailboxId && b.mayAdd)) throw Error('mailbox');
  phase = 'extension-import';
  const result = await account.importMessage({accountId: config.accountId,
    mailboxId: config.mailboxId, bytes: new Uint8Array(await readFile('/opt/work/private/fixture.eml')),
    confirmed: true});
  if (!result.metadataReadback || result.messageSent || result.rawMessageVerified) throw Error('receipt');
  await writeFile('/opt/work/private/import.json', JSON.stringify(result), {mode: 0o600, flag: 'wx'});
  await writeFile('/opt/work/extension.json', JSON.stringify({version: 1, success: true,
    phase: 'complete', metadata_readback: true, message_sent: false,
    raw_message_verified_by_extension: false, account_count: accounts.length}), {mode: 0o600});
} catch (error) {
  const allowed = ['invalid_session','authentication_failed','request_failed','invalid_response',
    'jmap_rejected','import_uncertain','import_rejected','import_readback_failed','request_cancelled'];
  await writeFile('/opt/work/extension.json', JSON.stringify({version: 1, success: false, phase,
    error: allowed.includes(error?.code) ? error.code : 'unknown'}), {mode: 0o600});
  process.exitCode = 1;
} finally { account.disconnect(); }
'''


def require(value, code):
    if not value:
        raise ValueError(code)


class JmapRejected(ValueError):
    def __init__(self, code, value):
        super().__init__(code)
        value = value if isinstance(value, dict) else {}
        kind = value.get('type')
        self.diagnostic = {'type': kind if kind in JMAP_ERROR_TYPES else 'unknown',
            'properties': sorted(p for p in value.get('properties', [])
                                 if isinstance(p, str) and p in JMAP_PROPERTIES)}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_json(path, value):
    with path.open('x', encoding='utf-8') as output:
        os.chmod(path, 0o600)
        json.dump(value, output, sort_keys=True)
        output.write('\n')


def account_object(domain, password):
    # v0.16 List<T> and Map<T> are JSON OBJECTS, not arrays.
    return {'@type': 'User', 'name': 'owner', 'domainId': domain,
            'credentials': {'0': {'@type': 'Password', 'secret': password}},
            'roles': {'@type': 'User'}, 'permissions': {'@type': 'Replace',
                'enabledPermissions': {name: True for name in PERMISSIONS},
                'disabledPermissions': {}}, 'encryptionAtRest': {'@type': 'Disabled'}}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('redirect-rejected')


def http(username, password, path, body=None, raw=False):
    require(path.startswith('/jmap') and not path.startswith('//'), 'invalid-fixture-endpoint')
    auth = base64.b64encode(f'{username}:{password}'.encode()).decode('ascii')
    request = urllib.request.Request(ORIGIN + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization': 'Basic ' + auth, 'Content-Type': 'application/json'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=15) as response:
        require(response.status == 200, 'http-status')
        data = response.read(2 * 1024 * 1024 + 1)
        require(len(data) <= 2 * 1024 * 1024, 'http-response-bound')
        return data if raw else json.loads(data)


def call(username, password, name, arguments):
    response = http(username, password, '/jmap', {
        'using': [CORE, MAIL, STALWART], 'methodCalls': [[name, arguments, 'fixture']]})
    calls = response.get('methodResponses', [])
    if len(calls) == 1 and len(calls[0]) == 3 and calls[0][0] == 'error':
        raise JmapRejected('jmap-method-rejected', calls[0][1])
    require(len(calls) == 1 and len(calls[0]) == 3 and calls[0][0] == name
            and calls[0][2] == 'fixture', 'jmap-method-rejected')
    return calls[0][1]


def created(response, key):
    if response.get('notCreated'):
        raise JmapRejected('jmap-create-rejected', response['notCreated'].get(key))
    require(not response.get('notCreated') and key in response.get('created', {}),
            'jmap-create-rejected')
    value = response['created'][key]['id']
    require(isinstance(value, str) and 0 < len(value) <= 256
            and all(c.isascii() and (c.isalnum() or c in '_-') for c in value), 'invalid-id')
    return value


def group_join(process):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            continue
    process.wait(timeout=5)
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        return True
    return False


def limits():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024**2, 32 * 1024**2))
    resource.setrlimit(resource.RLIMIT_NOFILE, (1024, 1024))
    cpus = sorted(os.sched_getaffinity(0))
    os.sched_setaffinity(0, cpus[:2])


def child(host_netns):
    report = {'version': 1, 'kind': 'stalwart-extension-live-jmap', 'success': False,
              'phase': 'namespace-preflight', 'source_revision': REVISION,
              'server_executed': False, 'recovery_mode': True,
              'external_network_available': False, 'smtp_tested': False,
              'thunderbird_ui_tested': False, 'overlay_tested': False,
              'message_sent': False, 'process_groups_joined': False,
              'private_state_removed': False}
    processes = []
    private = Path('/opt/work/private')
    try:
        require(os.readlink('/proc/self/ns/net') != host_netns, 'host-netns-refused')
        require(socket.if_nameindex() == [(1, 'lo')], 'non-loopback-interface-refused')
        require(not Path('/proc/net/route').read_text().splitlines()[1:], 'route-present')
        require(os.geteuid() != 0, 'root-server-refused')
        report['isolated_loopback'] = True
        private.mkdir(mode=0o700)
        (private / 'tmp').mkdir(mode=0o700)
        write_json(private / 'store.json', {'@type': 'Sqlite',
            'path': '/opt/work/private/store.sqlite', 'poolWorkers': 2, 'poolMaxConnections': 4})
        admin_password = secrets.token_urlsafe(32)
        owner_password = secrets.token_urlsafe(32)
        env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'TMPDIR': '/opt/work/private/tmp',
               'STALWART_HOSTNAME': 'mail.volparossa.test', 'STALWART_RECOVERY_MODE': '1',
               'STALWART_RECOVERY_ADMIN': 'fixture-admin:' + admin_password}
        report['phase'] = 'server-start'
        with (private / 'server.log').open('xb') as log:
            server = subprocess.Popen(['/opt/stalwart', '--config', '/opt/work/private/store.json'],
                env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                preexec_fn=limits)
            processes.append(server)
            report['server_executed'] = True
            deadline = time.monotonic() + 100
            while True:
                require(server.poll() is None, 'server-exited')
                try:
                    http('fixture-admin', admin_password, '/jmap/session')
                    break
                except (urllib.error.URLError, TimeoutError, ConnectionError):
                    require(time.monotonic() < deadline, 'server-start-timeout')
                    time.sleep(0.2)
            # Recovery disables background services but the unmodified upstream
            # still tries to fetch its default web-UI bundle. No route exists.
            report['upstream_webui_fetch_network_blocked'] = True
            report['phase'] = 'domain-create'
            domain = created(call('fixture-admin', admin_password, 'x:Domain/set', {
                'create': {'fixture': {'name': 'volparossa.test', 'isEnabled': True,
                    'certificateManagement': {'@type': 'Manual'},
                    'dnsManagement': {'@type': 'Manual'}, 'dkimManagement': {'@type': 'Manual'}}}}), 'fixture')
            report['phase'] = 'account-create'
            account = created(call('fixture-admin', admin_password, 'x:Account/set', {
                'create': {'fixture': account_object(domain, owner_password)}}), 'fixture')
            owner = 'owner@volparossa.test'
            report['phase'] = 'owner-session'
            session = http(owner, owner_password, '/jmap/session')
            require(session['primaryAccounts'][MAIL] == account
                    and list(session['accounts']) == [account], 'owner-account-binding')
            report['phase'] = 'mailbox-create'
            mailbox = created(call(owner, owner_password, 'Mailbox/set', {'accountId': account,
                'create': {'fixture': {'name': 'VOLPAROSSA fixture', 'parentId': None}}}), 'fixture')
            write_json(private / 'owner.json', {'server': ORIGIN, 'username': owner,
                'password': owner_password, 'accountId': account, 'mailboxId': mailbox})
            (private / 'fixture.eml').write_bytes(MESSAGE)
            (private / 'driver.mjs').write_text(NODE_DRIVER)
            report['phase'] = 'extension-live-import'
            with (private / 'node.log').open('xb') as output:
                node = subprocess.Popen(['/opt/node', '/opt/work/private/driver.mjs'],
                    env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}, stdout=output,
                    stderr=subprocess.STDOUT, start_new_session=True, preexec_fn=limits)
                processes.append(node)
                require(node.wait(timeout=100) == 0, 'extension-live-import-failed')
            receipt = json.loads((private / 'import.json').read_text())
            require(receipt['accountId'] == account and receipt['mailboxId'] == mailbox
                    and receipt['metadataReadback'] is True, 'extension-receipt-binding')
            report['phase'] = 'independent-raw-readback'
            result = call(owner, owner_password, 'Email/get', {'accountId': account,
                'ids': [receipt['emailId']], 'properties': ['id', 'blobId', 'mailboxIds', 'size']})
            require(len(result['list']) == 1 and not result['notFound'], 'readback-count')
            saved = result['list'][0]
            require(saved['id'] == receipt['emailId'] and saved['mailboxIds'].get(mailbox) is True,
                    'readback-mailbox-binding')
            blob = saved['blobId']
            require(isinstance(blob, str) and blob.isalnum() and blob.isascii(), 'readback-blob-id')
            raw = http(owner, owner_password,
                f'/jmap/download/{account}/{blob}/fixture.eml', raw=True)
            require(raw == MESSAGE, 'raw-message-mismatch')
            report.update({'phase': 'complete', 'success': True, 'metadata_readback': True,
                'independent_raw_readback': True, 'fixture_bytes': len(MESSAGE),
                'fixture_sha256': hashlib.sha256(MESSAGE).hexdigest()})
    except BaseException as error:
        # Never retain raw HTTP bodies, credentials, logs, exception messages or IDs.
        report['error_class'] = type(error).__name__ if type(error).__name__ in (
            'ValueError', 'HTTPError', 'URLError', 'TimeoutExpired', 'TimeoutError',
            'KeyError', 'FileNotFoundError', 'PermissionError') else 'unknown'
        if type(error) is ValueError and str(error) in ERROR_CODES:
            report['error_code'] = str(error)
        if isinstance(error, JmapRejected):
            report['error_class'] = 'JmapRejected'
            report['error_code'] = str(error)
            report['jmap_error'] = error.diagnostic
        if isinstance(error, urllib.error.HTTPError):
            report['http_status'] = error.code
    finally:
        joined = [group_join(process) for process in reversed(processes)]
        report['process_groups_joined'] = all(joined)
        if private.is_dir() and not private.is_symlink() and all(joined):
            shutil.rmtree(private)  # Exact fresh fixture-owned target, never input state.
        report['private_state_removed'] = not private.exists()
        report['success'] &= report['process_groups_joined'] and report['private_state_removed']
        write_json(Path('/opt/work/result.json'), report)
    return 0 if report['success'] else 1


def command(args, work, host_netns):
    # All new mount targets live under the disposable /opt tmpfs; /work need
    # not exist on the host and cannot be created on its read-only root mount.
    return ['/usr/bin/bwrap', '--die-with-parent', '--new-session', '--unshare-user',
        '--uid', str(os.getuid()), '--gid', str(os.getgid()), '--unshare-net', '--unshare-pid',
        '--unshare-ipc', '--unshare-uts', '--cap-drop', 'ALL', '--ro-bind', '/', '/',
        '--tmpfs', '/home', '--tmpfs', '/root', '--tmpfs', '/run', '--tmpfs', '/media',
        '--tmpfs', '/tmp', '--tmpfs', '/opt', '--proc', '/proc', '--dev', '/dev',
        '--ro-bind', str(args.binary), '/opt/stalwart',
        '--ro-bind', str(args.node), '/opt/node',
        '--ro-bind', str(ROOT / 'extensions/volparossa-mail-host/jmap.mjs'), '/opt/jmap.mjs',
        '--ro-bind', str(Path(__file__).resolve()), '/opt/smoke_stalwart.py',
        '--bind', str(work), '/opt/work', '--chdir', '/opt/work', '--clearenv',
        '--setenv', 'PATH', '/usr/bin:/bin', '--setenv', 'LANG', 'C.UTF-8',
        '--', '/usr/bin/python3', '-B', '/opt/smoke_stalwart.py', '--inside', host_netns]


def host_snapshot():
    result = {'netns': os.readlink('/proc/self/ns/net')}
    for path in ('/proc/net/route', '/proc/net/ipv6_route', '/etc/resolv.conf'):
        result[path] = digest(Path(path))
    return result


def run(args):
    # Do not start any code until root has supplied independently observed hashes.
    from prepare_stalwart import workspace_path, validate
    source = validate(ROOT / 'build/stalwart-0.16.24')
    require(source['revision'] == REVISION, 'source-revision')
    require(os.getuid() != 0, 'root-run-refused')
    for name in ('binary', 'node'):
        path = getattr(args, name)
        require(path.is_absolute() and path.is_file() and not path.is_symlink(), 'invalid-executable')
        require(digest(path) == getattr(args, name + '_sha256'), 'executable-hash')
    work = workspace_path(args.output, fresh=True)
    snapshot = host_snapshot()
    plan = {'kind': 'stalwart-disposable-smoke-plan', 'source_revision': REVISION,
        'binary_sha256': args.binary_sha256, 'node_sha256': args.node_sha256,
        'extension_sha256': digest(ROOT / 'extensions/volparossa-mail-host/jmap.mjs'),
        'output': str(work), 'new_user_net_pid_ipc_uts_namespaces': True,
        'only_persistent_writable_path': str(work), 'server_listener_inside_only': '[::]:8080',
        'external_routes': False, 'real_mail': False, 'smtp': False,
        'private_state_deleted_after_join': True,
        'upstream_default_webui_download_attempt': 'blocked by isolated network'}
    print(json.dumps(plan, sort_keys=True), flush=True)
    if not args.execute:
        return 0
    work.mkdir(mode=0o700)
    write_json(work / 'plan.json', plan)
    process = None
    report = {'version': 1, 'success': False, 'boundary_exit_code': None,
              'namespace_process_group_joined': False, 'host_network_snapshot_unchanged': False,
              'private_state_removed': False}
    try:
        with (work / 'boundary.log').open('xb') as log:
            process = subprocess.Popen(command(args, work, snapshot['netns']), stdout=log,
                stderr=subprocess.STDOUT, start_new_session=True, preexec_fn=limits)
            report['boundary_exit_code'] = process.wait(timeout=300)
    except (OSError, subprocess.TimeoutExpired) as error:
        report['error_class'] = type(error).__name__
    finally:
        if process is not None:
            report['namespace_process_group_joined'] = group_join(process)
        report['host_network_snapshot_unchanged'] = snapshot == host_snapshot()
        # If the inner timeout/signal prevented its finally clause, the joined
        # PID namespace is now gone. Delete only the new owned fixture subtree.
        private = work / 'private'
        if report['namespace_process_group_joined'] and private.is_dir() and not private.is_symlink():
            shutil.rmtree(private)
        report['private_state_removed'] = not private.exists() and not private.is_symlink()
        # No raw logs are exported, even on boundary/child failure.
        (work / 'boundary.log').unlink(missing_ok=True)
        report['success'] = report['boundary_exit_code'] == 0 and all((
            report['namespace_process_group_joined'], report['host_network_snapshot_unchanged'],
            report['private_state_removed']))
        write_json(work / 'boundary.json', report)
    print(json.dumps(report, sort_keys=True), flush=True)
    return 0 if report['success'] else 1


def main():
    os.umask(0o077)
    if len(sys.argv) == 3 and sys.argv[1] == '--inside':
        return child(sys.argv[2])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--binary-sha256', required=True)
    parser.add_argument('--node', type=Path, required=True)
    parser.add_argument('--node-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    return run(parser.parse_args())


if __name__ == '__main__':
    sys.exit(main())
