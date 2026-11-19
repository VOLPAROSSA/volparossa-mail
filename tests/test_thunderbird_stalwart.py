# SPDX-License-Identifier: GPL-3.0-only
"""Inert checks for the native Mail trial's inputs, boundaries and recorded evidence."""

import copy
import hashlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import smoke_thunderbird_stalwart as smoke
from thunderbird_import_ui import Marionette
from thunderbird_import_observer import EXPECTED


class Fixture(unittest.TestCase):
    def test_only_owned_outputs_are_writable_to_the_guest(self):
        args = smoke.namespace(Path("/example/work"), Path("/example/runtime"), "net:[1]")
        self.assertIn("--unshare-net", args)
        self.assertIn("--unshare-pid", args)
        self.assertEqual(args.count("--bind"), 1)
        self.assertEqual(args[args.index("--bind") + 1:args.index("--bind") + 3], ["/example/work", "/opt/work"])
        self.assertNotIn("/", args)
        self.assertNotIn("/home", args)
        self.assertNotIn("/workspace", args)

    def test_executable_hash_and_source_match_are_checked_before_launch(self):
        with tempfile.TemporaryDirectory() as temporary:
            exe = Path(temporary) / "program"
            exe.write_bytes(b"fixture")
            exe.chmod(0o700)
            smoke.executable(exe, hashlib.sha256(b"fixture").hexdigest())
            with self.assertRaises(ValueError):
                smoke.executable(exe, "a" * 64)
            link = exe.with_name("link")
            link.symlink_to(exe)
            with self.assertRaises(ValueError):
                smoke.executable(link, hashlib.sha256(b"fixture").hexdigest())
            with patch.object(smoke, "ROOT", exe.parent), patch("subprocess.check_output", return_value=b"other"):
                with self.assertRaises(ValueError):
                    smoke.checked_source("program", "0" * 40)

    def test_marionette_rejects_truncated_and_oversize_frames(self):
        for wire in (b"4:{}", b"9999999:", b"10000000", b"x:", b""):
            with self.subTest(wire=wire), self.assertRaises(ValueError):
                Marionette(io.BytesIO(wire))

    def test_recovery_needs_one_import_one_retry_and_original_bytes_after_client_exit(self):
        receipt = {"kind": "thunderbird-stalwart-owner-restart", "phase": "complete", "success": True,
                   "version": 1, "client_exit_code": 0, "wrong_password_status": 401, "message_bytes": 313,
                   "message_sha256": hashlib.sha256(smoke.server.MESSAGE).hexdigest(), "operations": [
                       {"operation": method, "upstream_status": 200, "delivered_status": 503 if i == 4 else 200}
                       for i, method in enumerate(EXPECTED)],
                   "application": dict(thunderbird_version="157.0", build_id="20260928193122",
                       native_toolbar=True, native_file_chooser=True, metadata_retry_confirmed=True,
                       disconnected=True, temporary_extension_removed=True)}
        for name in ("original_server_readable_after_client_exit", "server_killed_and_offline", "same_sqlite_reopened",
                     "original_account_metadata_and_mime", "process_groups_joined", "private_data_removed"):
            receipt[name] = True
        smoke.verified_receipt(receipt)
        for key, bad in [("client_exit_code", 1), ("message_bytes", 312), ("wrong_password_status", 403),
                         ("original_server_readable_after_client_exit", False), ("same_sqlite_reopened", False),
                         ("operations", receipt["operations"] + [receipt["operations"][3]])]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                smoke.verified_receipt(dict(receipt, **{key: bad}))
        wrong = copy.deepcopy(receipt)
        wrong["operations"][4]["upstream_status"] = 503
        with self.assertRaises(ValueError):
            smoke.verified_receipt(wrong)


if __name__ == "__main__":
    unittest.main()
