#!/usr/bin/env python3
"""Build the pinned Community development executable; never start a mail service."""
# SPDX-License-Identifier: GPL-3.0-only

import argparse
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import time

import prepare_stalwart as source_stage
import prepare_stalwart_toolchain as tool_stage
from prepare_stalwart import ROOT, digest, require, workspace_path

MAX_STATE_BYTES = 24 * 1024**3
MAX_FILE_BYTES = 2 * 1024**3
MAX_LOG_BYTES = 16 * 1024**2


def size_bound(root):
    total = 0
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if not (Path(directory) / name).is_symlink()]
        for name in names:
            path = Path(directory) / name
            try:
                if not path.is_symlink():
                    size = path.stat().st_size
                    require(size <= MAX_FILE_BYTES, "build file size bound exceeded")
                    total += size
            except FileNotFoundError:
                pass  # Cargo atomically renames its own temporary files.
    require(total <= MAX_STATE_BYTES, "build disk bound exceeded")
    return total


def child_limits():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_FILE_BYTES, MAX_FILE_BYTES))
    cpus = sorted(os.sched_getaffinity(0))
    os.sched_setaffinity(0, cpus[:2])


def joined(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=10)


def run_step(name, argv, env, state, source, *, network, seconds):
    # Only the build-state subtree and disposable /tmp are writable. Verified
    # sources, compiler and the real host are read-only. Compilation has no net.
    command = ["/usr/bin/bwrap", "--die-with-parent", "--ro-bind", "/", "/",
               "--bind", str(state), str(state), "--tmpfs", "/tmp",
               "--proc", "/proc", "--dev", "/dev", "--chdir", str(source)]
    if not network:
        command.append("--unshare-net")
    command += ["--"] + argv
    stamp = time.time_ns()
    log = state / f"{name}-{stamp}.log"
    receipt = state / f"{name}-{stamp}.json"
    start = time.monotonic()
    report = {"step": name, "argv": argv, "network_enabled": network,
              "source_read_only": True, "compiler_jobs": 2, "profile": "dev",
              "log": log.name, "passed": False, "server_executed": False}
    process = None
    try:
        with log.open("xb") as output:
            process = subprocess.Popen(command, env=env, stdout=output, stderr=subprocess.STDOUT,
                                       start_new_session=True, preexec_fn=child_limits)
            print(json.dumps({"step": name, "pid": process.pid, "log": str(log)}), flush=True)
            last_size_check = 0
            while process.poll() is None:
                elapsed = time.monotonic() - start
                require(elapsed < seconds, "build step deadline exceeded")
                require(log.stat().st_size <= MAX_LOG_BYTES, "build log bound exceeded")
                if elapsed - last_size_check >= 10:
                    size_bound(state)
                    last_size_check = elapsed
                time.sleep(1)
            report["exit_code"] = process.returncode
            require(process.returncode == 0, f"{name} exited {process.returncode}; retained {log.name}")
            report["passed"] = True
    except BaseException as error:
        report["error"] = str(error)
        raise
    finally:
        if process is not None:
            joined(process)
        report["elapsed_seconds"] = round(time.monotonic() - start, 3)
        report["log_sha256"] = digest(log) if log.exists() else None
        report["state_bytes"] = size_bound(state)
        with receipt.open("x") as output:
            json.dump(report, output, indent=2, sort_keys=True)
            output.write("\n")
        print(json.dumps({"step": name, "passed": report["passed"],
                          "receipt": str(receipt)}), flush=True)
    return receipt


def build(args):
    source_report = source_stage.validate(args.source)
    tool_report = tool_stage.validate(args.toolchain)
    source = args.source.resolve() / "source"
    toolchain = args.toolchain.resolve() / "toolchain"
    state = workspace_path(args.output, fresh=not args.resume)
    binding = {"version": 1, "source_revision": source_report["revision"],
               "source_archive_sha256": source_report["archive_sha256"],
               "lock_sha256": digest(source / "Cargo.lock"),
               "toolchain_manifest_sha256": tool_report["pin"]["manifest_sha256"],
               "profile": "dev", "features": ["sqlite"], "default_features": False,
               "jobs": 2, "debug": 0, "incremental": False}
    if args.resume:
        marker = state / "BUILD_OWNER.json"
        require(marker.is_file() and not marker.is_symlink() and
                json.loads(marker.read_text()) == binding, "build ownership/input mismatch")
    else:
        state.mkdir(mode=0o700)
        with (state / "BUILD_OWNER.json").open("x") as output:
            json.dump(binding, output, indent=2, sort_keys=True)
            output.write("\n")
        for directory in ("cargo", "target", "tmp"):
            (state / directory).mkdir(mode=0o700)
    for directory in ("cargo", "target", "tmp"):
        require((state / directory).is_dir() and not (state / directory).is_symlink(),
                "invalid build state directory")
    env = {name: value for name, value in os.environ.items()
           if name in ("HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TZ")}
    env.update({"PATH": str(toolchain / "bin") + ":/usr/bin:/bin",
                "CARGO_HOME": str(state / "cargo"), "CARGO_TARGET_DIR": str(state / "target"),
                "RUSTC": str(toolchain / "bin/rustc"), "RUSTDOC": str(toolchain / "bin/rustdoc"),
                "TMPDIR": str(state / "tmp"), "CARGO_BUILD_JOBS": "2",
                "CARGO_INCREMENTAL": "0", "CARGO_PROFILE_DEV_DEBUG": "0",
                "CARGO_PROFILE_TEST_DEBUG": "0", "CARGO_NET_RETRY": "0",
                "CARGO_HTTP_TIMEOUT": "60", "CARGO_TERM_COLOR": "never",
                "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_TERMINAL_PROMPT": "0"})
    cargo = str(toolchain / "bin/cargo")
    receipts = []
    if args.fetch:
        receipts.append(run_step("fetch", [cargo, "fetch", "--locked", "--target",
                        "x86_64-unknown-linux-gnu"], env, state, source, network=True, seconds=1800))
    receipts.append(run_step("compile", [cargo, "build", "--offline", "--locked", "-p",
                    "stalwart", "--no-default-features", "--features", "sqlite"],
                    env, state, source, network=False, seconds=3600))
    source_stage.validate(args.source)
    executable = state / "target/debug/stalwart"
    require(executable.is_file() and not executable.is_symlink(), "build produced no executable")
    print(json.dumps({"community_compiled": True, "profile": "dev", "source_unchanged": True,
                      "executable": str(executable), "sha256": digest(executable),
                      "bytes": executable.stat().st_size, "server_executed": False,
                      "receipts": [path.name for path in receipts]}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "build/stalwart-0.16.24")
    parser.add_argument("--toolchain", type=Path, default=ROOT / "build/stalwart-rust-1.98.1")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fetch", action="store_true", help="explicit locked dependency download")
    parser.add_argument("--resume", action="store_true", help="resume an exactly bound owned state")
    build(parser.parse_args())


if __name__ == "__main__":
    main()
