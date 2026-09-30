#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Focused offline command isolation and failure-receipt checks; no compiler starts."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_stalwart as build


class BuildTests(unittest.TestCase):
    def test_compile_is_offline_and_only_owned_state_is_writable(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            process = Mock(pid=123456, returncode=0)
            process.poll.return_value = 0
            with patch.object(build.subprocess, "Popen", return_value=process) as launch, \
                    patch.object(build, "joined") as join:
                receipt = build.run_step("compile", ["/compiler/cargo", "build", "--offline"],
                                         {"HOME": "/unchanged"}, state, Path("/source"),
                                         network=False, seconds=30)
            command = launch.call_args.args[0]
            self.assertIn("--unshare-net", command)
            self.assertEqual(command[command.index("--ro-bind") + 1:][:2], ["/", "/"])
            self.assertEqual(command[command.index("--bind") + 1:][:2], [str(state), str(state)])
            self.assertEqual(launch.call_args.kwargs["env"]["HOME"], "/unchanged")
            self.assertTrue(launch.call_args.kwargs["start_new_session"])
            self.assertTrue(json.loads(receipt.read_text())["passed"])
            join.assert_called_once_with(process)

    def test_failed_step_retains_failure_receipt_and_joins_children(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            process = Mock(pid=123456, returncode=17)
            process.poll.return_value = 17
            with patch.object(build.subprocess, "Popen", return_value=process), \
                    patch.object(build, "joined") as join:
                with self.assertRaisesRegex(ValueError, "exited 17"):
                    build.run_step("compile", ["/compiler/cargo"], {}, state, Path("/source"),
                                   network=False, seconds=30)
            receipt = json.loads(next(state.glob("compile-*.json")).read_text())
            self.assertFalse(receipt["passed"])
            self.assertEqual(receipt["exit_code"], 17)
            self.assertFalse(receipt["server_executed"])
            join.assert_called_once_with(process)

    def test_disk_bound_does_not_follow_external_symlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            (state / "payload").write_bytes(b"1234")
            (state / "external").symlink_to("/usr")
            self.assertEqual(build.size_bound(state), 4)
            with patch.object(build, "MAX_STATE_BYTES", 3):
                with self.assertRaisesRegex(ValueError, "disk bound"):
                    build.size_bound(state)


if __name__ == "__main__":
    unittest.main()
