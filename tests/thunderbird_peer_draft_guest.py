# SPDX-License-Identifier: GPL-3.0-only
"""Select a real local Thunderbird draft and verify the resulting offline peer deposit."""

import argparse
import contextlib
from email import policy
from email.parser import BytesParser
import json
import os
import secrets
import shutil
from pathlib import Path
import socket
import sqlite3
import subprocess
import tempfile

from peer_mail_guest import (
    ACCEPT,
    CORE,
    MESSAGE,
    NODE,
    ROOT,
    SIGNAL,
    command,
    core,
    digest,
    offline_providers,
    open_in_thunderbird,
    owner_server,
)
import owner_service_guest as mailbox
from smoke_stalwart import MAIL, call
from thunderbird_owner_inbox import issue_test_certificate
from smoke_thunderbird_stalwart import PREFERENCES, port_open, retire
from thunderbird_import_ui import CENTER, Display, Marionette, wait_for
from thunderbird_owner_inbox import PANE
from thunderbird_composed_draft import save_in_composer

ADDON = "volparossa-peer-draft@volparossa.local"
CREATE = """
const {MailServices} = ChromeUtils.importESModule("resource:///modules/MailServices.sys.mjs");
let local;
try { local = MailServices.accounts.localFoldersServer; } catch { /* not created yet */ }
if (!local) MailServices.accounts.createLocalMailAccount();
const root = MailServices.accounts.localFoldersServer.rootFolder;
const folder = root.containsChildNamed("Drafts") ? root.getChildNamed("Drafts")
  : root.QueryInterface(Ci.nsIMsgLocalMailFolder).createLocalSubfolder("Drafts");
folder.setFlag(Ci.nsMsgFolderFlags.Drafts);
const file = Cc["@mozilla.org/file/local;1"].createInstance(Ci.nsIFile);
file.initWithPath(arguments[0]);
return new Promise((resolve,reject) => MailServices.copy.copyFileMessage(file,folder,null,true,
  Ci.nsMsgMessageFlags.Read,"",{
    onStartCopy(){},onProgress(){},setMessageKey(){},getMessageId(){return null;},
    onStopCopy(status){if (Components.isSuccessCode(status)) resolve(folder.URI); else reject(status);}
  },null));
"""


def pair(work, providers):
    profiles = [work / "sender", work / "recipient"]
    bundles = [work / "sender.bundle", work / "recipient.bundle"]
    keys = []
    for device, profile, bundle in zip(providers, profiles, bundles):
        keys.append(
            command(
                [
                    CORE,
                    "--control-socket",
                    device["socket"],
                    "content",
                    "recipient-key",
                    "--identity",
                    device["identity"],
                    "--passphrase-file",
                    device["passphrase"],
                ]
            )["identity_public_key_hex"]
        )
        command([NODE, SIGNAL, "init", "--profile", profile, "--bundle", bundle])
    addresses = ("sender@example.test", "owner@volparossa.test")
    owners = (Path(os.environ["HOME"]) / ".config/volparossa/mail", work / "recipient-contacts")
    for side, peer in ((0, 1), (1, 0)):
        fingerprint = digest(bundles[peer].read_bytes())
        paired = command([
            "/usr/bin/python3", "-I", "-B", ROOT / "integration/peer-mail/contacts.py", "pair",
            "--owner", owners[side], "--from", addresses[side], "--address", addresses[peer],
            "--profile", profiles[side], "--peer-bundle", bundles[peer], "--trusted-bundle-sha256", fingerprint,
            "--core-owner-key", keys[peer], "--invitation", providers[side]["sendInvitation"],
            "--core-config", work / "providers" / ("a", "b")[side] / "core.json", "--node", NODE,
        ])
        assert paired == {"status":"paired", "address":addresses[peer], "core_owner_key":keys[peer],
                          "peer_bundle_sha256":fingerprint}
    return profiles[1]


@contextlib.contextmanager
def launched(args):
    child = subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        yield child
    finally:
        assert retire(child), "test process did not retire"


