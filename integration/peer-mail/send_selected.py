#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Route an explicitly selected complete message to one previously authorised owner."""
import argparse
import contextlib
from email import policy
from email.errors import MessageError, MultipartInvariantViolationDefect
from email.headerregistry import Address
from email.parser import BytesHeaderParser
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import sys

SIGNAL = Path(__file__).resolve().parents[1] / 'signal-mail'
sys.path.insert(0, str(SIGNAL))
from deposit import HEX, flush_directory, owned, private, read  # noqa: E402

LIMIT = 4 * 1024 * 1024
BOOK_LIMIT = 1024 * 1024
IDENTITY = {'address','profile','peer_bundle_sha256','core_owner_key'}
CONTACT = IDENTITY | {'invitation','core_config'}
STORED_CONTACT = IDENTITY | {'invitation_hex','core'}
CORE = {'executable','socket','identity','passphrase'}


class MessageTooLarge(ValueError):
    def result(self):
        return {'operation':'send_selected','status':'message_too_large','reason':self.args[0],'limit_bytes':LIMIT}


class Ordinary(Exception):
    pass


def require(condition):
    if not condition:
        raise ValueError('invalid owner selection or retained state')


def key(address):
    if not isinstance(address, Address) or not address.username or not address.domain:
        raise ValueError('expected a full individual address')
    # A mailbox local part can be case-sensitive. Do not infer equivalence
    # from aliases, display names, tags or Unicode-to-ASCII transliteration.
    return address.username, address.domain.lower()


def headers(raw):
    try:
        msg = BytesHeaderParser(policy=policy.default).parsebytes(raw)
        # Python 3.10 reports the intentionally unparsed multipart body as a
        # defect in headers-only mode. Every actual header defect still counts.
        if any(not isinstance(defect, MultipartInvariantViolationDefect) for defect in msg.defects):
            raise Ordinary('invalid_headers')
        if any(name.lower().startswith('resent-') for name in msg.keys()):
            raise Ordinary('resent_headers')
        def addrs(name):
            values = msg.get_all(name, [])
            if len(values) > 1:
                raise Ordinary('ambiguous_headers')
            if not values:
                return []
            value, = values
            if value.defects or any(g.display_name is not None for g in value.groups):
                raise Ordinary('ambiguous_address')
            return [key(a) for a in value.addresses]
        sender = addrs('From')
        if len(sender) != 1:
            raise Ordinary('invalid_sender')
        delegated = addrs('Sender')
        if delegated and delegated != sender:
            raise Ordinary('different_sender')
        recipients = addrs('To') + addrs('Cc') + addrs('Bcc')
        if len(recipients) != 1:
            raise Ordinary('single_recipient_required')
        return sender[0], recipients[0]
    except (AttributeError, IndexError, TypeError, ValueError, MessageError):
        raise Ordinary('ambiguous_address') from None


def object_pairs(pairs):
    obj = {}
    for name, value in pairs:
        require(name not in obj)
        obj[name] = value
    return obj


def selected_bytes(stream):
    raw = stream.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise MessageTooLarge('input_exceeds_limit')
    require(raw)
    return raw


def selected_file(path):
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as handle:
        info = os.fstat(handle.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o022
                and info.st_size > 0)
        if info.st_size > LIMIT:
            raise MessageTooLarge('input_exceeds_limit')
        raw = selected_bytes(handle)
        require(len(raw) == info.st_size)
        return raw


def address_book(owner):
    path = owner/'address-book.json'
    book = json.loads(read(path, BOOK_LIMIT), object_pairs_hook=object_pairs)
    require(isinstance(book,dict) and set(book)=={'version','address','contacts'}
            and type(book['version']) is int and book['version']==1 and isinstance(book['contacts'],list))
    local = key(Address(addr_spec=book['address']))
    contacts = {}
    for entry in book['contacts']:
        require(isinstance(entry,dict) and set(entry) in (CONTACT, STORED_CONTACT)
                and all(isinstance(entry[k],str) and entry[k] for k in IDENTITY))
        if set(entry) == CONTACT:
            require(all(isinstance(entry[k],str) and entry[k] for k in ('invitation','core_config')))
        else:
            require(isinstance(entry['core'],dict) and isinstance(entry['invitation_hex'],str))
        address = key(Address(addr_spec=entry['address']))
        require(address not in contacts and HEX.fullmatch(entry['peer_bundle_sha256']) and HEX.fullmatch(entry['core_owner_key']))
        contacts[address] = entry
    return book, local, contacts


