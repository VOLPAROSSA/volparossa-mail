# SPDX-License-Identifier: GPL-3.0-only
"""Exercise the installed Stalwart service with a fictional owner in its private network."""

import hashlib
import json
import os
from pathlib import Path
import secrets
import smtplib
import socket
import subprocess
import sys
import time
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import smoke_stalwart as server
import stalwart_owner_smtp as smtp
from smoke_stalwart import require

STATE = Path("/var/lib/volparossa-mail")
LIB = Path("/usr/local/libexec/volparossa-mail")
OWNER = STATE / "trial-owner.json"


def wait_ready(user, password, process=None):
    deadline = time.monotonic() + 80
    while time.monotonic() < deadline:
        try:
            return server.http(user, password, "/jmap/session")
        except (ConnectionError, urllib.error.URLError):
            if process:
                require(process.poll() is None, "server exited before readiness")
            time.sleep(0.2)
    raise TimeoutError("JMAP did not become ready")


def bootstrap():
    require(not (STATE / "store.sqlite").exists() and not OWNER.exists(), "fresh test store required")
    owner, password = "owner@volparossa.test", secrets.token_urlsafe(32)
    command = [sys.executable, "-B", LIB / "initialize-owner.py", owner]
    supplied = (password + "\n").encode()
    first = subprocess.run(command, input=supplied, capture_output=True, timeout=100, check=True)
    require(first.stdout == b"Owner mailbox ready for the VOLPAROSSA Mail service.\n" and not first.stderr,
            "first-time setup failed")
    require({item.name for item in STATE.iterdir()} == {"store.sqlite"}, "unexpected published setup file")
    db = STATE / "store.sqlite"
    before = (db.stat().st_ino, server.digest(db))
    second = subprocess.run(command, input=(secrets.token_urlsafe(32) + "\n").encode(),
                            capture_output=True, timeout=10)
    require(second.returncode == 1 and b"already contains a mailbox" in second.stderr
            and (db.stat().st_ino, server.digest(db)) == before, "setup replaced an existing mailbox")
    server.write_json(OWNER, {"owner": owner, "password": password})


def observe(operation):
    details = json.loads(OWNER.read_bytes())
    owner, password = details["owner"], details["password"]
    account = wait_ready(owner, password)["primaryAccounts"][server.MAIL]
    server.verify_owner(owner, password, account)
    if operation == "deliver":
        try:
            server.http(owner, secrets.token_urlsafe(32), "/jmap/session")
        except urllib.error.HTTPError as error:
            require(error.code == 401, "unexpected wrong-password response")
        else:
            raise ValueError("wrong password opened the mailbox")
        with smtplib.SMTP("127.0.0.1", smtp.SMTP_PORT, local_hostname="sender.example.test", timeout=15) as client:
            require(client.ehlo()[0] == 250 and client.mail("visitor@example.test")[0] == 250, "SMTP envelope")
            require(client.rcpt("unrelated@external.test")[0] == 550, "external recipient accepted")
            require(client.rcpt("absent@volparossa.test")[0] == 550, "unknown recipient accepted")
            require(client.rcpt(owner)[0] == 250 and client.data(smtp.MESSAGE)[0] == 250, "SMTP delivery refused")
    deadline = time.monotonic() + 30
    while True:
        ids = server.call(owner, password, "Email/query", {"accountId": account, "limit": 2})["ids"]
        if ids:
            break
        require(time.monotonic() < deadline, "Inbox remained empty")
        time.sleep(0.2)
    require(len(ids) == 1, "expected one delivered message")
    size, digest = smtp.inbox_message(owner, password, account, ids[0])
    db = (STATE / "store.sqlite").stat()
    print(json.dumps({"email_id": ids[0], "received_bytes": size, "received_sha256": digest.hex(),
                      "original_bytes": len(smtp.MESSAGE), "original_sha256": hashlib.sha256(smtp.MESSAGE).hexdigest(),
                      "database_inode": db.st_ino, "database_device": db.st_dev}))


if __name__ == "__main__":
    mode, host_namespace = sys.argv[1:]
    require(os.geteuid() != 0 and socket.if_nameindex() == [(1, "lo")]
            and os.readlink("/proc/self/ns/net") != host_namespace, "private loopback namespace required")
    os.umask(0o077)
    if mode == "bootstrap":
        bootstrap()
    else:
        require(mode in ("deliver", "read"), "unknown operation")
        observe(mode)
