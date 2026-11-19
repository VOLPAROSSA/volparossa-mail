#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Exercise a real Thunderbird import, its metadata retry and Stalwart process recovery."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error

import smoke_stalwart as server
from smoke_stalwart import require

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ("smoke_stalwart.py", "smoke_thunderbird_stalwart.py", "thunderbird_import_ui.py",
           "thunderbird_import_observer.py")
PRIVATE = Path("/opt/work/private")
PREFERENCES = {
    "marionette.port": 2828, "mail.shell.checkDefaultClient": False,
    "mailnews.start_page.enabled": False, "app.update.enabled": False,
    "datareporting.policy.dataSubmissionEnabled": False, "extensions.update.enabled": False,
}


def port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def retire(process):
    if server.group_join(process):
        return True
    for _ in range(30):
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.1)
    return False


def mailbox(admin, password):
    domain = server.created(server.call("fixture-admin", admin, "x:Domain/set", {"create": {"fixture": {
        "name": "volparossa.test", "isEnabled": True, "certificateManagement": {"@type": "Manual"},
        "dnsManagement": {"@type": "Manual"}, "dkimManagement": {"@type": "Manual"}}}}), "fixture")
    account = server.created(server.call("fixture-admin", admin, "x:Account/set",
        {"create": {"fixture": server.account_object(domain, password)}}), "fixture")
    owner = "owner@volparossa.test"
    server.verify_owner(owner, password, account)
    destination = server.created(server.call(owner, password, "Mailbox/set", {"accountId": account,
        "create": {"fixture": {"name": "VOLPAROSSA fixture", "parentId": None}}}), "fixture")
    return owner, account, destination


def stored_message(owner, password, account, destination, email_id):
    result = server.call(owner, password, "Email/get", {"accountId": account, "ids": [email_id],
        "properties": ["id", "blobId", "mailboxIds", "size"]})
    require(result["accountId"] == account and not result["notFound"] and len(result["list"]) == 1,
            "mailbox-readback")
    saved = result["list"][0]
    require(saved["id"] == email_id and saved["mailboxIds"].get(destination) is True
            and saved["size"] == len(server.MESSAGE), "message-binding")
    blob = saved["blobId"]
    require(blob.isascii() and blob.isalnum(), "blob-binding")
    actual = server.http(owner, password, f"/jmap/download/{account}/{blob}/fixture.eml", raw=True)
    require(actual == server.MESSAGE, "mailbox-mime")
    return saved