def core_settings(cfg, remote_owner):
    require(isinstance(cfg,dict) and (set(cfg)==CORE or
            (set(cfg)==CORE|{'sendInvitation','receiveInvitation','remoteOwner'} and cfg['remoteOwner']==remote_owner)))
    require(all(isinstance(cfg[k],str) and Path(cfg[k]).is_absolute() for k in CORE))
    return {k:cfg[k] for k in sorted(CORE)}


def contact_route(entry):
    if 'invitation_hex' in entry:
        encoded = entry['invitation_hex']
        require(isinstance(encoded,str) and 0 < len(encoded) <= 32768)
        invitation = bytes.fromhex(encoded)
        require(encoded == invitation.hex())
        cfg = entry['core']
    else:
        invitation = read(Path(entry['invitation']), 16384)
        cfg = json.loads(read(Path(entry['core_config']),8192), object_pairs_hook=object_pairs)
    return invitation, core_settings(cfg,entry['core_owner_key'])


def choose(owner, raw):
    sender, recipient = headers(raw)
    if not os.path.lexists(owner/'address-book.json'):
        raise Ordinary('no_trusted_recipient')
    _, local, contacts = address_book(owner)
    if local != sender:
        raise Ordinary('sender_not_owner')
    if recipient not in contacts:
        raise Ordinary('no_trusted_recipient')
    entry = contacts[recipient]
    profile = Path(entry['profile'])
    private(profile,directory=True)
    invitation, cfg = contact_route(entry)
    return str(profile), entry['peer_bundle_sha256'], entry['core_owner_key'], invitation, json.dumps(cfg)


def db_open(owner):
    path = owner/'selected-mail.sqlite'
    with os.fdopen(os.open(path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK,0o600),'rb') as fd:
        owned(os.fstat(fd.fileno()))
    db = sqlite3.connect(path,timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA synchronous=FULL')
    db.execute('PRAGMA secure_delete=ON')
    db.execute('''CREATE TABLE IF NOT EXISTS selections (id TEXT PRIMARY KEY, digest TEXT NOT NULL,
                 size INTEGER NOT NULL, profile TEXT NOT NULL, peer TEXT NOT NULL, owner TEXT NOT NULL, raw BLOB,
                 invitation BLOB, config TEXT, result TEXT) WITHOUT ROWID''')
    return db


def reserved(path, data):
    # The only writer holds this operation's exclusive file lock. Unlinking the
    # reserved name never follows it, and O_EXCL prevents replacing another file.
    path.unlink(missing_ok=True)
    with os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600),'wb') as output:
        output.write(data)


