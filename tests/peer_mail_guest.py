# SPDX-License-Identifier: GPL-3.0-only
"""Run the native Signal, Core and Stalwart handoff inside an isolated Linux guest."""
import contextlib
import hashlib
import json
import os
import re
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time

import owner_service_guest as mailbox
from smoke_stalwart import MAIL, call

ROOT = Path(__file__).resolve().parents[1]
NODE = "/trial/bin/node"
CORE = "/trial/bin/volparossa"
SIGNAL = ROOT / "integration/signal-mail/cli.mjs"
ACCEPT = ROOT / "integration/peer-mail/accept.py"
MESSAGE = (b"From: Synthetic Sender <sender@example.test>\r\nTo: owner@volparossa.test\r\n"
           b"Subject: Private peer acceptance\r\nMessage-ID: <peer-test@example.test>\r\n"
           b"MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=sample-boundary\r\n\r\n"
           b"--sample-boundary\r\nContent-Type: text/plain\r\n\r\nThe recipient was offline.\r\n"
           b"--sample-boundary\r\nContent-Type: application/octet-stream\r\n"
           b"Content-Transfer-Encoding: base64\r\n\r\nAAECAwQF/v8=\r\n--sample-boundary--\r\n")


def command(args, *, password=None, expected=0, env=None):
    result = subprocess.run(args, input=password, capture_output=True, env=env, timeout=100)
    if result.returncode != expected:
        # Never put private protocol or HTTP material in CI logs.
        raise RuntimeError(f"{Path(args[0]).name} unexpectedly exited {result.returncode}")
    if expected:
        return result.stderr
    return json.loads(result.stdout)


def digest(data):
    return hashlib.sha256(data).hexdigest()


