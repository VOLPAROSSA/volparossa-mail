# SPDX-License-Identifier: GPL-3.0-only
"""Exercise durable address selection against the established real two-owner fixture."""
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

SOURCE = Path(__file__).resolve().parents[1]


def check(value, phase):
    if not value:
        raise ValueError(phase)


def run(root, node, cfg, owners, bundles, raw, fetch, core, app):
    owner = root/'address-selection'
    owner.mkdir(mode=0o700)
    book = owner/'address-book.json'
    chosen_cfg, chosen_invite = owner/'selected-core.json',owner/'selected-invitation'
    chosen_cfg.write_text(json.dumps(cfg['a']))
    chosen_invite.write_bytes(Path(cfg['a']['sendInvitation']).read_bytes())
    recipient = 'bob@example.invalid'
    def contact(side,other,invitation,config):
        return dict(address=recipient,profile=str(root/side),peer_bundle_sha256=bundles[other],
                    core_owner_key=owners[other],invitation=str(invitation),core_config=str(config))
    original = contact('a','b',chosen_invite,chosen_cfg)
    def configure(entry):
        book.write_text(json.dumps(dict(version=1,address='alice@example.invalid',contacts=[entry])))
    configure(original)
    op=hashlib.sha256(b'address-selected-draft-1').hexdigest()
    draft=root/'selected-draft.eml'
    command=[sys.executable,'-B',SOURCE/'integration/peer-mail/send_selected.py','--node',node,
             '--owner',owner,'--id',op]
    def action(*args,code):
        done=subprocess.run([*command,*args],capture_output=True,timeout=90)
        report=json.loads(done.stdout)
        check(done.returncode==code and not done.stderr, 'address action result')
        return report
    for old,new,reason in [
        (b'alice@example.invalid',b'other@example.invalid','sender_not_owner'),
        (b'bob@example.invalid',b'external@example.invalid','no_trusted_recipient'),
        (b'To: bob@example.invalid',b'To: bob@example.invalid\r\nBcc: other@example.invalid','single_recipient_required')
    ]:
        draft.write_bytes(raw.replace(old,new))
        decision=action('--input',draft,code=3)
        check(decision['status']=='ordinary_mail_required' and decision['reason']==reason,'explicit ordinary mail decision')
    check(not (root/'a/core-send'/op).exists(),'ordinary route made a private deposit')
    # Hold local publication AFTER the two providers have accepted this original
    # native envelope, so the following invocation must resume a real send.
    stage=root/'a/core-send'/op
    stage.mkdir(mode=0o700)
    (stage/'.custody-next').mkdir()
    draft.write_bytes(raw)
    attempted=action('--input',draft,code=1)
    check(attempted['status']=='retry_selected_send','private route error offered another route')
    packet=(stage/'packet').read_bytes()
    with sqlite3.connect(owner/'selected-mail.sqlite') as db:
        snapshot=db.execute('SELECT raw,invitation,peer,owner,result FROM selections WHERE id=?',(op,)).fetchone()
    check(snapshot==(None,None,bundles['b'],owners['b'],None),'original draft retired after native sealing')
    check(not (owner/'selected-sends'/op/'message.eml').exists(),'cleartext draft still staged')
    configure(contact('b','a',cfg['b']['sendInvitation'],root/'providers/b/core.json'))
    chosen_cfg.write_text(json.dumps(cfg['b']))
    chosen_invite.unlink()
    draft.unlink()
    (stage/'.custody-next').rmdir()
    delivered=action('--resume',code=0)
    check(delivered['status']=='provider_custody' and delivered['confirmed_providers']==2,'original addressed custody')
    pending=fetch('after-address-selection')
    original=[p for p in pending if p.name==delivered['message_id']]
    check(len(original)==1 and (original[0]/'payload').read_bytes()==packet,'recipient changed after restart')
    check(core('a','mailbox','fetch','--invitation',cfg['a']['receiveInvitation'],'--output-dir',
               root/'other-owner-empty')['messages_fetched']==0,'replaced catalog redirected mail')
    restored=root/'selected-restored.eml'
    app('open','b','--input',original[0]/'payload','--output',restored)
    check(restored.read_bytes()==raw,'selected original MIME changed')
    book.unlink()
    check(action('--resume',code=0)==delivered,'completed receipt requires a mutable address book')
    draft.write_bytes(raw+b'changed')
    check(action('--input',draft,code=1)['status']=='invalid_selection_or_state','conflicting operation id reused')
    return dict(address_selection_verified=True, ordinary_mail_decisions_without_send=3,
                original_peer_retained_after_catalog_replacement=True, selected_message_restored=True)
