# Stalwart source provenance

Pinned upstream: [Stalwart v0.16.24](https://github.com/stalwartlabs/stalwart/releases/tag/v0.16.24),
commit `af37a234981722493b74623a983581691d2b70b6`.
Archive and original notice hashes are in [stalwart-source.json](../stalwart-source.json).

Upstream source notices identify **2020 Stalwart Labs LLC <hello@stalw.art>**.
The original license texts and README are retained byte-for-byte here. The full
prepared source retains every file and snippet notice unchanged. Most server
code offers `AGPL-3.0-only OR LicenseRef-SEL`; enterprise-only files/snippets have
their own terms and are not relabeled as community code.

The intended separate server build selects AGPL-3.0-only community code with
`--no-default-features --features sqlite`. Upstream's default enables enterprise,
so it is not our build recipe. Source preparation does not prove feature closure,
dependency availability, successful compilation or a running mail server.

When distributing an AGPL server binary, preserve its notices and provide the
corresponding source under the applicable license. Modified network-interactive
versions must also offer corresponding source to their remote users as required
by AGPL section 13. Keep dependency licenses and notices with any later build.
The independently written VOLPAROSSA protocol adapter does not incorporate the
Stalwart server into Thunderbird.

## Source-only preparation

From the mail repository, with an already existing `build/` directory:

```sh
python3 scripts/prepare_stalwart.py --fetch --output build/stalwart-0.16.24
python3 scripts/prepare_stalwart.py --validate build/stalwart-0.16.24
```

For a previously fetched archive, substitute `--archive <archive>` for `--fetch`.
Output must be fresh; nothing is overwritten. A failed stage is retained for
inspection, not automatically deleted. Validation is offline and compares the
entire source inventory against the pinned archive. No script compiles code,
starts a server, installs anything or changes DNS, routes or listening ports.

The manifest records the future community build command, **not permission to
run it or proof that it works**. The upstream archive has no compiler-version
pin; select and verify one before the first real build. Bootstrap and account
provisioning must first run inside the disposable integration VM. No public mail
hosting, private SMTP gateway or offline Internet delivery is established here.
