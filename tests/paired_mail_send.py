#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Exercise the ordinary paired Mail send with an installed native library and two real Core providers."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time

import selected_mail_trial

SOURCE = Path(__file__).resolve().parents[1]
MIME = (b'From: alice@example.invalid\r\nTo: bob@example.invalid\r\n'
        b'Subject: private attachment\r\nMIME-Version: 1.0\r\n'
        b'Content-Type: multipart/mixed; boundary=b\r\n\r\n--b\r\n'
        b'Content-Type: text/plain; charset=utf-8\r\n\r\nOriginal line endings.\r\n--b\r\n'
        b'Content-Type: application/octet-stream\r\nContent-Transfer-Encoding: base64\r\n'
        b'\r\nAAEC/f7/\r\n--b--\r\n')


def check(value, stage):
    if not value:
        raise RuntimeError(stage)


def invoke(args, stage, ok=True):
    result = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, timeout=80)
    check((result.returncode == 0) == ok, stage)
    return json.loads(result.stdout) if ok else None


def digest(data):
    return hashlib.sha256(data).hexdigest()


def run(root, node, fixture):
    os.umask(0o077)
    provision = root / 'providers'
    provision.mkdir(mode=0o700)
    agent = subprocess.Popen([fixture, provision], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 80
        while not (provision / 'ready').is_file():
            check(agent.poll() is None and time.monotonic() < deadline, 'two provider enrollments')
            time.sleep(0.05)
        cfg = {side: json.loads((provision / side / 'core.json').read_bytes()) for side in ('a','b')}
        cli = [node, '--no-warnings', SOURCE / 'integration/signal-mail/cli.mjs']
        def app(action, side, *args, ok=True):
            return invoke([*cli, action, '--profile', root / side, *args], action, ok)
        def core(side, *args):
            c = cfg[side]
            return invoke([c['executable'], '--control-socket', c['socket'], 'content', *args,
                           '--identity', c['identity'], '--passphrase-file', c['passphrase']], 'ordinary Core')
        owners = {s: core(s,'recipient-key')['identity_public_key_hex'] for s in cfg}
        bundles = {s: app('init',s,'--bundle',root/(s+'.bundle'))['bundle_sha256'] for s in cfg}
        for s,other in [('a','b'),('b','a')]:
            app('pair',s,'--peer-bundle',root/(other+'.bundle'),'--trusted-bundle-sha256',bundles[other],
                '--core-owner-key',owners[other])
        app('pair','a','--peer-bundle',root/'b.bundle','--trusted-bundle-sha256',bundles['b'],
            '--core-owner-key',owners['a'],ok=False)
        # Separate recipient identity authentication above is independent of the invitation.
        check(owners['b'] == cfg['a']['remoteOwner'], 'Core owner and fixture routing')
        eml, original = root/'source.eml', root/'chosen.invitation'
        eml.write_bytes(MIME)
        original.write_bytes(Path(cfg['a']['sendInvitation']).read_bytes())
        id_ = digest(b'outbound-owner-transaction')
        item = root/'a/core-send'/id_
        input_args = ['--id',id_,'--input',eml,'--invitation',original,'--core-owner-key',owners['b']]
        real_cfg = provision/'a/core.json'
        def fetch(name):
            dest = root/name
            report = core('b','mailbox','fetch','--invitation',cfg['b']['receiveInvitation'],'--output-dir',dest)
            check(report['acknowledged_providers_per_message'] == 0 and not report['provider_custody_consumed'], 'non-consuming fetch')
            return list(dest.iterdir())

        # A genuine Core CLI prepares its original cache and signed manifest before
        # encountering the unavailable local agent. No remote custody is reported.
        temporary_cfg = root/'unavailable.json'
        temporary_cfg.write_text(json.dumps({k:(str(root/'absent-agent.sock') if k == 'socket' else cfg['a'][k])
                                             for k in ('executable','socket','identity','passphrase')}))
        app('send','a',*input_args,'--core-config',temporary_cfg,ok=False)
        # Crash-equivalent Core preparation with an insufficient real cache quota.
        c = cfg['a']
        invoke([c['executable'],'--control-socket',c['socket'],'content','mailbox','send','--quota-bytes','1',
                '--owner-key',owners['b'],'--identity',c['identity'],'--passphrase-file',c['passphrase'],
                '--invitation',item/'invitation','--input',item/'packet','--cache',item/'cache',
                '--manifest',item/'manifest'], 'manifest-free preparation', False)
        check((item/'cache').is_dir() and not (item/'manifest').exists(), 'interrupted preparation')
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as unavailable:
            unavailable.bind(str(root/'absent-agent.sock'))
            app('send','a','--resume','--id',id_,'--core-config',temporary_cfg,ok=False)
        packet = (item/'packet').read_bytes()
        manifest = (item/'manifest').read_bytes()
        chunks = {p.name:digest(p.read_bytes()) for p in (item/'cache').iterdir() if len(p.name) == 64}
        check(chunks and MIME not in packet and not fetch('before'), 'no custody before the real agent')
        original_copy = original.read_bytes()
        original.write_bytes(Path(cfg['a']['receiveInvitation']).read_bytes())
        app('send','a',*input_args,'--core-config',real_cfg,ok=False)
        original.write_bytes(original_copy)
        eml.write_bytes(MIME+b'changed')
        app('send','a',*input_args,'--core-config',real_cfg,ok=False)
        eml.unlink()
        original.unlink()

        # Both providers now accept the real deposit. Simulate a failed local
        # custody-record publication after the authenticated remote operation.
        (item/'.custody-next').mkdir()
        resume = ['--id',id_,'--resume','--core-config',real_cfg]
        app('send','a',*resume,ok=False)
        picked = fetch('remote-before-local-receipt')
        check(len(picked) == 1 and (picked[0]/'payload').read_bytes() == packet, 'original remote packet')
        (item/'.custody-next').rmdir()
        success = app('send','a',*resume)
        repeated = app('send','a',*resume)
        check(success == repeated and success['confirmed_providers'] == 2 and success['message_id'] == picked[0].name,
              'same original manifest and two signed provider receipts')
        check(success['packet_sha256'] == digest(packet) and success['message_sha256'] == digest(MIME)
              and success['message_bytes'] == len(MIME), 'original mail and packet')
        check((item/'manifest').read_bytes() == manifest and (item/'invitation').read_bytes() == original_copy and
              chunks == {p.name:digest(p.read_bytes()) for p in (item/'cache').iterdir() if len(p.name) == 64},
              'unchanged Core publication')
        again = fetch('remote-after-resume')
        check([p.name for p in again] == [picked[0].name], 'no duplicate deposit')
        opened = root/'restored.eml'
        app('open','b','--input',again[0]/'payload','--output',opened)
        check(opened.read_bytes() == MIME, 'opened MIME')
        addressed = selected_mail_trial.run(root,node,cfg,owners,bundles,MIME,fetch,core,app)
        return dict(**addressed, two_signed_provider_receipts=True, manifest_free_preparation_recovered=True, source_and_invitation_removed_before_resume=True,
                    original_packet_manifest_chunks_retained=True, distinct_provider_messages=1,
                    original_mime_bytes_restored=True, receiver_acknowledgements=0)
    finally:
        agent.stdin.close()
        check(agent.wait(timeout=15) == 0, 'provider teardown')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--node', type=Path, required=True)
    args = parser.parse_args()
    check([name for _,name in socket.if_nameindex()] == ['lo'], 'isolated loopback-only network required')
    with tempfile.TemporaryDirectory() as temp:
        result = run(Path(temp),args.node.resolve(strict=True),args.fixture.resolve(strict=True))
    native = SOURCE/'integration/signal-mail/node_modules/@signalapp/libsignal-client/prebuilds/linux-x64/@signalapp+libsignal-client.node'
    clean = not subprocess.check_output(['git','-C',SOURCE,'status','--porcelain'])
    result.update(source_tree_clean=clean, temporary_profiles_removed=True, core_cli_sha256=digest((args.fixture.parent/'volparossa').read_bytes()),
                  fixture_sha256=digest(args.fixture.read_bytes()), libsignal_sha256=digest(native.read_bytes()),
                  mail_revision=subprocess.check_output(['git','-C',SOURCE,'rev-parse','HEAD'],text=True).strip(),
                  node_version=subprocess.check_output([args.node,'--version'],text=True).strip())
    print(json.dumps(result,sort_keys=True))
