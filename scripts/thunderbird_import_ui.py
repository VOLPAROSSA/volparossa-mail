# SPDX-License-Identifier: GPL-3.0-only
"""Drive the pinned Thunderbird en-US toolbar, permission prompt and native file chooser."""

import ctypes as ct
import json
import socket
import subprocess
import time

from smoke_stalwart import require


def wait_for(predicate, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.12)
    raise TimeoutError("ui-condition")


class Marionette:
    def __init__(self, stream):
        self.stream = stream
        self.sequence = 0
        require(self.receive().get("applicationType") == "gecko", "marionette-handshake")

    def receive(self):
        header = bytearray()
        while len(header) < 8:
            byte = self.stream.read(1)
            if byte == b":":
                break
            require(byte and byte.isdigit(), "marionette-header")
            header += byte
        else:
            raise ValueError("marionette-frame")
        length = int(header)
        require(0 < length <= 1_048_576, "marionette-length")
        data = self.stream.read(length)
        require(len(data) == length, "marionette-eof")
        return json.loads(data)

    def command(self, name, **options):
        self.sequence += 1
        contents = json.dumps([0, self.sequence, name, options]).encode()
        self.stream.write(str(len(contents)).encode() + b":" + contents)
        self.stream.flush()
        reply = self.receive()
        require(isinstance(reply, list) and len(reply) == 4 and
                reply[:3] == [1, self.sequence, None], "marionette-command")
        value = reply[3]
        return value["value"] if isinstance(value, dict) and set(value) == {"value"} else value

    def chrome(self, code, *args):
        return self.command("WebDriver:ExecuteScript", script=code, args=list(args), newSandbox=True)

    def page(self, code, *args):
        return self.chrome(
            "const actor = document.getElementById('tabmail').currentTabInfo.browser."
            "browsingContext.currentWindowGlobal.getActor('MarionetteCommands');"
            "return actor.executeScript(arguments[0],arguments[1],{timeout:5000,newSandbox:true})",
            code, list(args))


class Display:
    def __init__(self):
        self.x = ct.CDLL("libX11.so.6")
        self.test = ct.CDLL("libXtst.so.6")
        self.x.XOpenDisplay.argtypes = [ct.c_char_p]
        self.x.XOpenDisplay.restype = ct.c_void_p
        self.display = self.x.XOpenDisplay(None)
        require(self.display, "x-display")
        self.x.XSync.argtypes = [ct.c_void_p, ct.c_int]
        self.x.XCloseDisplay.argtypes = [ct.c_void_p]
        self.x.XStringToKeysym.argtypes = [ct.c_char_p]
        self.x.XStringToKeysym.restype = ct.c_ulong
        self.x.XKeysymToKeycode.argtypes = [ct.c_void_p, ct.c_ulong]
        self.x.XKeysymToKeycode.restype = ct.c_uint
        self.test.XTestFakeMotionEvent.argtypes = [ct.c_void_p, ct.c_int, ct.c_int, ct.c_int, ct.c_ulong]
        for name in ("XTestFakeButtonEvent", "XTestFakeKeyEvent"):
            getattr(self.test, name).argtypes = [ct.c_void_p, ct.c_uint, ct.c_int, ct.c_ulong]

    def click(self, x, y):
        self.test.XTestFakeMotionEvent(self.display, -1, round(x), round(y), 0)
        self.test.XTestFakeButtonEvent(self.display, 1, 1, 0)
        self.test.XTestFakeButtonEvent(self.display, 1, 0, 0)
        self.x.XSync(self.display, 0)

    def key(self, name, down):
        code = self.x.XKeysymToKeycode(self.display, self.x.XStringToKeysym(name.encode()))
        require(code, "x-key")
        self.test.XTestFakeKeyEvent(self.display, code, down, 0)
        self.x.XSync(self.display, 0)

    def press(self, name):
        self.key(name, True)
        self.key(name, False)


CENTER = """
const e=document.querySelector(arguments[0]);
e.scrollIntoView({block:'center',behavior:'instant'});
const r=e.getBoundingClientRect();
return [(window.mozInnerScreenX+r.x+r.width/2)*window.devicePixelRatio,
        (window.mozInnerScreenY+r.y+r.height/2)*window.devicePixelRatio];
"""
FIELD = """
const e=document.querySelector(arguments[0]); e.value=arguments[1];
for (const type of ['input','change']) e.dispatchEvent(new Event(type,{bubbles:true}));
"""