def send(args):
    owner, op = args.owner.absolute(), args.id
    private(owner,directory=True)
    require(HEX.fullmatch(op))
    with contextlib.closing(db_open(owner)) as db:
        raw = None
        try:
            if args.input:
                raw = selected_file(args.input)
            elif args.stdin:
                raw = selected_bytes(sys.stdin.buffer)
        except MessageTooLarge:
            # A larger replacement for an already selected ID is a conflict.
            require(db.execute('SELECT 1 FROM selections WHERE id=?',(op,)).fetchone() is None)
            raise
        raw_hash = hashlib.sha256(raw).hexdigest() if raw is not None else None
        with db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM selections WHERE id=?',(op,)).fetchone()
            if row is None:
                if raw is None:
                    raise ValueError('unknown operation')
                profile, peer, core_owner, invite, cfg = choose(owner,raw)
                db.execute('INSERT INTO selections VALUES (?,?,?,?,?,?,?,?,?,NULL)',
                           (op,raw_hash,len(raw),profile,peer,core_owner,raw,invite,cfg))
            elif raw_hash is not None:
                require(raw_hash==row['digest'])
        del raw
        flush_directory(owner)
        # Only the chosen mail occupies its lock while Core is waiting for providers.
        spool = owner/'selected-sends'
        for directory in (spool,spool/op):
            directory.mkdir(mode=0o700,exist_ok=True)
            private(directory,directory=True)
        active = spool/op
        with os.fdopen(os.open(active/'.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600),'rb') as lock:
            owned(os.fstat(lock.fileno()))
            fcntl.flock(lock,fcntl.LOCK_EX)
            row = db.execute('SELECT * FROM selections WHERE id=?',(op,)).fetchone()
            invitation, config = active/'invitation', active/'core.json'
            # Older versions staged a named draft here. The durable selection
            # owns its original bytes until the native seal has committed.
            (active/'message.eml').unlink(missing_ok=True)
            if row['result'] is not None:
                result = json.loads(row['result'])
            else:
                reserved(config, row['config'].encode())
                command = [args.node,'--no-warnings',SIGNAL/'cli.mjs','send','--profile',row['profile'],
                           '--id',op,'--core-config',config,'--core-owner-key',row['owner'],
                           '--peer-bundle-sha256',row['peer']]
                expected_digest, expected_size = row['digest'], row['size']
                def invoke(*arguments, body=None):
                    done = subprocess.run([*command,*arguments],input=body,
                                          stdin=subprocess.DEVNULL if body is None else None,stdout=subprocess.PIPE,
                                          stderr=subprocess.DEVNULL,pass_fds=(lock.fileno(),),timeout=1280)
                    if done.returncode == 4 and body is not None:
                        refusal = MessageTooLarge('encrypted_packet_exceeds_limit')
                        require(json.loads(done.stdout) == dict(refusal.result(), operation='send'))
                        raise refusal
                    if done.returncode:
                        raise RuntimeError('selected send pending')
                    reply = json.loads(done.stdout)
                    require(reply['operation']=='send' and reply['message_sha256']==expected_digest
                            and reply['message_bytes']==expected_size and HEX.fullmatch(reply['packet_sha256']))
                    return reply
                try:
                    if row['raw'] is not None:
                        reserved(invitation,row['invitation'])
                        prepared = invoke('--prepare-only','--stdin-size',str(expected_size),'--stdin-sha256',expected_digest,
                                          '--invitation',invitation,body=row['raw'])
                        require(prepared['status']=='prepared')
                        # The native profile owns the exact ciphertext and invitation
                        # now. Drop the extra draft before waiting on any provider.
                        with db:
                            db.execute('UPDATE selections SET raw=NULL,invitation=NULL WHERE id=?',(op,))
                    del row
                    invitation.unlink(missing_ok=True)
                    flush_directory(active)
                    result = invoke('--resume')
                    require(result['confirmed_providers']==2 and HEX.fullmatch(result['message_id']))
                    result.update(operation='send_selected',status='provider_custody')
                except MessageTooLarge as refusal:
                    result = refusal.result()
                with db:
                    db.execute('UPDATE selections SET result=?,raw=NULL,invitation=NULL,config=NULL WHERE id=?',
                               (json.dumps(result),op))
            for path in (invitation, config):
                path.unlink(missing_ok=True)
            flush_directory(active)
    return result


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owner',required=True,type=Path)
    parser.add_argument('--id',required=True)
    parser.add_argument('--node',type=Path,default=shutil.which('node'))
    source=parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--input',type=Path)
    source.add_argument('--stdin',action='store_true',help='read the complete RFC 5322 message as binary stdin')
    source.add_argument('--resume',action='store_true')
    args=parser.parse_args()
    try:
        require(args.node is not None)
        result = send(args)
        print(json.dumps(result))
        return 4 if result['status']=='message_too_large' else 0
    except MessageTooLarge as refusal:
        print(json.dumps(refusal.result()))
        return 4
    except Ordinary as decision:
        print(json.dumps({'operation':'send_selected','status':'ordinary_mail_required','reason':decision.args[0]}))
        return 3
    except RuntimeError:
        print(json.dumps({'operation':'send_selected','status':'retry_selected_send'}))
        return 1
    except (AttributeError, OSError, ValueError, TypeError, KeyError, MessageError, sqlite3.Error, subprocess.SubprocessError):
        print(json.dumps({'operation':'send_selected','status':'invalid_selection_or_state'}))
        return 1


if __name__=='__main__':
    sys.exit(main())
