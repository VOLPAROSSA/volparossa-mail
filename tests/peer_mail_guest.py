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
from smoke_thunderbird_stalwart import PREFERENCES, port_open, retire
from thunderbird_import_ui import wait_for
import thunderbird_owner_inbox as reader

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


def open_in_thunderbird(work, ca, user, password):
    profile = reader.open_profile(work, ca, PREFERENCES)
    home, runtime = work / "home", work / "runtime"
    home.mkdir(mode=0o700)
    runtime.mkdir(mode=0o700)
    os.environ.update(DISPLAY=":143", HOME=str(home), XDG_RUNTIME_DIR=str(runtime),
                      MOZ_CRASHREPORTER_DISABLE="1")
    Path("/tmp/.X11-unix").mkdir(mode=0o1777, exist_ok=True)
    def launch(args):
        return subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True)
    display = launch(["/opt/Xvfb", ":143", "-screen", "0", "1280x960x24", "-nolisten", "tcp"])
    try:
        wait_for(lambda: Path("/tmp/.X11-unix/X143").exists())
        client = launch(["dbus-run-session", "--", "/opt/thunderbird/thunderbird", "--no-remote", "--profile",
                         str(profile), "--marionette", "--remote-allow-system-access"])
        try:
            wait_for(lambda: port_open(2828), 30)
            receipt = reader.read(user, password, {}, subject="Private peer acceptance",
                                  body="The recipient was offline.", message_id="peer-test@example.test")
            assert client.wait(timeout=30) == 0
            return receipt
        finally:
            assert retire(client), "Thunderbird did not retire"
    finally:
        assert retire(display), "display did not retire"


