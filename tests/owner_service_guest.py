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
from smoke_thunderbird_stalwart import mailbox
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
    admin, password = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    env = {"PATH": "/usr/bin:/bin", "STALWART_HOSTNAME": "mail.volparossa.test",
           "STALWART_RECOVERY_MODE": "1", "STALWART_RECOVERY_ADMIN": "fixture-admin:" + admin}
    with subprocess.Popen([LIB / "stalwart", "--config", LIB / "store.json"], cwd=STATE, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as child:
        try:
            wait_ready("fixture-admin", admin, child)
            owner, account, _ = mailbox(admin, password, smtp=True)
            smtp.configure(admin)
            server.write_json(OWNER, {"owner": owner, "account": account, "password": password})
        finally:
            child.terminate()
            try:
                child.wait(timeout=20)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)


def observe(operation):
    details = json.loads(OWNER.read_bytes())
    owner, password, account = (details[k] for k in ("owner", "password", "account"))
    wait_ready(owner, password)
    server.verify_owner(owner, password, account)
    if operation == "deliver":
        with smtplib.SMTP("127.0.0.1", smtp.SMTP_PORT, local_hostname="sender.example.test", timeout=15) as client:
            require(client.sendmail("visitor@example.test", [owner], smtp.MESSAGE) == {}, "SMTP delivery refused")
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
