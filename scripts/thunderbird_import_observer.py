# SPDX-License-Identifier: GPL-3.0-only
"""Forward the connector's six loopback requests, interrupting the first metadata reply."""

import base64
from contextlib import closing
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json

from smoke_stalwart import MAIL, require

EXPECTED = ["session", "Mailbox/get", "upload", "Email/import", "Email/get", "Email/get"]


class Observer(ThreadingHTTPServer):
    def __init__(self, owner, password, account, mailbox, message):
        super().__init__(("127.0.0.1", 0), Handler)
        self.account = account
        self.mailbox = mailbox
        self.message = message
        self.auth = "Basic " + base64.b64encode(f"{owner}:{password}".encode()).decode()
        self.events = []
        self.uploaded = None
        self.created = None
        self.failed = False

    def handle_error(self, request, client_address):
        self.failed = True


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def relay(self):
        observer = self.server
        require(self.client_address[0] == "127.0.0.1" and self.headers.get("Authorization") == observer.auth,
                "loopback-authorization")
        size = int(self.headers.get("Content-Length", 0))
        require(0 <= size <= 1_048_576, "request-size")
        body = self.rfile.read(size) if size else None
        if self.command == "GET":
            require(self.path == "/jmap/session", "session-endpoint")
            method = "session"
        elif self.path.startswith("/jmap/upload/"):
            require(body == observer.message, "selected-mime")
            method = "upload"
        else:
            require(self.path in ("/jmap", "/jmap/"), "method-endpoint")
            (call,) = json.loads(body)["methodCalls"]
            method, args, tag = call
            require(method in ("Mailbox/get", "Email/import", "Email/get") and
                    args["accountId"] == observer.account, "method-account")
            if method == "Email/import":
                email = args["emails"]["selected"]
                require(email["mailboxIds"] == {observer.mailbox: True} and
                        email["blobId"] == observer.uploaded, "import-identity")
            elif method == "Email/get":
                require(args["ids"] == [observer.created], "readback-identity")
        index = len(observer.events)
        require(index < len(EXPECTED) and method == EXPECTED[index], "unexpected-operation")
        headers = {k: self.headers[k] for k in ("Authorization", "Content-Type", "Accept", "Host") if k in self.headers}
        with closing(HTTPConnection("127.0.0.1", 8080, timeout=12)) as upstream:
            upstream.request(self.command, self.path, body=body, headers=headers)
            response = upstream.getresponse()
            data = response.read(1_048_577)
            require(response.status == 200 and len(data) <= 1_048_576, "upstream-status")
        payload = json.loads(data)
        if method == "session":
            require(payload["primaryAccounts"][MAIL] == observer.account, "session-account")
        elif method == "upload":
            require(payload["accountId"] == observer.account and payload["size"] == size, "uploaded-size")
            observer.uploaded = payload["blobId"]
        else:
            (reply,) = payload["methodResponses"]
            operation, result, reply_tag = reply
            require(operation == method and reply_tag == tag and result["accountId"] == observer.account,
                    "upstream-method")
            if method == "Email/import":
                observer.created = result["created"]["selected"]["id"]
            elif method == "Email/get":
                (email,) = result["list"]
                require(email["id"] == observer.created and email["mailboxIds"].get(observer.mailbox) is True
                        and email["size"] == len(observer.message), "stored-metadata")
        status = 503 if index == 4 else 200
        if status == 503:
            data = b'{"type":"interrupted-first-metadata-response"}'
        observer.events.append({"operation": method, "upstream_status": 200, "delivered_status": status})
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = relay
    do_POST = relay