def click_draft(driver, screen, source, compose):
    driver.command("Marionette:SetContext", value="chrome")
    wait_for(lambda: driver.chrome(PANE + "return !!pane?.folderPane"))
    user_manifests = Path(
        driver.chrome('return Services.dirsvc.get("XREUserNativeManifests",Ci.nsIFile).path')
    )
    assert user_manifests.is_relative_to(Path(os.environ["HOME"]))
    registered = subprocess.check_output(
        ["/usr/bin/python3", "-B", ROOT / "scripts/peer_draft_install.py", "register", "--node", NODE], text=True
    ).strip()
    assert Path(registered) == user_manifests / "native-messaging-hosts/org.volparossa.peer_mail.json"
    assert Path(registered).with_suffix(".node").resolve(strict=True) == Path(NODE)
    assert shutil.which("node") != NODE
    assert driver.command("Addon:Install", path="/opt/peer-draft.xpi", temporary=False) == ADDON
    if driver.chrome(
        "return document.elementFromPoint(850,20)?.tagName.toLowerCase()==='account-hub-container'"
    ):
        screen.click(938, 206)
    if compose:
        uri, original = save_in_composer(driver, screen, source)
    else:
        uri, original = driver.chrome(CREATE, str(source)), MESSAGE
    message_id = str(BytesParser(policy=policy.default).parsebytes(original)["Message-ID"]).strip("<>")
    if compose:
        # Native Filelink drafts can hold owner-side provider parameters on a MIME part.
        placeholder = source.parent / "filelink.eml"
        placeholder.write_bytes(MESSAGE.replace(b"Private peer acceptance", b"Filelink review")
            .replace(b"peer-test@example.test", b"filelink@example.test")
            .replace(b"Content-Transfer-Encoding:", b"X-Mozilla-Cloud-Part: cloudFile; url=https://example.invalid/file;\r\n"
                     b" provider=synthetic-local-account\r\nContent-Transfer-Encoding:"))
        reject_draft(driver, screen, uri, placeholder, "Filelink review", "filelink@example.test",
                     "Attach the original file in Thunderbird, save the draft, and select it again.")
        # The raw correspondence fits exactly; Signal framing must also fit the Core limit.
        large = source.parent / "large.eml"
        header = (b"From: sender@example.test\r\nTo: owner@volparossa.test\r\nSubject: Size review\r\n"
                  b"Message-ID: <size@example.test>\r\nContent-Type: text/plain\r\n\r\n")
        count, last = divmod(4 * 1024 * 1024 - len(header) - 2, 80)
        large.write_bytes(header + (b"a" * 78 + b"\r\n") * count + b"b" * last + b"\r\n")
        assert large.stat().st_size == 4 * 1024 * 1024
        reject_draft(driver, screen, uri, large, "Size review", "size@example.test",
                     "The encrypted message exceeds 4 MiB. Reduce the message or its attachments and save the draft again.")
    review(driver, screen, uri, "Private peer acceptance", message_id)
    return str(user_manifests.relative_to(Path(os.environ["HOME"]))), original, message_id


def wait_for_custody(driver, home, work):
    states = {
        "The paired recipient will receive this saved version, including its attachments.": "preview",
        "Requesting custody from both providers…": "requested",
        "Both providers have the message for the recipient.": "confirmed",
        "The local mail service did not confirm custody. Check the service and retry this draft.": "native-connection",
        "Custody is unconfirmed. Retry this same saved draft to check the original delivery.": "sender-unconfirmed",
        "This draft cannot use a paired address. You can choose ordinary mail in Thunderbird.": "unpaired",
    }

    def state():
        text = driver.page('return document.querySelector("#status").textContent')
        return states.get(text, "other")

    try:
        wait_for(lambda: state() == "confirmed", 45)
    except TimeoutError:
        # Allowlisted observations only. A failure report never contains a message, account, grant, or path.
        selector = home / ".config/volparossa/mail/selected-mail.sqlite"
        result = {"ui": state(), "selector_opened": selector.exists(), "selection_rows": 0,
                  "signal_envelopes": 0, "provider_receipt": False}
        if selector.is_file():
            with sqlite3.connect(selector.as_uri() + "?mode=ro", uri=True) as db:
                result["selection_rows"] = db.execute("SELECT count(*) FROM selections").fetchone()[0]
                result["provider_receipt"] = bool(db.execute("SELECT count(*) FROM selections WHERE result IS NOT NULL").fetchone()[0])
        with sqlite3.connect((work / "sender/signal-mail.sqlite").as_uri() + "?mode=ro", uri=True) as db:
            result["signal_envelopes"] = db.execute("SELECT count(*) FROM outgoing").fetchone()[0]
        console = driver.chrome("""
            const messages = Services.console.getMessageArray().map(item => item.message || "");
            return ["No such native application", "Native messaging proxy/portal is not available", "Permission denied",
                    "Failed to start", "disconnected port", "not executable"].filter(
                      needle => messages.some(message => message.includes(needle)));
        """)
        result["native_diagnostics"] = console
        raise RuntimeError("Draft delivery stopped: " + json.dumps(result, sort_keys=True)) from None


