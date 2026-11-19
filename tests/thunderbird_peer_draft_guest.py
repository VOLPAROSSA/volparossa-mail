# SPDX-License-Identifier: GPL-3.0-only
"""Select a real local Thunderbird draft and verify the resulting offline peer deposit."""

import argparse
import contextlib
from email import policy
from email.parser import BytesParser
import json
import os
import secrets
from pathlib import Path
import socket
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
    for profile, peer, key in zip(profiles, reversed(bundles), reversed(keys)):
        command(
            [
                NODE,
                SIGNAL,
                "pair",
                "--profile",
                profile,
                "--peer-bundle",
                peer,
                "--trusted-bundle-sha256",
                digest(peer.read_bytes()),
                "--core-owner-key",
                key,
            ]
        )
    owner = Path(os.environ["HOME"]) / ".config/volparossa/mail"
    owner.mkdir(mode=0o700, parents=True)
    (owner / "address-book.json").write_text(
        json.dumps(
            {
                "version": 1,
                "address": "sender@example.test",
                "contacts": [
                    {
                        "address": "owner@volparossa.test",
                        "profile": str(profiles[0]),
                        "peer_bundle_sha256": digest(bundles[1].read_bytes()),
                        "core_owner_key": keys[1],
                        "invitation": providers[0]["sendInvitation"],
                        "core_config": str(work / "providers/a/core.json"),
                    }
                ],
            }
        )
    )
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
        ["/usr/bin/python3", "-B", ROOT / "scripts/peer_draft_install.py", "register"], text=True
    ).strip()
    assert Path(registered) == user_manifests / "native-messaging-hosts/org.volparossa.peer_mail.json"
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
        assert driver.chrome(CREATE, str(placeholder)) == uri
        review(driver, screen, uri, "Filelink review", "filelink@example.test")
        screen.click(*driver.page(CENTER, "#send"))
        wait_for(lambda: driver.page('return document.querySelector("#status").textContent')
                 == "Attach the original file in Thunderbird, save the draft, and select it again.")
        driver.chrome('const t=document.getElementById("tabmail"); t.closeTab(t.currentTabInfo);')
        wait_for(lambda: driver.chrome(PANE + "return !!pane?.folderPane"))
    review(driver, screen, uri, "Private peer acceptance", message_id)
    return str(user_manifests.relative_to(Path(os.environ["HOME"]))), original, message_id


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
    os.environ.update(
        HOME=str(home),
        XDG_RUNTIME_DIR=str(runtime),
        DISPLAY=":143",
        MOZ_CRASHREPORTER_DISABLE="1",
        PATH="/trial/bin:/usr/bin:/bin",
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
                        wait_for(
                            lambda: driver.page('return document.querySelector("#status").textContent')
                            == "Both providers have the message for the recipient.",
                            35,
                        )
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
        "edited_and_saved_in_composer": compose,
        "filelink_refused_before_custody": compose,
        "native_toolbar_and_send_clicked": True,
        "packaged_addon_installed": True,
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