def native_trial(work, providers, ca, user, password):
    sender, recipient = providers
    sender_profile, recipient_profile = work / "sender", work / "recipient"
    local = [(sender_profile, work / "a.bundle"), (recipient_profile, work / "b.bundle")]
    for profile, bundle in local:
        record = command([NODE, SIGNAL, "init", "--profile", profile, "--bundle", bundle])
        assert record["bundle_sha256"] == digest(bundle.read_bytes())
    # Authenticate each Core identity independently of any invitation.
    owners = []
    for config in providers:
        key = command([CORE, "--control-socket", config["socket"], "content", "recipient-key",
                       "--identity", config["identity"], "--passphrase-file", config["passphrase"]])
        owners.append(key["identity_public_key_hex"])
    for (profile, _), (_, peer), owner in zip(local, reversed(local), reversed(owners)):
        command([NODE, SIGNAL, "pair", "--profile", profile, "--peer-bundle", peer,
                 "--trusted-bundle-sha256", digest(peer.read_bytes()), "--core-owner-key", owner])

    original, invitation = work / "selected.eml", work / "selected.invitation"
    original.write_bytes(MESSAGE)
    invitation.write_bytes(Path(sender["sendInvitation"]).read_bytes())
    operation = secrets.token_hex(32)
    send = [NODE, SIGNAL, "send", "--profile", sender_profile, "--id", operation,
            "--core-config", work / "providers/a/core.json"]
    sent = command([*send, "--input", original, "--invitation", invitation, "--core-owner-key", owners[1]])
    assert sent["operation"] == "send" and sent["confirmed_providers"] == 2
    assert sent["message_sha256"] == digest(MESSAGE) and sent["message_bytes"] == len(MESSAGE)
    original.unlink()
    invitation.unlink()
    assert command([*send, "--resume"]) == sent
    opaque_id = sent["message_id"]
    packet = sender_profile / "core-send" / operation / "packet"
    assert digest(packet.read_bytes()) == sent["packet_sha256"]
    assert b"Private peer acceptance" not in packet.read_bytes()
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
    state = work / "accepted"
    state.mkdir(mode=0o700)
    dest = work / "owner.json"
    pickup = [sys.executable, "-B", ACCEPT, "--node", NODE, "--core", CORE, "--profile", recipient_profile,
              "--invitation", recipient["receiveInvitation"], "--connection", dest, "--state-dir", state]
    with owner_server(user, password, hard_stop=True) as account:
        boxes = call(user, password, "Mailbox/get", {"accountId": account, "ids": None, "properties": ["id", "role"]})
        inbox, = [box["id"] for box in boxes["list"] if box["role"] == "inbox"]
        conf = dict(server="http://127.0.0.1:8080", username=user, accountId=account, mailboxId=inbox,
                    coreConfig=str(work / "providers/b/core.json"))
        dest.write_text(json.dumps(conf))
        # Drop only the successful Email/import response, after the actual server committed it.
        # NODE_OPTIONS also reaches the Signal process, where no HTTP requests are made.
        environ = dict(os.environ, NODE_OPTIONS="--import=/trial/lost-import.mjs")
        first = subprocess.run(pickup, input=password.encode(), capture_output=True, env=environ, timeout=100)
        assert first.returncode == 1 and json.loads(first.stdout)["pending"] == 1
        retained_item = state / "pickup/pending" / opaque_id
        assert not (retained_item / "confirmed.json").exists()
        original_token = digest((retained_item / "import-token").read_bytes())
        original_pending = digest((retained_item / "pending.pb").read_bytes())
        retained = fetch("still-in-custody")
        assert retained["messages_fetched"] == 1 and retained["listed_providers"] == 2
        assert retained["acknowledged_providers_per_message"] == 0
        assert (work / "still-in-custody" / opaque_id / "payload").read_bytes() == packet.read_bytes()
        records = call(user, password, "Email/query", {"accountId": account, "limit": 2})["ids"]
        assert len(records) == 1
    with owner_server(user, password) as account:
        dest.write_text(json.dumps(dict(conf, mailboxId="different")))
        assert b"another route or mailbox" in command(pickup, password=password.encode(), expected=1)
        assert not (retained_item / "confirmed.json").exists()
        dest.write_text(json.dumps(conf))
        # With no local journal to consult, a mismatched but equal-sized candidate must
        # fail while reading the real stored blob. The existing email stays in place.
        wrong = work / "different.eml"
        wrong.write_bytes(MESSAGE.replace(b"offline.", b"on-line."))
        assert len(wrong.read_bytes()) == len(MESSAGE) and wrong.read_bytes() != MESSAGE
        jmap = work / "selected-mailbox.json"
        jmap.write_text(json.dumps({k: conf[k] for k in ("server", "username", "accountId", "mailboxId")}))
        error = command([NODE, ROOT / "integration/peer-mail/import-mime.mjs", jmap, wrong, work / "other.json",
                         sent["packet_sha256"]], password=password.encode(), expected=1)
        assert b"peer_bytes_unverified" in error
        # Restart with the same journal. Inspect the real confirmation inputs without logging private bytes.
        wrapper = work / "observed-core"
        wrapper.write_text(f"""#!/usr/bin/python3
import hashlib, os, resource, signal, subprocess, sys
from pathlib import Path
if os.environ.get("OFFLINE_AFTER_ACK") == "1" and "confirm-import" in sys.argv:
    Path({str(work / "unexpected-offline-confirm")!r}).touch()
if "confirm-import" in sys.argv:
    def signature(flag):
        return hashlib.sha256(Path(sys.argv[sys.argv.index(flag)+1]).read_bytes()).hexdigest()
    assert signature("--pending") == {original_pending!r} and signature("--import-token-file") == {original_token!r}
    Path({str(work / "same-handoff")!r}).touch()
    if os.environ.get("INTERRUPT_AFTER_TEST_ACK") == "1":
        # The two individual signed receipts fit; their combined JSON cannot be published.
        def limit_aggregate():
            resource.setrlimit(resource.RLIMIT_FSIZE, (1024, 1024))
        done = subprocess.run([{CORE!r}, *sys.argv[1:]], stdout=subprocess.DEVNULL, preexec_fn=limit_aggregate)
        root = Path(sys.argv[sys.argv.index("--pending") + 1]).parent
        assert done.returncode and len(list(root.glob("ack-*.json"))) == 2 and not (root / "confirmed.json").exists()
        os.kill(os.getppid(), signal.SIGKILL)
        sys.exit(0)
os.execv({CORE!r}, [{CORE!r}, *sys.argv[1:]])
""")
        wrapper.chmod(0o700)
        resumed = pickup.copy()
        resumed[resumed.index(CORE)] = wrapper
        # Stop the receiver with plaintext staged and after the real JMAP readback, before Core ACK.
        stop_at_readback = work / "stop-after-jmap"
        stop_at_readback.write_text(f"""#!/usr/bin/python3
import os, signal, subprocess, sys
from pathlib import Path
if Path(sys.argv[1]).name == "import-mime.mjs":
    subprocess.run([{NODE!r}, *sys.argv[1:]], stdout=subprocess.DEVNULL, check=True)
    os.kill(os.getppid(), signal.SIGKILL)
    sys.exit(0)
os.execv({NODE!r}, [{NODE!r}, *sys.argv[1:]])
""")
        stop_at_readback.chmod(0o700)
        interrupted_import = resumed.copy()
        interrupted_import[interrupted_import.index(NODE)] = stop_at_readback
        stopped = subprocess.run(interrupted_import, input=password.encode(), capture_output=True, timeout=100)
        assert stopped.returncode == -signal.SIGKILL and (state / ".accept-work/message.eml").read_bytes() == MESSAGE
        assert fetch("still-after-readback")["messages_fetched"] == 1
        interrupted = subprocess.run(resumed, input=password.encode(), capture_output=True, timeout=100,
                                     env=dict(os.environ, INTERRUPT_AFTER_TEST_ACK="1"))
        assert interrupted.returncode == -signal.SIGKILL and not (retained_item / "confirmed.json").exists()
        assert len(list(retained_item.glob("ack-*.json"))) == 2
        assert not (state / ".accept-work").exists()
        assert not (state / "pickup/receipts" / (opaque_id + ".json")).exists()
        assert fetch("confirmed-before-restart")["messages_fetched"] == 0
        # Disconnect the receiver's actual Unix endpoint for both provider routes.
        socket_path = Path(recipient["socket"])
        disconnected = socket_path.with_name("offline-test.sock")
        socket_path.rename(disconnected)
        try:
            offline_env = dict(os.environ, OFFLINE_AFTER_ACK="1")
            first_ack, second_ack = sorted(retained_item.glob("ack-*.json"))
            original = first_ack.read_bytes()
            candidate = json.loads(original)
            signed = bytearray.fromhex(candidate["receipt"])
            signed[-1] ^= 1
            for replacement in (json.loads(second_ack.read_bytes())["receipt"], signed.hex()):
                candidate["receipt"] = replacement
                first_ack.write_text(json.dumps(candidate))
                rejected = subprocess.run(resumed, input=password.encode(), capture_output=True,
                                          env=offline_env, timeout=100)
                assert rejected.returncode == 1 and b"could not verify" in rejected.stderr
                assert first_ack.is_file() and second_ack.is_file() and not (state / "pickup/receipts" / (opaque_id + ".json")).exists()
            first_ack.write_bytes(original)
            # Two callers can start together. The durable pickup directory serializes their progress.
            procs = [subprocess.Popen(resumed, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      env=offline_env) for _ in range(2)]
            for proc in procs:
                proc.stdin.write(password.encode())
                proc.stdin.close()
                proc.stdin = None
            totals = []
            for proc in procs:
                out, err = proc.communicate(timeout=100)
                assert proc.returncode == 1 and not err
                totals.append(json.loads(out))
            assert sorted(r["imported"] for r in totals) == [0, 1]
            assert all(r["pending"] == 0 and r["listed_providers"] is None and r["degraded"]
                       and not r["fetch_complete"] for r in totals)
            assert not (work / "unexpected-offline-confirm").exists()
        finally:
            disconnected.rename(socket_path)
        online = command(resumed, password=password.encode())
        assert online["fetch_complete"] and online["listed_providers"] == 2 and not online["degraded"]
        assert online["imported"] == online["pending"] == 0
        assert (work / "same-handoff").is_file() and not list((state / "pickup/pending").iterdir())
        saved = json.loads((state / "pickup/receipts" / (opaque_id + ".json")).read_bytes())
        assert saved["confirmation"]["operation"] == "mailbox_verify_import_acks" and not saved["confirmation"]["network_used"]
        assert len(saved["confirmation"]["acknowledgement_receipts"]) == 2
        accepted = saved["delivery"]
        assert accepted["message_id"] == opaque_id and accepted["signal_packet_sha256"] == sent["packet_sha256"]
        assert accepted["message_sha256"] == digest(MESSAGE) and accepted["email_id"] == records[0]
        assert accepted["message_bytes"] == len(MESSAGE) and accepted["acknowledged_providers"] == 2
        assert fetch("empty")["messages_fetched"] == 0
        assert call(user, password, "Email/query", {"accountId": account, "limit": 2})["ids"] == records
        native_inbox = open_in_thunderbird(work, ca, user, password)
    return {"schema": 2, "native_inbox": native_inbox, "message_bytes": len(MESSAGE), "message_sha256": digest(MESSAGE),
            "sender_command": "send", "sender_resumed_without_inputs": True, "offline_custodians": 2, "fetched_before_confirmation": 1, "lost_import_reply": True,
            "same_email_after_restart": True, "different_same_size_refused": True, "exact_raw_bytes_verified": True, "provider_acks": 2,
            "messages_after_ack": 0, "provider_transport": "isolated-local-stream", "owner_invitation_pickup": True,
            "original_handoff_resumed": True, "pickup_killed_after_provider_acks": True,
            "individual_signed_acks_recovered_without_aggregate": True, "altered_and_mixed_acks_refused": True,
            "interrupted_plaintext_staging_removed": True, "changed_mailbox_refused": True,
            "concurrent_pickups_imported_once": True, "final_receipts_recovered_while_offline": True}


if __name__ == "__main__":
    assert os.geteuid() != 0 and socket.if_nameindex() == [(1, "lo")]
    os.umask(0o077)
    with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
        work = Path(tmp)
        ca, cert, key = reader.issue_test_certificate(work)
        user, password = "owner@volparossa.test", secrets.token_urlsafe(32)
        subprocess.run([sys.executable, "-B", mailbox.LIB / "initialize-owner.py", user, "--imap-cert", cert,
                        "--imap-key", key], input=password.encode(), check=True,
                       stdout=subprocess.DEVNULL, timeout=100)
        key.unlink()
        with offline_providers(work / "providers") as pair:
            report = native_trial(work, pair, ca, user, password)
    print(json.dumps(report, sort_keys=True))