def reject_draft(driver, screen, uri, source, subject, message_id, explanation):
    assert driver.chrome(CREATE, str(source)) == uri
    review(driver, screen, uri, subject, message_id)
    screen.click(*driver.page(CENTER, "#send"))
    wait_for(lambda: driver.page('return document.querySelector("#status").textContent') == explanation, 35)
    driver.chrome('const t=document.getElementById("tabmail"); t.closeTab(t.currentTabInfo);')
    wait_for(lambda: driver.chrome(PANE + "return !!pane?.folderPane"))


def review(driver, screen, uri, subject, message_id):
    driver.chrome(PANE + "pane.displayFolder(arguments[0])", uri)
    coords = wait_for(
        lambda: driver.chrome(
            PANE
            + """
      const s = [...pane.document.querySelectorAll("#threadTree .subject, #threadTree .subject-line")]
        .find(e => e.textContent.trim()===arguments[0]);
      if (!s) return null;
      const r=s.getBoundingClientRect();
      return r.width && [(pane.mozInnerScreenX+r.x+r.width/2)*pane.devicePixelRatio,
                         (pane.mozInnerScreenY+r.y+r.height/2)*pane.devicePixelRatio];
    """, subject
        ),
        30,
    )
    screen.click(*coords)
    wait_for(
        lambda: driver.chrome(
            PANE + 'return pane.gDBView?.hdrForFirstSelectedMessage?.messageId===arguments[0]', message_id
        )
    )
    screen.click(*driver.chrome(CENTER, f"button[extension='{ADDON}']"))
    wait_for(
        lambda: driver.chrome(
            "return document.getElementById('tabmail')?.currentTabInfo?.browser?"
            ".currentURI.spec.startsWith('moz-extension:')"
        )
    )
    assert driver.page("return document.title") == "Send to a paired contact"
    wait_for(lambda: driver.page('return document.querySelector("#send")?.disabled === false'))
    assert "owner@volparossa.test" in driver.page('return document.querySelector("#to").textContent')
    assert driver.page('return document.querySelector("#subject").textContent') == subject


def receive_in_inbox(work, recipient_profile, handoff, fetched, original, message_id):
    """Open the one UI-selected message in the other owner’s fresh Thunderbird profile."""
    home = work / "recipient-ui"
    home.mkdir(mode=0o700)
    ca, cert, key = issue_test_certificate(home)
    user, password = "owner@volparossa.test", secrets.token_urlsafe(32)
    subprocess.run(
        [
            "/usr/bin/python3",
            "-B",
            mailbox.LIB / "initialize-owner.py",
            user,
            "--imap-cert",
            cert,
            "--imap-key",
            key,
        ],
        input=password.encode(),
        check=True,
        stdout=subprocess.DEVNULL,
        timeout=100,
    )
    key.unlink()
    with owner_server(user, password) as account:
        boxes = call(user, password, "Mailbox/get", {"accountId": account, "properties": ["id", "role"]})
        (inbox,) = [box["id"] for box in boxes["list"] if box["role"] == "inbox"]
        dest = home / "connection.json"
        dest.write_text(
            json.dumps(
                {
                    "server": "http://127.0.0.1:8080",
                    "username": user,
                    "accountId": account,
                    "mailboxId": inbox,
                    "coreConfig": str(work / "providers/b/core.json"),
                }
            )
        )
        journal = home / "journal"
        journal.mkdir(mode=0o700)
        receipt = command(
            [
                "/usr/bin/python3",
                "-B",
                ACCEPT,
                "--profile",
                recipient_profile,
                "--handoff",
                handoff,
                "--connection",
                dest,
                "--state-dir",
                journal,
                "--node",
                NODE,
                "--core",
                CORE,
            ],
            password=password.encode(),
        )
        assert receipt["message_sha256"] == digest(original) and receipt["acknowledged_providers"] == 2
        assert fetched("after-import")["messages_fetched"] == 0
        return open_in_thunderbird(home, ca, user, password, message_id)