def exercise(report, processes, log):
    from thunderbird_import_observer import EXPECTED, Observer
    from thunderbird_import_ui import run, wait_for

    for name in ("tmp", "runtime", "profile", "home"):
        (PRIVATE / name).mkdir(mode=0o700)
    os.environ.update(DISPLAY=":143", XDG_RUNTIME_DIR=str(PRIVATE / "runtime"), HOME=str(PRIVATE / "home"),
                      TMPDIR=str(PRIVATE / "tmp"), MOZ_CRASHREPORTER_DISABLE="1")
    admin, password = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    database = PRIVATE / "store.sqlite"
    server.write_json(PRIVATE / "store.json", {"@type": "Sqlite", "path": str(database),
                                               "poolWorkers": 2, "poolMaxConnections": 4})
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "TMPDIR": str(PRIVATE / "tmp"),
                   "STALWART_HOSTNAME": "mail.volparossa.test", "STALWART_RECOVERY_MODE": "1",
                   "STALWART_RECOVERY_ADMIN": "fixture-admin:" + admin}
    report["phase"] = "mailbox-provision"
    original = server.start_server(environment, log, admin, processes)
    owner, account, destination = mailbox(admin, password)
    message = PRIVATE / "fixture.eml"
    message.write_bytes(server.MESSAGE)
    profile = PRIVATE / "profile"
    (profile / "user.js").write_text("".join(
        f"user_pref({json.dumps(k)}, {json.dumps(v)});\n" for k, v in PREFERENCES.items()))
    Path("/tmp/.X11-unix").mkdir(mode=0o1777, exist_ok=True)

    def launch(args):
        child = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        processes.append(child)
        return child

    report["phase"] = "display"
    launch(["/opt/Xvfb", ":143", "-screen", "0", "1280x960x24", "-nolisten", "tcp"])
    wait_for(lambda: Path("/tmp/.X11-unix/X143").exists())
    with Observer(owner, password, account, destination, server.MESSAGE) as observer:
        thread = threading.Thread(target=observer.serve_forever, daemon=True)
        thread.start()
        try:
            report["phase"] = "thunderbird-ui"
            client = launch(["dbus-run-session", "--", "/opt/thunderbird/thunderbird", "--no-remote",
                             "--profile", str(profile), "--marionette", "--remote-allow-system-access"])
            wait_for(lambda: port_open(2828), 30)
            report["application"] = run("http://127.0.0.1:" + str(observer.server_port), owner, password,
                                        account, destination, message, observer.events)
            report["phase"] = "client-exit"
            require(client.wait(timeout=30) == 0 and retire(client) and not port_open(2828), "thunderbird-quit")
            processes.remove(client)
            require(not observer.failed and [e["operation"] for e in observer.events] == EXPECTED
                    and [e["delivered_status"] for e in observer.events] == [200, 200, 200, 200, 503, 200],
                    "observed-operations")
            report["operations"] = observer.events
            email_id = observer.created
        finally:
            observer.shutdown()
            thread.join(timeout=5)
            require(not thread.is_alive(), "observer-join")
    shutil.rmtree(profile)
    message.unlink()
    require(not profile.exists() and not message.exists(), "client-data-removal")
    report["client_exit_code"] = client.returncode
    report["phase"] = "server-after-client-exit"
    require(original.poll() is None, "original-server-exited")
    server.verify_owner(owner, password, account)
    before = stored_message(owner, password, account, destination, email_id)
    report["original_server_readable_after_client_exit"] = True

    report["phase"] = "server-process-loss"
    inode = database.stat()
    server.crash_server(original)
    processes.remove(original)
    report["server_killed_and_offline"] = True
    report["phase"] = "sqlite-reopen"
    server.start_server(environment, log, admin, processes)
    require(os.path.samestat(inode, database.stat()), "sqlite-replaced")
    try:
        server.http(owner, secrets.token_urlsafe(32), "/jmap/session")
    except urllib.error.HTTPError as error:
        with error:
            require(error.code == 401, "credential-status")
    else:
        raise ValueError("credential-accepted")
    server.verify_owner(owner, password, account)
    require(stored_message(owner, password, account, destination, email_id) == before, "metadata-changed")
    report.update(phase="complete", same_sqlite_reopened=True, wrong_password_status=401,
                  original_account_metadata_and_mime=True, message_bytes=len(server.MESSAGE),
                  message_sha256=hashlib.sha256(server.MESSAGE).hexdigest())


def guest(host_namespace):
    report = {"version": 1, "kind": "thunderbird-stalwart-owner-restart", "phase": "namespace"}
    processes = []
    try:
        require(os.geteuid() != 0 and os.readlink("/proc/self/ns/net") != host_namespace
                and socket.if_nameindex() == [(1, "lo")] and not Path("/proc/net/route").read_text().splitlines()[1:],
                "namespace-boundary")
        PRIVATE.mkdir(mode=0o700)
        with (PRIVATE / "process.log").open("xb") as log:
            exercise(report, processes, log)
    except Exception as exc:
        report["error_class"] = type(exc).__name__
        import traceback
        # Fixed-source line numbers aid diagnosis without exposing request bodies, credentials or paths.
        report["error_line"] = traceback.extract_tb(exc.__traceback__)[-1].lineno
    finally:
        report["process_groups_joined"] = all([retire(p) for p in reversed(processes)])
        if report["process_groups_joined"] and PRIVATE.is_dir() and not PRIVATE.is_symlink():
            shutil.rmtree(PRIVATE)
        report["private_data_removed"] = not PRIVATE.exists()
        report["success"] = report["phase"] == "complete" and report["process_groups_joined"] and report["private_data_removed"]
        server.write_json(PRIVATE.parent / "ui-restart.json", report)
    return 0 if report["success"] else 1


