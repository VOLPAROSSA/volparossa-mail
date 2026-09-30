"""Source and inert JavaScript contracts; not Thunderbird/peer runtime proof."""
# SPDX-License-Identifier: GPL-3.0-only

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import prepare_source as source


class CooperativeIntegrationTests(unittest.TestCase):
    def test_transport_consent_cancel_and_native_adapter(self):
        node = os.environ.get("VOLPAROSSA_TEST_NODE") or shutil.which("node")
        self.assertTrue(node, "Set VOLPAROSSA_TEST_NODE to an existing Node executable; no installer is run")
        result = subprocess.run([node, str(ROOT / "tests/cooperative_contract.cjs")],
                                check=True, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.stdout, "cooperative_transport_and_consent_contracts_passed\n")
        self.assertEqual(result.stderr, "")

    def test_exact_pin_and_license_retention(self):
        manifest = source.source_manifest()
        self.assertEqual(manifest["revision"], source.REVISION)
        self.assertFalse(manifest["integration"]["native_assist_provider_api"])
        self.assertFalse(manifest["integration"]["confidential_peer_inference"])
        notice = (ROOT / manifest["license"]["notice"]).read_bytes()
        self.assertEqual(hashlib.sha256(notice).hexdigest(), manifest["license"]["sha256"])
        self.assertIn(b"Mozilla Public License Version 2.0", notice)
        for item in manifest["files"].values():
            self.assertLessEqual(item["size"], source.MAX_SOURCE_BYTES)
            self.assertEqual(len(item["sha256"]), 64)

    def test_no_mail_harvesting_or_network_fallback(self):
        adapter = (ROOT / "integration/VolparossaMailAI.sys.mjs").read_text()
        host = (ROOT / "integration/mail-ai.mjs").read_text()
        panel = (ROOT / "integration/VolparossaCooperativePanel.sys.mjs").read_text()
        transport = (ROOT / "integration/VolparossaCooperativeCompute.sys.mjs").read_text()
        self.assertIn("mail.volparossa.compute.public_socket", transport)
        self.assertNotIn("browser.volparossa.compute.public_socket", transport + panel)
        self.assertIn("Do not submit private correspondence", panel)
        for forbidden in ["MailServices", "gFolderDisplay", "getSelectedMessages", "window.arguments",
                          "fetch(", "XMLHttpRequest", "sendMessage", "innerHTML", "eval("]:
            self.assertNotIn(forbidden, adapter + host)
        self.assertIn('connect-src \'none\'', (ROOT / "integration/mail-ai.html").read_text())
        self.assertIn("panel.ask(question, text)", adapter)
        self.assertNotIn(".submit(", adapter)

    def test_source_edits_are_additive_tools_only(self):
        self.assertEqual(set(source.EDITS), {
            "mail/base/content/messenger-menubar.inc.xhtml", "mail/modules/moz.build", "mail/base/jar.mn"})
        self.assertIn("window.openDialog", source.MENU)
        for path, replacements in source.EDITS.items():
            for old, new in replacements:
                self.assertIn(old, new)
            original = "\n".join(old for old, _ in replacements)
            modified = source.transform(path, original)
            self.assertGreater(len(modified), len(original))
            with self.assertRaises(ValueError):
                source.transform(path, original + original)
            with self.assertRaises(ValueError):
                source.transform(path, "unexpected source")

    def test_local_source_hash_and_symlink_rejection(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            good = b"pinned source\n"
            (directory / "source").write_bytes(good)
            expected = {"size": len(good), "sha256": hashlib.sha256(good).hexdigest()}
            self.assertEqual(source.verified_source("source", expected, directory), good.decode())
            with self.assertRaises(ValueError):
                source.verified_source("source", {**expected, "sha256": "0" * 64}, directory)
            (directory / "link").symlink_to(directory / "source")
            with self.assertRaises(ValueError):
                source.verified_source("link", expected, directory)
            with self.assertRaises(ValueError):
                source.verified_source("source", expected)

    def test_native_assets_have_packaged_routes(self):
        expected = set(source.ADDITIONS.values())
        self.assertEqual(expected, {str(p.relative_to(ROOT)) for p in (ROOT / "integration").iterdir()})
        for path in expected:
            self.assertIn("SPDX-License-Identifier: GPL-3.0-only", (ROOT / path).read_text())
        patch = (ROOT / "patches/0001-cooperative-ai-tools.patch").read_text()
        self.assertIn("volparossaCooperativeAI", patch)
        self.assertIn("VolparossaMailAI.sys.mjs", patch)
        self.assertNotIn("mailnews/", patch)


if __name__ == "__main__":
    unittest.main()
