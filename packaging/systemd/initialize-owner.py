#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Create an owner-local Stalwart mailbox from a private loopback namespace."""

import argparse
import base64
import contextlib
import fcntl
import getpass
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time

STATE = Path("/var/lib/volparossa-mail")
LIB = Path("/usr/local/libexec/volparossa-mail")
MAIL = "urn:ietf:params:jmap:mail"
ADMIN = "setup-admin"


def address_parts(address):
    local, separator, domain = address.partition("@")
    label = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    if (not separator or len(local) > 64 or len(address) > 254
            or not re.fullmatch(r"[a-z0-9_+-]+(?:\.[a-z0-9_+-]+)*", local)
            or not re.fullmatch(rf"{label}(?:\.{label})*", domain)):
        raise ValueError("use a lowercase mailbox address with an ASCII domain")
    return local, domain


def request(user, password, method=None, arguments=None):
    token = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
    headers = {"Authorization": "Basic " + token, "Content-Type": "application/json"}
    with contextlib.closing(http.client.HTTPConnection("127.0.0.1", 8080, timeout=8)) as connection:
        if method:
            body = {"using": ["urn:ietf:params:jmap:core", MAIL, "urn:stalwart:jmap"],
                    "methodCalls": [[method, arguments, "setup"]]}
            connection.request("POST", "/jmap", json.dumps(body), headers)
        else:
            connection.request("GET", "/jmap/session", headers=headers)
        response = connection.getresponse()
        raw = response.read(1024 * 1024 + 1)
        if response.status != 200 or len(raw) > 1024 * 1024:
            raise ValueError("Stalwart did not accept the setup request")
        data = json.loads(raw)
    if method is None:
        return data
    replies = data.get("methodResponses", [])
    if len(replies) != 1 or len(replies[0]) != 3 or replies[0][0] != method or replies[0][2] != "setup":
        raise ValueError("Stalwart rejected " + method)
    return replies[0][1]


def create(admin_secret, method, items):
    result = request(ADMIN, admin_secret, method + "/set", {"create": items})
    created = result.get("created", {})
    if result.get("notCreated") or set(created) != set(items):
        raise ValueError("Stalwart did not create " + method)
    return created