def interact(driver, screen, origin, owner, password, account, mailbox, message, events):
    command, chrome, page = driver.command, driver.chrome, driver.page
    session = command("WebDriver:NewSession", capabilities={"alwaysMatch": {}})
    cap = session["capabilities"]
    require(cap["browserVersion"] == "157.0" and cap["moz:buildID"] == "20260928193122",
            "unexpected-thunderbird-build")
    identity = "volparossa-mail-host@volparossa.local"
    require(command("Addon:Install", path="/opt/addon", temporary=True) == identity, "temporary-addon")
    command("Marionette:SetContext", value="chrome")
    wait_for(lambda: chrome("return document.querySelector('button[extension]')?.getBoundingClientRect()?.width"))
    # This first-run account prompt and the host permission doorhanger use the pinned
    # English release at 1280x960. Observe each dialog before sending its native click.
    if chrome("return document.elementFromPoint(850,20)?.tagName.toLowerCase()==='account-hub-container'"):
        screen.click(938, 206)
    wait_for(lambda: chrome("return !!document.elementFromPoint(850,20)?.closest('button[extension]')"))
    screen.click(*chrome(CENTER, f"button[extension='{identity}']"))
    wait_for(lambda: chrome("return document.getElementById('tabmail')?.currentTabInfo?.browser?"
                            ".currentURI.spec.startsWith('moz-extension:')"))
    require(page("return document.title") == "VOLPAROSSA Mail host" and not events, "toolbar")

    def click(selector):
        screen.click(*page(CENTER, selector))

    page(FIELD, "#server", origin)
    page(FIELD, "#username", owner)
    page(FIELD, "#password", password)
    click("#owner-confirmed")
    click("#connect")
    wait_for(lambda: chrome("const p=document.getElementById('addon-webext-permissions-notification');"
                            "return p?.getBoundingClientRect().width>0 && p.getAttribute('buttonlabel')==='Allow'"))
    screen.click(949, 171)
    wait_for(lambda: page("return !document.getElementById('account').disabled"), 20)
    page(FIELD, "#account", account)
    click("#load-mailboxes")
    wait_for(lambda: page("return !document.getElementById('mailbox').disabled"))
    page(FIELD, "#mailbox", mailbox)
    click("#message-file")
    wait_for(lambda: "File Upload" in subprocess.check_output(["xwininfo", "-root", "-tree"], text=True, timeout=3))
    screen.key("Control_L", True)
    screen.press("l")
    screen.key("Control_L", False)
    time.sleep(0.2)
    for character in str(message):
        screen.press({"/": "slash", "-": "minus", ".": "period"}.get(character, character))
    screen.press("Return")
    wait_for(lambda: page("return document.getElementById('message-file').files.length===1"))
    click("#import-confirmed")
    click("#import")
    wait_for(lambda: page("return !document.getElementById('recheck-import').hidden"), 20)
    require("server reported an import" in page("return document.getElementById('status').textContent")
            and [row["operation"] for row in events][-3:] == ["upload", "Email/import", "Email/get"], "import-status")
    click("#recheck-import")
    wait_for(lambda: page("return document.getElementById('recheck-import').hidden && "
                          "document.getElementById('status').textContent.startsWith('Stored metadata confirmed')"), 20)
    require(f"{message.stat().st_size} bytes" in page("return document.getElementById('status').textContent"),
            "stored-size")
    click("#disconnect")
    wait_for(lambda: page("return document.getElementById('status').textContent.startsWith('Disconnected')"))
    require(page("return !document.getElementById('connect').disabled && document.getElementById('recheck-import').hidden"), "disconnect")
    command("Addon:Uninstall", id=identity)
    command("Marionette:Quit", flags=["eAttemptQuit"])
    return {"thunderbird_version": cap["browserVersion"], "build_id": cap["moz:buildID"],
            "native_toolbar": True, "native_file_chooser": True, "metadata_retry_confirmed": True,
            "disconnected": True, "temporary_extension_removed": True}


def run(*args):
    with socket.create_connection(("127.0.0.1", 2828), timeout=10) as sock, sock.makefile("rwb") as stream:
        sock.settimeout(20)
        driver = Marionette(stream)
        display = Display()
        try:
            return interact(driver, display, *args)
        finally:
            display.x.XCloseDisplay(display.display)