@contextlib.contextmanager
def offline_providers(root):
    root.mkdir(mode=0o700)
    with subprocess.Popen(["/trial/bin/mailbox-provider-pair", root], stdin=subprocess.PIPE,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as child:
        try:
            deadline = time.monotonic() + 80
            while not (root / "ready").is_file():
                assert child.poll() is None and time.monotonic() < deadline, "provider enrollment incomplete"
                time.sleep(0.15)
            yield [json.loads((root / who / "core.json").read_bytes()) for who in ("a", "b")]
        finally:
            child.stdin.close()
            child.wait(timeout=15)
    assert child.returncode == 0 and not list(root.iterdir()), "provider fixture did not retire"


@contextlib.contextmanager
def owner_server(owner, password, hard_stop=False):
    with subprocess.Popen([mailbox.LIB / "stalwart", "--config", mailbox.LIB / "store.json"],
            cwd=mailbox.STATE, env={"PATH": "/usr/bin:/bin", "STALWART_PUBLIC_URL": "http://127.0.0.1:8080"},
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as child:
        try:
            yield mailbox.wait_ready(owner, password, child)["primaryAccounts"][MAIL]
        finally:
            child.send_signal(signal.SIGKILL if hard_stop else signal.SIGINT)
            child.wait(timeout=20)


def core(who, operation, *args):
    return command([CORE, "--control-socket", who["socket"], "content", "mailbox", operation, *args,
                    "--identity", who["identity"], "--passphrase-file", who["passphrase"]])


def native_trial(work, providers):
    sender, recipient = providers
    sender_profile, recipient_profile = work / "sender", work / "recipient"
    local = [(sender_profile, work / "a.bundle"), (recipient_profile, work / "b.bundle")]
    for profile, bundle in local:
        record = command([NODE, SIGNAL, "init", "--profile", profile, "--bundle", bundle])
        assert record["bundle_sha256"] == digest(bundle.read_bytes())
    for (profile, _), (_, peer) in zip(local, reversed(local)):
        command([NODE, SIGNAL, "pair", "--profile", profile, "--peer-bundle", peer,
                 "--trusted-bundle-sha256", digest(peer.read_bytes())])
    original, packet = work / "original.eml", work / "packet"
    original.write_bytes(MESSAGE)
    seal = [NODE, SIGNAL, "seal", "--profile", sender_profile, "--id", secrets.token_hex(32),
            "--input", original, "--output", packet]
    sealed = command(seal)
    assert command(seal) == sealed and sealed["packet_sha256"] == digest(packet.read_bytes())
    assert sealed["message_sha256"] == digest(MESSAGE) and b"Private peer acceptance" not in packet.read_bytes()

    sent = core(sender, "send", "--invitation", sender["sendInvitation"], "--owner-key", sender["remoteOwner"],
                "--input", packet, "--cache", work / "cipher-cache", "--manifest", work / "original.manifest")
    assert sent["confirmed_providers"] == sent["retained_providers"] == 2
    opaque_id = sent["message_id"]
    provider_dirs = sorted((work / "providers").glob(".providers-*/provider-*"))
    assert len(provider_dirs) == 2
    for provider in provider_dirs:
        regular = [file for file in provider.rglob("*") if file.is_file()]
        assert any(re.fullmatch(r"[a-f0-9]{64}", file.name) for file in regular), "missing retained chunk"
        samples = [file.read_bytes() for file in regular]
        assert all(marker not in data for data in samples for marker in
                   (b"Private peer acceptance", b"sender@example.test", b"The recipient was offline.", packet.read_bytes()))

    def fetch(name):
        return core(recipient, "fetch", "--invitation", recipient["receiveInvitation"],
                    "--output-dir", work / name)
    fetched = fetch("received")
    assert fetched["messages_fetched"] == 1 and fetched["listed_providers"] == 2
    assert not fetched["provider_custody_consumed"] and fetched["acknowledged_providers_per_message"] == 0
    handoff = work / "received" / opaque_id
    assert (handoff / "payload").read_bytes() == packet.read_bytes()
    data = json.loads(mailbox.OWNER.read_text()); user, password = data["owner"], data["password"]
    state = work / "accepted"
    state.mkdir(mode=0o700)
    dest = work / "owner.json"
    accept = [sys.executable, "-B", ACCEPT, "--node", NODE, "--core", CORE, "--profile", recipient_profile,
              "--handoff", handoff, "--connection", dest, "--state-dir", state]
    with owner_server(user, password, hard_stop=True) as account:
        boxes = call(user, password, "Mailbox/get", {"accountId": account, "ids": None, "properties": ["id", "role"]})
        inbox, = [box["id"] for box in boxes["list"] if box["role"] == "inbox"]
        conf = dict(server="http://127.0.0.1:8080", username=user, accountId=account, mailboxId=inbox,
                    coreConfig=str(work / "providers/b/core.json"))
        dest.write_text(json.dumps(conf))
        # Drop only the successful Email/import response, after the actual server committed it.
        # NODE_OPTIONS also reaches the Signal process, where no HTTP requests are made.
        environ = dict(os.environ, NODE_OPTIONS="--import=/trial/lost-import.mjs")
        error = command(accept, password=password.encode(), expected=1, env=environ)
        assert b"Stalwart import unconfirmed" in error and not (handoff / "confirmed.json").exists()
        retained = fetch("still-in-custody")
        assert retained["messages_fetched"] == 1 and retained["listed_providers"] == 2
        assert retained["acknowledged_providers_per_message"] == 0
        assert (work / "still-in-custody" / opaque_id / "payload").read_bytes() == packet.read_bytes()
        records = call(user, password, "Email/query", {"accountId": account, "limit": 2})["ids"]
        assert len(records) == 1
    with owner_server(user, password) as account:
        accepted = command(accept, password=password.encode())
        assert accepted["message_id"] == opaque_id and accepted["signal_packet_sha256"] == sealed["packet_sha256"]
        assert accepted["message_sha256"] == digest(MESSAGE) and accepted["email_id"] == records[0]
        assert accepted["message_bytes"] == len(MESSAGE) and accepted["acknowledged_providers"] == 2
        assert fetch("empty")["messages_fetched"] == 0
        assert call(user, password, "Email/query", {"accountId": account, "limit": 2})["ids"] == records
    return {"schema": 1, "message_bytes": len(MESSAGE), "message_sha256": digest(MESSAGE),
            "offline_custodians": 2, "fetched_before_confirmation": 1, "lost_import_reply": True,
            "same_email_after_restart": True, "exact_raw_bytes_verified": True, "provider_acks": 2,
            "messages_after_ack": 0, "provider_transport": "isolated-local-stream"}


if __name__ == "__main__":
    assert os.geteuid() != 0 and socket.if_nameindex() == [(1, "lo")]
    os.umask(0o077)
    mailbox.bootstrap()
    with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
        work = Path(tmp)
        with offline_providers(work / "providers") as pair:
            report = native_trial(work, pair)
    print(json.dumps(report, sort_keys=True))
