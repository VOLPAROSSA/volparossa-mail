# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path
import runpy
import unittest

MODULE = runpy.run_path(str(Path(__file__).resolve().parents[1]/'integration/peer-mail/send_selected.py'))
parse, Ordinary = MODULE['headers'], MODULE['Ordinary']


class AddressTests(unittest.TestCase):
    def message(self, fields):
        return (fields+'\r\n\r\nTo: other@example.test\r\nbody').encode()

    def test_original_headers_and_individual_mailbox_addresses(self):
        for name in ('To','Cc','Bcc'):
            msg=self.message('From: "Owner, A" <Alice@EXAMPLE.TEST>\r\nSender: Alice@example.test\r\n'
                             +name+': =?UTF-8?Q?B=C3=A9b=C3=A9?=\r\n <bob+tag@Example.Test>')
            self.assertEqual(parse(msg),(('Alice','example.test'),('bob+tag','example.test')))

    def test_ambiguous_origins_and_hidden_recipients_require_explicit_ordinary_mail(self):
        for fields in ('To: b@domain.test', 'From: a@domain.test', 'From: a@domain.test, b@domain.test\r\nTo: b@domain.test',
                       'From: a@domain.test\r\nFrom: a@domain.test\r\nTo: b@domain.test',
                       'From: a@domain.test\r\nSender: c@domain.test\r\nTo: b@domain.test',
                       'From: a@domain.test\r\nTo: b@domain.test\r\nBcc: c@domain.test',
                       'From: a@domain.test\r\nTo: b@domain.test\r\nTo: b@domain.test',
                       'From: a@domain.test\r\nTo: group: b@domain.test;',
                       'From: a@domain.test\r\nTo: localpart',
                       'Resent-To: c@domain.test\r\nFrom: a@domain.test\r\nTo: b@domain.test'):
            with self.subTest(fields=fields), self.assertRaises(Ordinary):
                parse(self.message(fields))
