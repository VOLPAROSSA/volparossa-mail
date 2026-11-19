# SPDX-License-Identifier: GPL-3.0-only
"""Run the real peer Mail guest with disposable owner state and no outside network."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def run(args):
    node, stalwart, core = (p.resolve(strict=True) for p in (args.node, args.stalwart, args.core_dir))
    assert os.geteuid() != 0 and node.is_file() and stalwart.is_file() and (core / "volparossa").is_file()
    assert (core / "mailbox-provider-pair").is_file()
    # The shim loses one approved test response after the genuine owner server committed it.
    with tempfile.TemporaryDirectory() as temp:
        tmp = Path(temp)
        store = tmp / "owner"
        store.mkdir(mode=0o700)
        shim = tmp / "lost-import.mjs"
        shim.write_text("""const native = globalThis.fetch;
globalThis.fetch = async (url, options) => {
  const response = await native(url, options);
  if (typeof options.body === "string" && JSON.parse(options.body).methodCalls?.[0]?.[0] === "Email/import") {
    const reply = await response.json();
    if (reply.methodResponses?.[0]?.[1]?.created?.selected?.id) throw Error("synthetic lost response");
    return Response.json(reply);
  }
  return response;
};
""")
        cmd = ["bwrap", "--die-with-parent", "--unshare-all", "--cap-drop", "ALL",
               "--ro-bind", "/usr", "/usr", "--ro-bind", "/etc", "/etc", "--proc", "/proc",
               "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/run", "--tmpfs", "/usr/local"]
        mounts = [(Path("/" + name), "/" + name) for name in ("bin", "lib", "lib64", "sbin")
                  if Path("/" + name).exists()]
        mounts.extend((
            (ROOT / "integration/peer-mail", "/mail/integration/peer-mail"),
            (ROOT / "integration/signal-mail", "/mail/integration/signal-mail"),
            (ROOT / "extensions/volparossa-mail-host", "/mail/extensions/volparossa-mail-host"),
            (ROOT / "tests", "/mail/tests"), (ROOT / "scripts", "/mail/scripts"),
            (ROOT / "packaging/systemd/store.json", "/usr/local/libexec/volparossa-mail/store.json"),
            (ROOT / "packaging/systemd/initialize-owner.py", "/usr/local/libexec/volparossa-mail/initialize-owner.py"),
            (node, "/trial/bin/node"), (core / "volparossa", "/trial/bin/volparossa"),
            (core / "mailbox-provider-pair", "/trial/bin/mailbox-provider-pair"),
            (stalwart, "/usr/local/libexec/volparossa-mail/stalwart"), (shim, "/trial/lost-import.mjs"),
        ))
        for source, dest in mounts:
            cmd.extend(("--ro-bind", str(source), dest))
        cmd.extend(("--bind", str(store), "/var/lib/volparossa-mail", "--clearenv",
                    "--setenv", "PATH", "/usr/bin:/bin", "--setenv", "LANG", "C.UTF-8",
                    "--", "/usr/bin/python3", "-B", "/mail/tests/peer_mail_guest.py"))
        result = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, timeout=300,
                                preexec_fn=lambda: os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[:2]))
        receipt = json.loads(result.stdout)
        assert receipt["provider_acks"] == 2 and receipt["same_email_after_restart"] is True
        return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("node", "stalwart", "core-dir"):
        parser.add_argument("--" + name, required=True, type=Path)
    print(json.dumps(run(parser.parse_args()), sort_keys=True))
