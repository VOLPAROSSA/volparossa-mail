# SPDX-License-Identifier: GPL-3.0-only
"""Read an owner's synthetic inbound message in Thunderbird's ordinary IMAP inbox."""

import json
import socket
import ssl
import subprocess

import smoke_stalwart as server
from smoke_stalwart import require
from thunderbird_import_ui import Display, Marionette, wait_for

PORT = 2993
READ_PERMISSIONS = ("imapAuthenticate", "imapCapability", "imapEnable", "imapExamine", "imapFetch",
                    "imapId", "imapIdle", "imapList", "imapLsub", "imapNamespace", "imapSelect", "imapStatus")
SUBJECT = "Owner mailbox reception"
BODY = "Synthetic visitor message."


def quiet(*args):
    subprocess.run(args, check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=15)


def configure(admin, private):
    tls = private / "imap-tls"
    tls.mkdir(mode=0o700)
    ca, key, cert = tls / "ca.crt", tls / "server.key", tls / "server.crt"
    quiet("openssl", "req", "-x509", "-newkey", "rsa:2048", "-noenc", "-days", "2",
          "-subj", "/CN=Disposable Mail Test CA", "-keyout", tls / "ca.key", "-out", ca,
          "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign")
    quiet("openssl", "req", "-new", "-newkey", "rsa:2048", "-noenc", "-subj", "/CN=localhost",
          "-keyout", key, "-out", tls / "server.csr")
    ext = tls / "extensions"
    ext.write_text("basicConstraints=critical,CA:FALSE\nsubjectAltName=DNS:localhost,IP:127.0.0.1\n"
                   "keyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n")
    quiet("openssl", "x509", "-req", "-days", "2", "-in", tls / "server.csr", "-CA", ca,
          "-CAkey", tls / "ca.key", "-set_serial", "2", "-out", cert, "-extfile", ext)
    server.created(server.call("fixture-admin", admin, "x:Certificate/set", {"create": {"imap": {
        "certificate": {"@type": "Text", "value": cert.read_text()},
        "privateKey": {"@type": "Text", "secret": key.read_text()}}}}), "imap")
    server.created(server.call("fixture-admin", admin, "x:NetworkListener/set", {"create": {"imap": {
        "name": "imap", "protocol": "imap", "bind": {f"127.0.0.1:{PORT}": True},
        "useTls": True, "tlsImplicit": True}}}), "imap")
    (tls / "ca.key").unlink()
    key.unlink()
    return ca


def open_profile(private, ca, prefs):
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    with socket.create_connection(("127.0.0.1", PORT), timeout=10) as conn:
        try:
            with context.wrap_socket(conn, server_hostname="127.0.0.1"):
                raise ValueError("unexpected-system-trust")
        except ssl.SSLCertVerificationError:
            pass
    context.load_verify_locations(cafile=ca)
    with socket.create_connection(("127.0.0.1", PORT), timeout=10) as conn:
        with context.wrap_socket(conn, server_hostname="127.0.0.1") as peer:
            require(peer.recv(512).startswith(b"* OK "), "imap-tls-greeting")
    profile = private / "inbox-profile"
    profile.mkdir(mode=0o700)
    quiet("certutil", "-N", "--empty-password", "-d", "sql:" + str(profile))
    quiet("certutil", "-A", "-d", "sql:" + str(profile), "-t", "C,,", "-n", "Isolated Mail CA", "-i", ca)
    (profile / "user.js").write_text("".join(f"user_pref({json.dumps(k)}, {json.dumps(v)});\n"
        for k, v in (prefs | {"mailnews.mark_message_read.auto": False}).items()))
    return profile