def trial(work, compose):
    home, profile, runtime = (work / name for name in ("home", "profile", "runtime"))
    for d in (home, profile, runtime):
        d.mkdir(mode=0o700)
    # Some desktops have a different system Node. Even an unusable one first
    # on PATH must not replace the executable selected during registration.
    wrong_node = runtime / "node"
    wrong_node.write_text("#!/bin/sh\nexit 91\n")
    wrong_node.chmod(0o700)
    os.environ.update(
        HOME=str(home),
        XDG_RUNTIME_DIR=str(runtime),
        DISPLAY=":143",
        MOZ_CRASHREPORTER_DISABLE="1",
        PATH=f"{runtime}:/usr/bin:/bin",
    )
    Path("/tmp/.X11-unix").mkdir(mode=0o1777, exist_ok=True)
    prefs = PREFERENCES | {"mailnews.mark_message_read.auto": False}
    (profile / "user.js").write_text(
        "".join(f"user_pref({json.dumps(k)}, {json.dumps(v)});\n" for k, v in prefs.items())
    )
    attachment = bytes.fromhex("000102030405feff")
    source = work / ("attachment.bin" if compose else "source.eml")
    source.write_bytes(attachment if compose else MESSAGE)
    with offline_providers(work / "providers") as providers:
        recipient_profile = pair(work, providers)

        def fetch(name):
            return core(
                providers[1],
                "fetch",
                "--invitation",
                providers[1]["receiveInvitation"],
                "--output-dir",
                work / name,
            )

        with launched(["/opt/Xvfb", ":143", "-screen", "0", "1280x960x24", "-nolisten", "tcp"]):
            wait_for(lambda: Path("/tmp/.X11-unix/X143").exists())
            with launched(
                [
                    "dbus-run-session",
                    "--",
                    "/opt/thunderbird/thunderbird",
                    "--no-remote",
                    "--profile",
                    str(profile),
                    "--marionette",
                    "--remote-allow-system-access",
                ]
            ) as client:
                wait_for(lambda: port_open(2828), 30)
                with (
                    socket.create_connection(("127.0.0.1", 2828), timeout=15) as sock,
                    sock.makefile("rwb") as stream,
                ):
                    driver = Marionette(stream)
                    caps = driver.command(
                        "WebDriver:NewSession",
                        capabilities={"alwaysMatch": {"unhandledPromptBehavior": "ignore"}},
                    )["capabilities"]
                    assert caps["browserVersion"] == "157.0" and caps["moz:buildID"] == "20260928193122"
                    screen = Display()
                    try:
                        directory, original, message_id = click_draft(driver, screen, source, compose)
                        assert fetch("before-click")["messages_fetched"] == 0
                        screen.click(*driver.page(CENTER, "#send"))
                        wait_for_custody(driver, home, work)
                    finally:
                        screen.x.XCloseDisplay(screen.display)
                    driver.command("Marionette:Quit", flags=["eAttemptQuit"])
                assert client.wait(timeout=30) == 0
        report = fetch("peer")
        assert report["listed_providers"] == 2 and report["messages_fetched"] == 1
        (handoff,) = [d for d in (work / "peer").iterdir() if d.is_dir()]
        output = work / "opened.eml"
        opened = command(
            [
                NODE,
                SIGNAL,
                "open",
                "--profile",
                recipient_profile,
                "--input",
                handoff / "payload",
                "--output",
                output,
            ]
        )
        raw = output.read_bytes()
        assert opened["message_sha256"] == digest(raw) and raw == original
        msg = BytesParser(policy=policy.default).parsebytes(raw)
        assert msg["Message-ID"] == f"<{message_id}>" and msg["Subject"] == "Private peer acceptance"
        parts = list(msg.iter_parts())
        assert "The recipient was offline." in parts[0].get_content()
        assert parts[1].get_payload(decode=True) == attachment
        inbox = receive_in_inbox(work, recipient_profile, handoff, fetch, original, message_id)
    return {
        "thunderbird": "157.0",
        "native_host_root": directory,
        "used_registered_node": True,
        "edited_and_saved_in_composer": compose,
        "filelink_refused_before_custody": compose,
        "oversize_encrypted_message_refused_before_custody": compose,
        "native_toolbar_and_send_clicked": True,
        "packaged_addon_installed": True,
        "owner_registered_both_contacts": True,
        "no_message_before_click": True,
        "offline_provider_count": 2,
        "receiver_inbox": inbox,
        "verified_import_acknowledgements": 2,
        "fetched_messages": 1,
        "message_bytes": len(raw),
        "message_sha256": digest(raw),
        "attachment_bytes_verified": 8,
        "local_store_headers_removed": True,
        "original_correspondence_bytes": True,
    }


if __name__ == "__main__":
    assert os.geteuid() != 0 and socket.if_nameindex() == [(1, "lo")]
    options = argparse.ArgumentParser()
    options.add_argument("--compose", action="store_true")
    args = options.parse_args()
    os.umask(0o077)
    with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
        print(json.dumps(trial(Path(tmp), args.compose), sort_keys=True))
