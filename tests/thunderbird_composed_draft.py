# SPDX-License-Identifier: GPL-3.0-only
"""Create the QA correspondence using Thunderbird's own compose and save commands."""
import base64
from email import policy
from email.parser import BytesParser
import re

from thunderbird_import_ui import wait_for

COMPOSE = """
const {MailServices} = ChromeUtils.importESModule("resource:///modules/MailServices.sys.mjs");
let local;
try { local = MailServices.accounts.localFoldersServer; } catch {}
if (!local) MailServices.accounts.createLocalMailAccount();
const root = MailServices.accounts.localFoldersServer.rootFolder;
const folder = root.containsChildNamed("Drafts") ? root.getChildNamed("Drafts")
  : root.QueryInterface(Ci.nsIMsgLocalMailFolder).createLocalSubfolder("Drafts");
folder.setFlag(Ci.nsMsgFolderFlags.Drafts);
const server = MailServices.accounts.createIncomingServer("sender", "127.0.0.1", "pop3");
const identity = MailServices.accounts.createIdentity();
identity.email = "sender@example.test";
identity.fullName = "Synthetic Sender";
identity.draftsFolderURI = folder.URI;
identity.composeHtml = false;
const account = MailServices.accounts.createAccount();
account.incomingServer = server;
account.addIdentity(identity);
server.valid = true;
const file = Cc["@mozilla.org/file/local;1"].createInstance(Ci.nsIFile);
file.initWithPath(arguments[0]);
const attachment = Cc["@mozilla.org/messengercompose/attachment;1"].createInstance(Ci.nsIMsgAttachment);
attachment.url = Services.io.newFileURI(file).spec;
attachment.name = file.leafName;
attachment.contentType = "application/octet-stream";
const fields = Cc["@mozilla.org/messengercompose/composefields;1"].createInstance(Ci.nsIMsgCompFields);
fields.to = "owner@volparossa.test";
fields.subject = "Private peer acceptanc";
fields.body = "The recipient was offline.";
fields.addAttachment(attachment);
const params = Cc["@mozilla.org/messengercompose/composeparams;1"].createInstance(Ci.nsIMsgComposeParams);
params.type = Ci.nsIMsgCompType.New;
params.format = Ci.nsIMsgCompFormat.PlainText;
params.identity = identity;
params.composeFields = fields;
MailServices.compose.OpenComposeWindowWithParams(null, params);
return folder.URI;
"""

FOLDER = """
const {MailServices} = ChromeUtils.importESModule("resource:///modules/MailServices.sys.mjs");
const folder = MailServices.accounts.localFoldersServer.rootFolder.getChildNamed("Drafts");
"""


def save_in_composer(driver, screen, attachment):
    uri = driver.chrome(COMPOSE, str(attachment))
    compose = 'const w=Services.wm.getMostRecentWindow("msgcompose"); '
    coords = wait_for(lambda: driver.chrome(compose + """
        const field=w?.document.getElementById("msgSubject");
        if (field?.value!=="Private peer acceptanc") return null;
        const r=field.getBoundingClientRect();
        return r.width && [(w.mozInnerScreenX+r.x+r.width/2)*w.devicePixelRatio,
                           (w.mozInnerScreenY+r.y+r.height/2)*w.devicePixelRatio];
    """))
    screen.click(*coords)
    screen.press("End")
    screen.press("e")
    wait_for(lambda: driver.chrome(compose + 'return w?.document.getElementById("msgSubject").value==="Private peer acceptance"'))
    screen.key("Control_L", True)
    screen.press("s")
    screen.key("Control_L", False)
    wait_for(lambda: driver.chrome(FOLDER + 'return folder.getTotalMessages(false)===1'), 20)
    screen.key("Control_L", True)
    screen.press("w")
    screen.key("Control_L", False)
    wait_for(lambda: driver.chrome(compose + 'return !w'))
    encoded = driver.chrome(FOLDER + """
      const {MsgHdrProcessor} = ChromeUtils.importESModule("resource:///modules/ExtensionMessages.sys.mjs");
      const [header] = [...folder.messages];
      return new MsgHdrProcessor(header).getOriginalMessage().then(bytes=>btoa(bytes));
    """)
    stored = base64.b64decode(encoded, validate=True)
    head, separator, body = stored.partition(b"\r\n\r\n")
    assert separator and body
    # Independent expected MIME from the saved draft. Only store-private fields may disappear.
    private = rb"(?:X-Mozilla-(?:Status2?|Keys|Draft-Info|Identity-Key|Account-Key|Fcc|News-Host)|X-(?:Identity|Account)-Key|Fcc)"
    filtered, removed = re.subn(rb"(?im)^" + private + rb":[^\r\n]*\r\n(?:[ \t][^\r\n]*\r\n)*", b"", head + b"\r\n")
    assert removed and b"X-Mozilla-Draft-Info:".lower() in head.lower()
    expected = filtered + b"\r\n" + body
    parsed = BytesParser(policy=policy.default).parsebytes(expected)
    assert parsed["From"].addresses[0].addr_spec == "sender@example.test"
    assert parsed["To"].addresses[0].addr_spec == "owner@volparossa.test"
    assert parsed["Subject"] == "Private peer acceptance"
    assert parsed["Message-ID"] and parsed["Date"]
    assert not any(name.lower().startswith("x-mozilla-") for name in parsed.keys())
    assert b"mailbox://" not in expected and str(attachment.parent).encode() not in expected
    assert attachment.read_bytes() == next(parsed.iter_attachments()).get_payload(decode=True)
    assert "The recipient was offline." in parsed.get_body().get_content()
    return uri, expected