SETUP = """
const {MailServices} = ChromeUtils.importESModule("resource:///modules/MailServices.sys.mjs");
const [user, password, port] = arguments;
const imap = MailServices.accounts.createIncomingServer(user, "127.0.0.1", "imap");
imap.socketType = Ci.nsMsgSocketType.SSL;
imap.port = port;
imap.authMethod = Ci.nsMsgAuthMethod.passwordCleartext;
imap.password = password;
imap.prettyName = "Owner mailbox";
const identity = MailServices.accounts.createIdentity();
identity.email = user;
const account = MailServices.accounts.createAccount();
account.incomingServer = imap;
account.addIdentity(identity);
imap.valid = true;
imap.performExpand(window.msgWindow);
return [imap.key, imap.port, imap.socketType == Ci.nsMsgSocketType.SSL];
"""
PANE = "const pane = document.getElementById('tabmail')?.currentAbout3Pane; "
GET_INBOX = """
const {MailServices} = ChromeUtils.importESModule("resource:///modules/MailServices.sys.mjs");
const server = MailServices.accounts.getIncomingServer(arguments[0]);
const inbox = server.rootFolder.getFolderWithFlags(Ci.nsMsgFolderFlags.Inbox);
return inbox?.URI;
"""


def read(owner, password, progress):
    with socket.create_connection(("127.0.0.1", 2828), timeout=10) as sock, sock.makefile("rwb") as stream:
        sock.settimeout(25)
        driver = Marionette(stream)
        cap = driver.command("WebDriver:NewSession", capabilities={"alwaysMatch": {"unhandledPromptBehavior": "ignore"}})["capabilities"]
        require(cap["browserVersion"] == "157.0" and cap["moz:buildID"] == "20260928193122", "reader-build")
        driver.command("Marionette:SetContext", value="chrome")
        progress["phase"] = "imap-account"
        wait_for(lambda: driver.chrome(PANE + "return !!pane?.folderPane"), 20)
        progress["phase"] = "imap-configure-account"
        key, port, encrypted = driver.chrome(SETUP, owner, password, PORT)
        require(isinstance(key, str) and key.startswith("server") and port == PORT and encrypted is True,
                "imap-account-endpoint")
        progress["phase"] = "imap-inbox"
        uri = wait_for(lambda: driver.chrome(GET_INBOX, key), 20)
        wait_for(lambda: driver.chrome(PANE + "return !!pane.folderPane.getRowForFolder(arguments[0])", uri), 20)
        driver.chrome(PANE + "pane.displayFolder(arguments[0]);", uri)
        progress["phase"] = "imap-message-list"
        coords = wait_for(lambda: driver.chrome(PANE + """
            if (pane?.gFolder?.URI != arguments[0]) return null;
            const matches = [...pane.document.querySelectorAll("#threadTree .subject, #threadTree .subject-line")]
              .filter(e => e.textContent.trim() == arguments[1]);
            if (!matches.length) return null;
            const r = matches[0].getBoundingClientRect();
            if (!r.width || !r.height) return null;
            return [(pane.mozInnerScreenX+r.x+r.width/2)*pane.devicePixelRatio,
                    (pane.mozInnerScreenY+r.y+r.height/2)*pane.devicePixelRatio];
        """, uri, SUBJECT), 25)
        progress["phase"] = "imap-inbox-selection"
        screen = Display()
        try:
            if driver.chrome("return document.elementFromPoint(850,20)?.tagName.toLowerCase()==='account-hub-container'"):
                screen.click(938, 206)
            screen.click(*coords)
        finally:
            screen.x.XCloseDisplay(screen.display)
        progress["phase"] = "imap-message-pane"
        wait_for(lambda: driver.chrome(PANE + """
            const browser = pane.messageBrowser?.contentWindow?.getMessagePaneBrowser?.();
            return pane.gDBView?.hdrForFirstSelectedMessage?.messageId == "after-quit@example.test" &&
              browser?.contentDocument?.body?.innerText?.includes(arguments[0]);
        """, BODY), 25)
        driver.command("Marionette:Quit", flags=["eAttemptQuit"])
    return {"implicit_tls": True, "untrusted_certificate_rejected": True, "fresh_profile": True, "inbox_message_id": "after-quit@example.test",
            "subject_displayed": True, "message_body_displayed": True}
