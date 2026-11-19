# SPDX-License-Identifier: GPL-3.0-only
"""Synthetic SMTP reception in the native Mail trial's private loopback namespace."""

import hashlib
import smtplib
import time

import smoke_stalwart as server
from smoke_stalwart import require

SMTP_PORT = 2525
MESSAGE = (b"From: Visitor <visitor@example.test>\r\nTo: owner@volparossa.test\r\n"
           b"Subject: Owner mailbox reception\r\nMessage-ID: <after-quit@example.test>\r\n"
           b"Date: Tue, 01 Sep 2026 12:01:00 +0000\r\nMIME-Version: 1.0\r\n"
           b"Content-Type: text/plain; charset=utf-8\r\n\r\nSynthetic visitor message.\r\n")


def configure(admin):
    def create(method, items):
        response = server.call("fixture-admin", admin, method + "/set", {"create": items})
        require(not response.get("notCreated") and set(response.get("created") or {}) == set(items),
                "receive-configuration")

    listeners = server.call("fixture-admin", admin, "x:NetworkListener/get", {"ids": None, "properties": ["id"]})
    require(listeners.get("list") == [], "fresh-receiver-required")
    create("x:NetworkListener", {
        name: {"name": name, "protocol": name, "bind": {f"127.0.0.1:{port}": True},
               "useTls": False, "tlsImplicit": False}
        for name, port in (("http", 8080), ("smtp", SMTP_PORT))
    })
    # Give the nonprivileged loopback port inbound SMTP semantics. The server's
    # recipient and relay rules still apply to every envelope recipient.
    create("x:MtaStageAuth", {"inbound": {"require": {"else": "true",
        "match": {"0": {"if": f"local_port == {SMTP_PORT}", "then": "false"}}}}})


def receive(owner, password, account, imported):
    def ids():
        result = server.call(owner, password, "Email/query", {"accountId": account, "limit": 3})
        require(result.get("accountId") == account and isinstance(result.get("ids"), list), "owner-query")
        return result["ids"]

    require(ids() == [imported], "unexpected-initial-mail")
    with smtplib.SMTP("127.0.0.1", SMTP_PORT, local_hostname="sender.example.test", timeout=15) as smtp:
        require(smtp.ehlo()[0] == 250 and smtp.mail("visitor@example.test")[0] == 250, "smtp-envelope")
        external = smtp.rcpt("unrelated@external.test")[0]
        absent = smtp.rcpt("unregistered@volparossa.test")[0]
        require(external == absent == 550, "unexpected-recipient-acceptance")
        require(smtp.rcpt(owner)[0] == 250, "smtp-owner")
        delivered = smtp.data(MESSAGE)[0]
        require(delivered == 250, "smtp-delivery")
    deadline = time.monotonic() + 30
    while True:
        current = ids()
        if current != [imported]:
            require(len(current) == 2 and len(set(current)) == 2 and imported in current, "delivery-count")
            received = next(item for item in current if item != imported)
            return received, {"external_recipient_status": external, "unknown_local_recipient_status": absent,
                              "accepted_status": delivered}
        require(time.monotonic() < deadline, "delivery-timeout")
        time.sleep(0.2)


def inbox_message(owner, password, account, email_id):
    result = server.call(owner, password, "Email/get", {"accountId": account, "ids": [email_id],
        "properties": ["id", "blobId", "mailboxIds", "size"]})
    require(result.get("accountId") == account and result.get("notFound") == [] and len(result["list"]) == 1,
            "owner-email")
    message = result["list"][0]
    blob = message["blobId"]
    require(message["id"] == email_id and blob.isascii() and blob.isalnum()
            and len(message["mailboxIds"]) == 1 and all(value is True for value in message["mailboxIds"].values()),
            "received-binding")
    box_id, = message["mailboxIds"]
    boxes = server.call(owner, password, "Mailbox/get", {"accountId": account, "ids": [box_id],
        "properties": ["id", "role"]})
    require(boxes.get("accountId") == account and boxes.get("notFound") == []
            and boxes["list"] == [{"id": box_id, "role": "inbox"}], "inbox-binding")
    raw = server.http(owner, password, f"/jmap/download/{account}/{blob}/fixture.eml", raw=True)
    # The receiver prepends its own trace headers. Preserve every original MIME byte.
    require(len(raw) == message["size"] and raw.endswith(MESSAGE), "received-mime")
    return len(raw), hashlib.sha256(raw).digest()


def check_receipt(value):
    expected = {"after_thunderbird_exit": True, "recovery_admin_removed": True, "external_recipient_status": 550,
                "unknown_local_recipient_status": 550, "accepted_status": 250, "original_bytes": len(MESSAGE),
                "original_sha256": hashlib.sha256(MESSAGE).hexdigest(), "inbox_read_after_restart": True}
    require(isinstance(value, dict) and value.keys() == expected.keys()
            and all(type(value[key]) is type(wanted) and value[key] == wanted for key, wanted in expected.items()),
            "smtp-receipt")
