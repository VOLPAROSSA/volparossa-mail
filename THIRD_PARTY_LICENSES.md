# Third-party source and licenses

Original VOLPAROSSA integration code is GPL-3.0-only. This does not relicense upstream
Thunderbird, Gecko or Stalwart code.

## Thunderbird and Gecko

- Source: https://github.com/thunderbird/thunderbird-desktop
- Thunderbird 157.0 revision: `c747bb0160f873ab78692b2450612b232d38c30e`.
- Paired Gecko source: https://hg.mozilla.org/releases/mozilla-release
- Gecko revision: `8eb25af4acf031ab1e06abf1a912275083c820ed`.
- The patched upstream files retain MPL-2.0 and their original notices. The unmodified
  [MPL-2.0 text](third_party/MPL-2.0.txt) has SHA-256
  `fab3dd6bdab226f1c08630b1dd917e11fcb4ec5e1e020e2c16f83a0a13863e85`.
- Local patch: `patches/0001-cooperative-ai-tools.patch`; adds an explicit Tools entry,
  module registration and packaged panel assets. It does not alter account delivery.
- Source input sizes/hashes and the exact overlay are checked by `scripts/prepare_source.py`.

## Reused VOLPAROSSA browser transport

- Source: https://github.com/VOLPAROSSA/volparossa-browser
- Revision: `3c7894168ce6c1f28e96ce5b95bc99928e6c4304`.
- GPL-3.0-only public cooperative IPC/panel modules are adapted for Thunderbird's separate
  `mail.volparossa.compute.public_socket` setting. They do not provide private mail inference.

## Stalwart Community

- Source: https://github.com/stalwartlabs/stalwart
- Version: `v0.16.24`; revision `af37a234981722493b74623a983581691d2b70b6`.
- Community code is AGPL-3.0-only, with upstream commercial alternatives and separately
  restricted enterprise sections. The proposed source build explicitly excludes the
  default enterprise feature; it has not yet been build-verified here.
- Stalwart is a separate service, not code relabelled under this repository's GPL license.
  Original source/license notices and corresponding source must accompany redistribution
  as applicable. Modified network-accessible AGPL service code must offer corresponding
  source to its users.
- Source preparation and the mail extension are development candidates. No Stalwart
  executable is shipped or automatically downloaded by the Thunderbird AI overlay.
- Exact archive hashes and the unchanged AGPL/SEL texts are retained in
  [the source manifest](third_party/stalwart-source.json) and
  [provenance directory](third_party/stalwart/NOTICE.md). The independently written JMAP
  adapter records the source-verified protocol paths in
  [its interoperability pin](extensions/volparossa-mail-host/upstream.json).