def namespace(work, stage, netns):
    args = ["/usr/bin/bwrap", "--die-with-parent", "--new-session", "--unshare-user",
            "--uid", str(os.getuid()), "--gid", str(os.getgid()), "--unshare-net", "--unshare-pid",
            "--unshare-ipc", "--unshare-uts", "--cap-drop", "ALL", "--ro-bind", "/usr", "/usr",
            "--ro-bind", "/etc", "/etc", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/run"]
    for name in ("bin", "sbin", "lib", "lib64"):
        args.extend(["--ro-bind", "/" + name, "/" + name])
    for name in ("stalwart", "Xvfb", "thunderbird", "addon", "tools"):
        args.extend(["--ro-bind", str(stage / name), "/opt/" + name])
    return args + ["--bind", str(work), "/opt/work", "--chdir", "/opt/work", "--clearenv",
                   "--setenv", "PATH", "/usr/bin:/bin", "--setenv", "LANG", "C.UTF-8", "--",
                   "/usr/bin/python3", "-B", "/opt/tools/smoke_thunderbird_stalwart.py", "--inside", netns]


def verified_receipt(value):
    from thunderbird_import_observer import EXPECTED
    flags = {"original_server_readable_after_client_exit", "server_killed_and_offline", "same_sqlite_reopened",
             "original_account_metadata_and_mime", "process_groups_joined", "private_data_removed"}
    require(set(value) == flags | {"version", "kind", "phase", "success", "application", "client_exit_code",
            "wrong_password_status", "message_bytes", "message_sha256", "operations"}, "unknown-receipt-field")
    require(type(value["version"]) is int and value["version"] == 1 and
            value["kind"] == "thunderbird-stalwart-owner-restart" and value["phase"] == "complete"
            and value["success"] is True and type(value["client_exit_code"]) is int and value["client_exit_code"] == 0
            and value.get("wrong_password_status") == 401 and value.get("message_bytes") == len(server.MESSAGE)
            and value.get("message_sha256") == hashlib.sha256(server.MESSAGE).hexdigest(), "recovery-result")
    for flag in flags:
        require(value.get(flag) is True, "recovery-observation")
    expected = [{"operation": method, "upstream_status": 200, "delivered_status": 503 if i == 4 else 200}
                for i, method in enumerate(EXPECTED)]
    require(value.get("operations") == expected, "metadata-retry")
    application = value.get("application", {})
    require(set(application) == {"thunderbird_version", "build_id", "native_toolbar", "native_file_chooser",
            "metadata_retry_confirmed", "disconnected", "temporary_extension_removed"}, "unknown-ui-field")
    require(application.get("thunderbird_version") == "157.0" and application.get("build_id") == "20260928193122"
            and all(application.get(flag) is True for flag in ("native_toolbar", "native_file_chooser",
                    "metadata_retry_confirmed", "disconnected", "temporary_extension_removed")), "native-ui")


def checked_source(relative, revision):
    data = subprocess.check_output(["git", "-C", str(ROOT), "show", f"{revision}:{relative}"])
    require((ROOT / relative).read_bytes() == data, "uncommitted-fixture-source")
    return data


def executable(path, expected):
    require(path.is_absolute() and path.is_file() and not path.is_symlink() and os.access(path, os.X_OK)
            and re.fullmatch("[0-9a-f]{64}", expected) and server.digest(path) == expected, "runtime-digest")


def host(args):
    from prepare_stalwart import workspace_path
    require(os.geteuid() != 0, "nonroot-required")
    work = workspace_path(args.output, fresh=True)
    revision = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    pins = json.loads(checked_source("third_party/thunderbird-extension-smoke.json", revision))["runtime_inputs"]
    archive = args.thunderbird_archive
    require(archive.is_absolute() and archive.is_file() and not archive.is_symlink()
            and archive.stat().st_size == pins["distribution_bytes"], "thunderbird-archive")
    with archive.open("rb") as raw:
        require(hashlib.file_digest(raw, "sha512").hexdigest() == pins["distribution_sha512"], "thunderbird-sha512")
    for path, expected in ((args.stalwart, args.stalwart_sha256), (args.xvfb, args.xvfb_sha256)):
        executable(path, expected)
    sources = {f"tools/{name}": checked_source(f"scripts/{name}", revision) for name in SCRIPTS}
    for name in (*pins["extension_files_sha256"], "NOTICE.txt", "upstream.json"):
        sources["addon/" + name] = checked_source("extensions/volparossa-mail-host/" + name, revision)
    plan = {"version": 1, "source_revision": revision, "thunderbird_sha512": pins["distribution_sha512"],
            "stalwart_sha256": args.stalwart_sha256, "xvfb_sha256": args.xvfb_sha256,
            "source_sha256": {name: hashlib.sha256(data).hexdigest() for name, data in sources.items()}}
    if not args.execute:
        print(json.dumps(plan, sort_keys=True))
        return 0
    work.mkdir(mode=0o700)
    server.write_json(work / "inputs.json", plan)
    before = server.host_snapshot()
    boundary = {"version": 1, "group_joined": False, "exit_status": None}
    process = None
    try:
        with tempfile.TemporaryDirectory(dir=work.parent, prefix="thunderbird-runtime-") as staging:
            stage = Path(staging)
            # Use private copies of the submitted bytes for the entire run.
            distribution = stage / "mozilla.tar.xz"
            shutil.copyfile(archive, distribution)
            with distribution.open("rb") as raw:
                require(hashlib.file_digest(raw, "sha512").hexdigest() == pins["distribution_sha512"], "thunderbird-sha512")
            with tarfile.open(distribution, "r:xz") as release:
                release.extractall(stage, filter="data")
            distribution.unlink()
            require(server.digest(stage / "thunderbird/thunderbird") == pins["launcher_sha256"], "launcher-digest")
            for source, name, expected in ((args.stalwart, "stalwart", args.stalwart_sha256),
                                           (args.xvfb, "Xvfb", args.xvfb_sha256)):
                destination = stage / name
                shutil.copyfile(source, destination)
                require(server.digest(destination) == expected, "staged-executable-digest")
                destination.chmod(0o500)
            for name, data in sources.items():
                path = stage / name
                path.parent.mkdir(mode=0o700, exist_ok=True)
                path.write_bytes(data)
            with (work / "private-boundary.log").open("xb") as log:
                process = subprocess.Popen(namespace(work, stage, before["netns"]), stdout=log,
                                           stderr=subprocess.STDOUT, start_new_session=True)
                try:
                    boundary["exit_status"] = process.wait(timeout=330)
                    if boundary["exit_status"] == 0:
                        verified_receipt(json.loads((work / "ui-restart.json").read_text()))
                        boundary["receipt_verified"] = True
                finally:
                    boundary["group_joined"] = retire(process)
    finally:
        boundary["host_network_unchanged"] = before == server.host_snapshot()
        private = work / "private"
        if boundary["group_joined"] and private.is_dir() and not private.is_symlink():
            shutil.rmtree(private)
        boundary["private_data_removed"] = not private.exists() and not private.is_symlink()
        (work / "private-boundary.log").unlink(missing_ok=True)
        boundary["success"] = (boundary.get("receipt_verified") is True and boundary["group_joined"] and
                               boundary["host_network_unchanged"] and boundary["private_data_removed"])
        server.write_json(work / "boundary.json", boundary)
    print(json.dumps(boundary, sort_keys=True))
    return 0 if boundary["success"] else 1


def main():
    os.umask(0o077)
    if len(sys.argv) == 3 and sys.argv[1] == "--inside":
        return guest(sys.argv[2])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--thunderbird-archive", type=Path, required=True)
    parser.add_argument("--stalwart", type=Path, required=True)
    parser.add_argument("--stalwart-sha256", required=True)
    parser.add_argument("--xvfb", type=Path, required=True)
    parser.add_argument("--xvfb-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    return host(parser.parse_args())


if __name__ == "__main__":
    sys.exit(main())