@contextlib.contextmanager
def server(directory, domain, user, password, recovery=False):
    env = {"PATH": "/usr/bin:/bin", "STALWART_HOSTNAME": domain}
    if recovery:
        env.update(STALWART_RECOVERY_MODE="1", STALWART_RECOVERY_ADMIN=ADMIN + ":" + password)
    with subprocess.Popen([LIB / "stalwart", "--config", LIB / "store.json"], cwd=directory, env=env,
                          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as proc:
        try:
            deadline = time.monotonic() + 60
            while True:
                if proc.poll() is not None:
                    raise ValueError("Stalwart stopped during initialization")
                try:
                    session = request(user, password)
                    break
                except (ConnectionRefusedError, ConnectionResetError, http.client.RemoteDisconnected):
                    if time.monotonic() >= deadline:
                        raise ValueError("Stalwart setup listener did not become ready") from None
                    time.sleep(0.15)
            yield session
        finally:
            if proc.poll() is None:
                proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                raise ValueError("Stalwart did not finish writing the mailbox") from None
            if proc.returncode != 0:
                raise ValueError("Stalwart did not shut down cleanly")


def initialize(address, password):
    local, domain = address_parts(address)
    if (not password or len(password.encode()) > 1024
            or any(ord(char) < 32 or ord(char) == 127 for char in password)):
        raise ValueError("supply a nonempty single-line mailbox password")
    if os.geteuid() == 0 or socket.if_nameindex() != [(1, "lo")]:
        raise ValueError("run under the service account in a private loopback namespace")
    meta = STATE.lstat()
    if not stat.S_ISDIR(meta.st_mode) or stat.S_IMODE(meta.st_mode) != 0o700 or meta.st_uid != os.geteuid():
        raise ValueError("a private state directory owned by the service account is required")
    os.umask(0o077)
    with contextlib.ExitStack() as cleanup:
        descriptor = os.open(STATE, os.O_DIRECTORY | os.O_NOFOLLOW)
        cleanup.callback(os.close, descriptor)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        dest = STATE / "store.sqlite"
        if any(STATE.glob("store.sqlite*")):
            raise ValueError("the state directory already contains a mailbox or SQLite sidecar")
        stage = Path(cleanup.enter_context(tempfile.TemporaryDirectory(prefix=".create-", dir=STATE)))
        admin_secret = secrets.token_urlsafe(40)
        with server(stage, domain, ADMIN, admin_secret, recovery=True):
            manual = {"@type": "Manual"}
            domain_object = {
                "name": domain, "isEnabled": True, "certificateManagement": manual,
                "dnsManagement": manual, "dkimManagement": manual,
            }
            domain_id = create(admin_secret, "x:Domain", {"domain": domain_object})["domain"]["id"]
            permissions = ("authenticate", "emailReceive", "jmapMailboxGet", "jmapMailboxCreate",
                           "jmapEmailGet", "jmapEmailQuery", "jmapEmailImport", "jmapBlobGet", "jmapBlobUpload")
            owner = {
                "@type": "User", "name": local, "domainId": domain_id, "roles": {"@type": "User"},
                "credentials": {"0": {"@type": "Password", "secret": password}},
                "permissions": {"@type": "Replace", "enabledPermissions": dict.fromkeys(permissions, True),
                                "disabledPermissions": {}},
            }
            account = create(admin_secret, "x:Account", {"owner": owner})["owner"]["id"]
            listeners = {
                name: {"name": name, "protocol": name, "bind": {f"127.0.0.1:{port}": True},
                       "useTls": False, "tlsImplicit": False}
                for name, port in (("http", 8080), ("smtp", 2525))
            }
            create(admin_secret, "x:NetworkListener", listeners)
            local_inbound = {"if": "local_port == 2525", "then": "false"}
            create(admin_secret, "x:MtaStageAuth", {"inbound": {
                "require": {"else": "true", "match": {"0": local_inbound}}
            }})
        with server(stage, domain, address, password) as session:
            if session["primaryAccounts"].get(MAIL) != account or set(session["accounts"]) != {account}:
                raise ValueError("owner session did not match the created mailbox")
            boxes = request(address, password, "Mailbox/get", {"accountId": account, "properties": ["id", "role"]})
            if boxes.get("accountId") != account or not any(b.get("role") == "inbox" for b in boxes.get("list", [])):
                raise ValueError("owner inbox did not become available")
        # SQLite's backup includes committed WAL pages. Publish the closed, standalone file only.
        candidate = stage / "ready.sqlite"
        with contextlib.closing(sqlite3.connect(stage / "store.sqlite")) as source:
            with contextlib.closing(sqlite3.connect(candidate)) as copy:
                source.backup(copy)
                if copy.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise ValueError("SQLite did not verify the new mailbox")
        with candidate.open("rb") as handle:
            os.fsync(handle.fileno())
        os.link(candidate, dest)
        os.fsync(descriptor)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("address", help="local owner mailbox address")
    address = parser.parse_args().address
    try:
        address_parts(address)
        if sys.stdin.isatty():
            password = getpass.getpass("Mailbox password: ")
            if password != getpass.getpass("Confirm mailbox password: "):
                raise ValueError("password confirmation differs")
        else:
            password = sys.stdin.buffer.readline(1026).removesuffix(b"\n").decode()
            if sys.stdin.buffer.read(1):
                raise ValueError("supply exactly one password line")
        initialize(address, password)
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, http.client.HTTPException) as exc:
        # The remote response and credentials must never be included in console or journal output.
        if type(exc) is ValueError:
            message = str(exc)
        else:
            message = type(exc).__name__
        print("Mailbox initialization failed (" + message + ")", file=sys.stderr)
        return 1
    print("Owner mailbox ready for the VOLPAROSSA Mail service.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
