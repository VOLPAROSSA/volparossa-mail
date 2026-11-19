#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Verify optional first-owner IMAPS on an existing Stalwart binary inside private loopback."""
import argparse
import hashlib
import imaplib
import json
import os
from pathlib import Path
import secrets
import signal
import smtplib
import socket
import ssl
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
LIB = Path('/usr/local/libexec/volparossa-mail')
STATE = Path('/var/lib/volparossa-mail')
OWNER = 'owner@example.test'
MESSAGE = b'From: visitor@example.test\r\nTo: owner@example.test\r\nMessage-ID: <imap@example.test>\r\nSubject: Owner IMAPS\r\n\r\nOriginal body.\r\n'


def check(value, phase):
    if not value:
        raise ValueError(phase)


def openssl(*args):
    subprocess.run(['openssl',*args],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL,timeout=15,check=True)


def issue(directory, name, ip):
    cert, key = directory / (name+'.crt'), directory / (name+'.key')
    openssl('req','-x509','-newkey','rsa:2048','-noenc','-days','2','-subj','/CN=owner.example.test',
            '-addext','basicConstraints=critical,CA:FALSE','-addext','subjectAltName=IP:'+ip,
            '-addext','extendedKeyUsage=serverAuth','-keyout',key,'-out',cert)
    return cert,key


def inside():
    check(os.geteuid() != 0 and socket.if_nameindex() == [(1,'lo')], 'isolated unprivileged worker')
    os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[:2])
    os.umask(0o077)
    password = 'pässword-' + secrets.token_hex(16)
    def initialize(*args, ok=False):
        command = [sys.executable,'-B',LIB/'initialize-owner.py',OWNER,*args]
        result = subprocess.run(command,input=(password+'\n').encode(),capture_output=True,timeout=100)
        check((result.returncode == 0) == ok, 'first-owner outcome')
        check(password.encode() not in result.stderr and b'PRIVATE KEY-----' not in result.stderr, 'private output')
        if not ok:
            check(not list(STATE.iterdir()), 'rejected initializer published store data')

    with tempfile.TemporaryDirectory(dir='/tmp') as temp:
        temp = Path(temp)
        cert,key = issue(temp,'local','127.0.0.1')
        wrong,otherkey = issue(temp,'other','127.0.0.2')
        initialize('--imap-cert',cert)
        initialize('--imap-key',key)
        key.chmod(0o644)
        initialize('--imap-cert',cert,'--imap-key',key)
        key.chmod(0o600)
        initialize('--imap-cert',cert,'--imap-key',otherkey)
        initialize('--imap-cert',wrong,'--imap-key',otherkey)
        initialize('--imap-cert',cert,'--imap-key',key,ok=True)
        check([p.name for p in STATE.iterdir()] == ['store.sqlite'],'single published store')
        trusted = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        trusted.load_verify_locations(cafile=cert)
        trusted.minimum_version = ssl.TLSVersion.TLSv1_2
        leaf = ssl.PEM_cert_to_DER_cert(cert.read_text())
        cert.unlink()
        key.unlink()
        ingress = {}
        def login(client, supplied):
            return client.authenticate('PLAIN',lambda _: b'\0'+OWNER.encode()+b'\0'+supplied.encode())
        def serve(deliver):
            proc = subprocess.Popen([LIB/'stalwart','--config',LIB/'store.json'],cwd=STATE,
                                    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                                    env={'PATH':'/usr/bin:/bin','STALWART_HOSTNAME':'example.test',
                                         'STALWART_PUBLIC_URL':'http://127.0.0.1:8080'})
            try:
                deadline = time.monotonic()+60
                while True:
                    try:
                        client = imaplib.IMAP4_SSL('127.0.0.1',2993,ssl_context=trusted,timeout=4)
                        break
                    except ConnectionRefusedError:
                        check(proc.poll() is None and time.monotonic()<deadline,'normal TLS readiness')
                        time.sleep(.1)
                with client:
                    check(client.sock.getpeercert(binary_form=True)==leaf,'owner leaf')
                    if deliver:
                        try:
                            with imaplib.IMAP4_SSL('127.0.0.1',2993,ssl_context=ssl.create_default_context(),timeout=5):
                                pass
                        except ssl.SSLCertVerificationError:
                            pass
                        else:
                            raise ValueError('unknown certificate accepted')
                        with imaplib.IMAP4_SSL('127.0.0.1',2993,ssl_context=trusted,timeout=5) as wrong_user:
                            try:
                                login(wrong_user,secrets.token_hex(16))
                            except imaplib.IMAP4.error:
                                pass
                            else:
                                raise ValueError('wrong mailbox password accepted')
                        with smtplib.SMTP('127.0.0.1',2525,local_hostname='sender.example.test',timeout=8) as smtp:
                            check(smtp.ehlo()[0] == 250 and smtp.mail('visitor@example.test')[0] == 250
                                  and smtp.rcpt(OWNER)[0] == 250, 'local SMTP envelope')
                            # Stalwart processes the message before replying to DATA. Give this
                            # single submission the remaining trial budget, including the final 250.
                            started = time.monotonic()
                            remaining = deadline - started
                            check(remaining > 0, 'SMTP submission deadline')
                            smtp.sock.settimeout(remaining)
                            check(smtp.data(MESSAGE)[0] == 250, 'SMTP DATA confirmation')
                            ingress['smtp_data_ms'] = round((time.monotonic() - started) * 1000)
                    login(client,password)
                    while True:
                        status, data = client.select('INBOX',readonly=True)
                        if status=='OK' and data==[b'1']:
                            break
                        check(time.monotonic()<deadline,'one Inbox item')
                        time.sleep(.1)
                    status, data = client.fetch('1','(BODY.PEEK[])')
                    check(status=='OK' and any(isinstance(part,tuple) and part[1].endswith(MESSAGE) for part in data),
                          'full received MIME read over authenticated TLS')
                if deliver:
                    # Stalwart can close a session that issues an unauthorised
                    # command. Check the mailbox state independently afterwards.
                    limited = imaplib.IMAP4_SSL('127.0.0.1',2993,ssl_context=trusted,timeout=5)
                    try:
                        login(limited,password)
                        try:
                            status, _ = limited.create('forbidden-write')
                        except imaplib.IMAP4.abort:
                            status = 'DISCONNECTED'
                        check(status in ('NO','DISCONNECTED'),'IMAP write accepted')
                    finally:
                        limited.shutdown()
                with imaplib.IMAP4_SSL('127.0.0.1',2993,ssl_context=trusted,timeout=5) as verify:
                    login(verify,password)
                    status, entries = verify.list('""','forbidden-write')
                    check(status=='OK' and not any(entries),'denied IMAP mailbox creation took effect')
            finally:
                proc.send_signal(signal.SIGINT)
                proc.wait(timeout=20)
                check(proc.returncode==0,'clean Stalwart shutdown')
        serve(True)
        serve(False)
    print(json.dumps(dict(implicit_tls=True,loopback_port=2993,wrong_hostname_rejected=True,
                          mismatched_key_rejected=True,incomplete_pair_rejected=True,exposed_private_key_rejected=True,
                          unknown_certificate_rejected=True,wrong_password_rejected=True,imap_write_refused=True,
                          owner_supplied_leaf_retained=True,original_message_read_after_restart=True, **ingress),sort_keys=True))


