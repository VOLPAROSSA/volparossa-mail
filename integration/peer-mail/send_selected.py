#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Route an explicitly selected complete message to one previously authorised owner."""
import argparse
import contextlib
from email import policy
from email.errors import MessageError
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
CONTACT = {'address','profile','peer_bundle_sha256','core_owner_key','invitation','core_config'}
CORE = {'executable','socket','identity','passphrase'}


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
        if msg.defects:
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
    require(0 < len(raw) <= LIMIT)
    return raw


def selected_file(path):
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as handle:
        info = os.fstat(handle.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o022
                and 0 < info.st_size <= LIMIT)
        raw = selected_bytes(handle)
        require(len(raw) == info.st_size)
        return raw


def choose(owner, raw):
    sender, recipient = headers(raw)
    book = json.loads(read(owner/'address-book.json',65536),object_pairs_hook=object_pairs)
    require(set(book)=={'version','address','contacts'} and type(book['version']) is int and book['version']==1
            and isinstance(book['contacts'],list))
    if key(Address(addr_spec=book['address'])) != sender:
        raise Ordinary('sender_not_owner')
    contacts = {}
    for entry in book['contacts']:
        require(isinstance(entry,dict) and set(entry)==CONTACT and all(isinstance(v,str) and v for v in entry.values()))
        address = key(Address(addr_spec=entry['address']))
        require(address not in contacts and HEX.fullmatch(entry['peer_bundle_sha256']) and HEX.fullmatch(entry['core_owner_key']))
        contacts[address] = entry
    if recipient not in contacts:
        raise Ordinary('no_trusted_recipient')
    entry = contacts[recipient]
    profile = Path(entry['profile'])
    private(profile,directory=True)
    cfg = json.loads(read(Path(entry['core_config']),8192),object_pairs_hook=object_pairs)
    require(set(cfg)==CORE or (set(cfg)==CORE|{'sendInvitation','receiveInvitation','remoteOwner'}
                               and cfg['remoteOwner']==entry['core_owner_key']))
    require(all(isinstance(cfg[k],str) and Path(cfg[k]).is_absolute() for k in CORE))
    return str(profile), entry['peer_bundle_sha256'], entry['core_owner_key'], read(Path(entry['invitation']),16384), json.dumps({k:cfg[k] for k in sorted(CORE)})


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
    raw = None
    if args.input:
        raw = selected_file(args.input)
    elif args.stdin:
        raw = selected_bytes(sys.stdin.buffer)
    raw_hash = hashlib.sha256(raw).hexdigest() if raw is not None else None
    with contextlib.closing(db_open(owner)) as db:
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
            paths = [active/'message.eml', active/'invitation', active/'core.json']
            if row['result'] is not None:
                result = json.loads(row['result'])
            else:
                reserved(paths[2], row['config'].encode())
                command = [args.node,'--no-warnings',SIGNAL/'cli.mjs','send','--profile',row['profile'],
                           '--id',op,'--core-config',paths[2],'--core-owner-key',row['owner'],
                           '--peer-bundle-sha256',row['peer']]
                def invoke(*arguments):
                    done = subprocess.run([*command,*arguments],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                                          stderr=subprocess.DEVNULL,pass_fds=(lock.fileno(),),timeout=1280)
                    if done.returncode:
                        raise RuntimeError('selected send pending')
                    reply = json.loads(done.stdout)
                    require(reply['operation']=='send' and reply['message_sha256']==row['digest']
                            and reply['message_bytes']==row['size'] and HEX.fullmatch(reply['packet_sha256']))
                    return reply
                if row['raw'] is not None:
                    reserved(paths[0],row['raw'])
                    reserved(paths[1],row['invitation'])
                    prepared = invoke('--prepare-only','--input',paths[0],'--invitation',paths[1])
                    require(prepared['status']=='prepared')
                    # The native profile owns the exact ciphertext and invitation
                    # now. Drop the extra draft before waiting on any provider.
                    with db:
                        db.execute('UPDATE selections SET raw=NULL,invitation=NULL WHERE id=?',(op,))
                for path in paths[:2]:
                    path.unlink(missing_ok=True)
                flush_directory(active)
                result = invoke('--resume')
                require(result['confirmed_providers']==2 and HEX.fullmatch(result['message_id']))
                result.update(operation='send_selected',status='provider_custody')
                with db:
                    db.execute('UPDATE selections SET result=?,raw=NULL,invitation=NULL,config=NULL WHERE id=?',
                               (json.dumps(result),op))
            for path in paths:
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
        print(json.dumps(send(args)))
    except Ordinary as decision:
        print(json.dumps({'operation':'send_selected','status':'ordinary_mail_required','reason':decision.args[0]}))
        return 3
    except RuntimeError:
        print(json.dumps({'operation':'send_selected','status':'retry_selected_send'}))
        return 1
    except (AttributeError, OSError, ValueError, TypeError, KeyError, MessageError, sqlite3.Error, subprocess.SubprocessError):
        print(json.dumps({'operation':'send_selected','status':'invalid_selection_or_state'}))
        return 1
    return 0


if __name__=='__main__':
    sys.exit(main())
