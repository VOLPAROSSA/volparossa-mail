#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Send one retained Signal packet through the ordinary local Core mailbox CLI."""
import fcntl
import hashlib
import json
from itertools import islice
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

HEX = re.compile(r'[0-9a-f]{64}\Z')
CACHE_FILE = re.compile(r'(?:[0-9a-f]{64}|\.volparossa-(?:owner|index|chunk-next|index-next|replicas|mailbox)-v1)\Z')


def require(value):
    if not value:
        raise ValueError('incomplete Core custody')


def owned(info, directory=False):
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    require(kind(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077
            and (directory or info.st_nlink == 1))


def private(path, directory=False):
    require(path.is_absolute() and path == path.resolve())
    owned(path.lstat(), directory)


def read(path, maximum):
    require(path.is_absolute() and path.parent == path.parent.resolve())
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as handle:
        info = os.fstat(handle.fileno())
        owned(info)
        require(0 < info.st_size <= maximum)
        data = handle.read(maximum + 1)
        require(len(data) == info.st_size)
        return data


def flush_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def clear_unpublished(cache):
    # Core creates its cache before its atomic manifest and deposits only after
    # that manifest is durable. Existing manifests always take the resume path.
    private(cache, directory=True)
    entries = list(islice(cache.iterdir(), 257))
    require(len(entries) <= 256)
    for entry in entries:
        require(CACHE_FILE.fullmatch(entry.name))
        private(entry)
    for entry in entries:
        entry.unlink()
    cache.rmdir()
    flush_directory(cache.parent)


def receipt(report):
    require(report['operation'] == 'mailbox_send' and report['confirmed_providers'] == 2
            and report['plaintext_uploaded'] is False and HEX.fullmatch(report['message_id'])
            and report['retained_providers'] + report['already_acknowledged_providers'] == 2)
    signed = report['storage_receipts']
    require(isinstance(signed, list) and len(signed) == 2 and len(set(signed)) == 2
            and all(isinstance(s, str) and 0 < len(bytes.fromhex(s)) <= 16384 for s in signed))
    return {'message_id': report['message_id'], 'confirmed_providers': 2}


def send(directory, config_file, owner):
    private(directory, directory=True)
    require(HEX.fullmatch(owner))
    manifest, cache = directory / 'manifest', directory / 'cache'
    saved = directory / 'custody.json'
    lock = directory / '.lock'
    with os.fdopen(os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600), 'rb') as guard:
        private(lock)
        fcntl.flock(guard, fcntl.LOCK_EX)
        if os.path.lexists(saved):
            retained = json.loads(read(saved, 65536))
            require(retained['manifest_sha256'] == hashlib.sha256(read(manifest, 4096)).hexdigest())
            return receipt(retained['core'])
        cfg = json.loads(read(config_file, 8192))
        base = {'executable', 'socket', 'identity', 'passphrase'}
        require(set(cfg) == base or (set(cfg) == base | {'sendInvitation', 'receiveInvitation', 'remoteOwner'}
                                     and cfg['remoteOwner'] == owner))
        exe, socket, identity, password = (Path(cfg[k]) for k in ('executable', 'socket', 'identity', 'passphrase'))
        require(all(p.is_absolute() and p.resolve() == p for p in (exe, socket, identity, password)))
        binary = exe.stat()
        require(stat.S_ISREG(binary.st_mode) and binary.st_uid in (0, os.getuid())
                and binary.st_mode & 0o111 and not binary.st_mode & 0o022 and stat.S_ISSOCK(socket.stat().st_mode))
        private(identity)
        private(password)
        if os.path.lexists(manifest):
            private(manifest)
            mode = ['--resume']
        else:
            if os.path.lexists(cache):
                clear_unpublished(cache)
            mode = ['--input', directory / 'packet']
        command = [exe, '--control-socket', socket, 'content', 'mailbox', 'send', *mode, '--cache', cache,
                   '--manifest', manifest, '--invitation', directory / 'invitation', '--owner-key', owner,
                   '--identity', identity, '--passphrase-file', password]
        # A killed caller cannot race an orphaned Core process on its cache.
        # The lock remains held until the process using that cache also exits.
        with tempfile.TemporaryFile(dir=directory) as out:
            subprocess.run(command, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.DEVNULL,
                           pass_fds=(guard.fileno(),), check=True, timeout=1250)
            require(0 < out.tell() <= 65536)
            out.seek(0)
            report = json.load(out)
        result = receipt(report)
        require(report['manifest'] == str(manifest) and report['cache'] == str(cache))
        retained = {'manifest_sha256': hashlib.sha256(read(manifest, 4096)).hexdigest(), 'core': report}
        staging = directory / '.custody-next'
        if os.path.lexists(staging):
            private(staging)
            staging.unlink()
        with os.fdopen(os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'wb') as out:
            out.write(json.dumps(retained).encode())
            out.flush()
            os.fsync(out.fileno())
        os.replace(staging, saved)
        flush_directory(directory)
        return result


if __name__ == '__main__':
    try:
        os.umask(0o077)
        directory, config, owner = sys.argv[1:]
        print(json.dumps(send(Path(directory), Path(config).absolute(), owner)))
    except (AttributeError, KeyError, OSError, TypeError, ValueError, subprocess.SubprocessError):
        sys.exit('Core custody incomplete')