def trial(binary):
    binary = binary.resolve(strict=True)
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp)/'state'
        state.mkdir(mode=0o700)
        command = ['bwrap','--die-with-parent','--unshare-all','--cap-drop','ALL','--clearenv',
                   '--ro-bind','/usr','/usr','--ro-bind','/etc','/etc','--proc','/proc','--dev','/dev',
                   '--tmpfs','/tmp','--dir','/run','--tmpfs','/usr/local']
        for name in ('bin','lib','lib64','sbin'):
            command += ['--ro-bind','/'+name,'/'+name]
        for source,destination in [(binary,LIB/'stalwart'),(ROOT/'packaging/systemd/store.json',LIB/'store.json'),
                                   (ROOT/'packaging/systemd/initialize-owner.py',LIB/'initialize-owner.py'),
                                   (Path(__file__).resolve(),Path('/trial/test.py'))]:
            command += ['--ro-bind',source,destination]
        command += ['--bind',state,STATE,'--setenv','PATH','/usr/bin:/bin','--setenv','LANG','C.UTF-8',
                    '--','/usr/bin/python3','-B','/trial/test.py','--inside']
        output = subprocess.check_output(command,timeout=280)
        result = json.loads(output)
    with binary.open('rb') as handle:
        digest = hashlib.sha256()
        for block in iter(lambda:handle.read(1024*1024),b''):
            digest.update(block)
    print(json.dumps(dict(result,server_sha256=digest.hexdigest(),private_state_removed=True),sort_keys=True))


if __name__=='__main__':
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--server',type=Path)
    mode.add_argument('--inside',action='store_true',help=argparse.SUPPRESS)
    args = parser.parse_args()
    inside() if args.inside else trial(args.server)
