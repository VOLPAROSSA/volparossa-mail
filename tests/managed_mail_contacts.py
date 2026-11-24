# SPDX-License-Identifier: GPL-3.0-only
"""Pair two fresh native profiles, then use the normal address-bound sender."""
import hashlib
import json
from pathlib import Path
import signal
import subprocess
import sys

SOURCE = Path(__file__).resolve().parents[1]


def run(root, node, cfg, owners, raw, fetch, app):
    ctl = [sys.executable, '-I', '-B', SOURCE / 'integration/peer-mail/contacts.py']
    sender = SOURCE / 'integration/peer-mail/send_selected.py'
    accounts = {'a':'alice@example.invalid', 'b':'bob@example.invalid'}
    profiles = {s:'contact-' + s for s in accounts}
    books = {s:root / ('contacts-' + s) for s in accounts}
    fingerprints = {s:app('init',profiles[s],'--bundle',root / (profiles[s]+'.bundle'))['bundle_sha256']
                    for s in accounts}
    legacy = dict(address='legacy@example.invalid', profile=str(root/'a'), peer_bundle_sha256='0'*64,
                  core_owner_key=owners['b'], invitation=cfg['a']['sendInvitation'],
                  core_config=str(root/'providers/a/core.json'))
    books['a'].mkdir(mode=0o700)
    (books['a']/'address-book.json').write_text(json.dumps({'version':1,'address':accounts['a'],'contacts':[legacy]}))

    def call(cmd, code=0, data=None):
        done = subprocess.run(cmd, input=data, capture_output=True, timeout=30)
        assert done.returncode == code, (Path(cmd[2] if len(cmd)>2 else cmd[0]).name, done.returncode, code)
        return json.loads(done.stdout)

    commands = {}
    for s, peer in [('a','b'),('b','a')]:
        route = root / (s+'-contact-config.json')
        grant = root / (s+'-contact.invitation')
        route.write_text(json.dumps({key:cfg[s][key] for key in ('executable','socket','identity','passphrase')}))
        grant.write_bytes(Path(cfg[s]['sendInvitation']).read_bytes())
        commands[s] = [*ctl, 'pair', '--owner', books[s], '--from', accounts[s], '--address', accounts[peer],
                       '--profile', root/profiles[s], '--peer-bundle', root/(profiles[peer]+'.bundle'),
                       '--trusted-bundle-sha256', fingerprints[peer], '--core-owner-key', owners[peer],
                       '--invitation', grant, '--core-config', route, '--node', node]
    cmd = commands['a']
    def replaced(command, option, value):
        copy = command.copy()
        copy[copy.index(option)+1] = value
        return copy
    before = (books['a']/'address-book.json').read_bytes()
    assert call(replaced(cmd,'--trusted-bundle-sha256','0'*64),1)['status'] == 'signal_fingerprint_mismatch'
    assert call(replaced(cmd,'--invitation',Path(cfg['b']['sendInvitation'])),1)['status'] == 'core_invitation_unverified'
    assert (books['a']/'address-book.json').read_bytes() == before
    # Stop between native pairing and address publication. The old book stays usable.
    interrupted = root/'stop-after-native-contact'
    interrupted.write_text(f"""#!/usr/bin/python3
import os, signal, subprocess, sys
subprocess.run([{str(node)!r}, *sys.argv[1:]], check=True, stdout=subprocess.DEVNULL)
os.kill(os.getppid(), signal.SIGKILL)
""")
    interrupted.chmod(0o700)
    lost = subprocess.run(replaced(cmd,'--node',interrupted),capture_output=True,timeout=45)
    assert lost.returncode == -signal.SIGKILL and (books['a']/'address-book.json').read_bytes()==before
    workers = [subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL) for _ in range(2)]
    states = []
    for worker in workers:
        out,_ = worker.communicate(timeout=45)
        assert worker.returncode == 0
        states.append(json.loads(out)['status'])
    assert sorted(states) == ['already_paired','paired']
    assert call(commands['b'])['status'] == 'paired'
    listed = call([*ctl,'list','--owner',books['a']])
    assert listed['address'] == accounts['a'] and listed['contacts'][-1] == {
        'address':accounts['b'], 'core_owner_key':owners['b'], 'peer_bundle_sha256':fingerprints['b']}
    assert json.loads((books['a']/'address-book.json').read_bytes())['contacts'][0] == legacy
    assert call(replaced(cmd,'--profile',root/'a'),1)['status'] == 'different_contact_already_paired'
    # A contact created by the original setup already points to these files.
    # Register it through the owner command before putting the exchange away.
    book_path = books['a']/'address-book.json'
    book = json.loads(book_path.read_bytes())
    managed = book['contacts'][-1]
    old_entry = {k:v for k,v in managed.items() if k not in ('invitation_hex','core')}
    book['contacts'][-1] = dict(old_entry, invitation=str(root/'a-contact.invitation'),
                                core_config=str(root/'a-contact-config.json'))
    book_path.write_text(json.dumps(book))
    assert call(cmd)['status'] == 'already_paired'
    assert json.loads(book_path.read_bytes())['contacts'] == [legacy, managed]
    for path in (root/'a-contact.invitation',root/'a-contact-config.json'):
        path.unlink()
    operation = hashlib.sha256(b'complete mail after ordinary contact registration').hexdigest()
    selected = [sys.executable,'-I','-B',sender,'--owner',books['a'],'--node',node,'--id',operation]
    sent = call([*selected,'--stdin'],data=raw)
    assert sent['status']=='provider_custody' and sent['confirmed_providers']==2
    messages = fetch('managed-delivery')
    handoff, = [p for p in messages if p.name == sent['message_id']]
    opened = root/'opened-managed.eml'
    app('open',profiles['b'],'--input',handoff/'payload','--output',opened)
    assert opened.read_bytes() == raw
    remove = [*ctl,'remove','--owner',books['a'],'--address',accounts['b'],'--trusted-bundle-sha256',fingerprints['b']]
    assert call(replaced(remove,'--trusted-bundle-sha256','0'*64),1)['status']=='signal_fingerprint_mismatch'
    assert call(remove)['status']=='removed' and call(remove)['status']=='not_paired'
    assert json.loads((books['a']/'address-book.json').read_bytes())['contacts']==[legacy]
    assert call([*selected,'--resume']) == sent
    fresh = replaced(selected,'--id',hashlib.sha256(b'fresh-after-revocation').hexdigest())
    assert call([*fresh,'--stdin'],3,raw)['status']=='ordinary_mail_required'
    restored = replaced(replaced(cmd,'--invitation',Path(cfg['a']['sendInvitation'])),
                        '--core-config',root/'providers/a/core.json')
    assert call(restored)['status']=='paired'
    assert {p.name for p in fetch('managed-no-extra-deposit')} == {p.name for p in messages}
    return dict(native_contacts_verified=True, legacy_registration_survived_exchange_removal=True,
                independent_signal_and_core_mismatches_refused=True,
                concurrent_pairing_idempotent=True, interrupted_contact_pairing_recovered=True, contact_removal_and_repair=True,
                original_selected_send_survived_contact_removal=True, original_mail_opened_with_managed_contact=True)
