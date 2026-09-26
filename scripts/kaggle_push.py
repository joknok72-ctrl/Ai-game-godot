#!/usr/bin/env python3
"""Push / inspect the godotai training kernel on Kaggle — without ever handling the token.

    python3 scripts/kaggle_push.py                 # check prerequisites, render metadata, do nothing remote
    python3 scripts/kaggle_push.py --push          # kaggle kernels push  (uses GPU T4 x2 by default)
    python3 scripts/kaggle_push.py --status        # kaggle kernels status <user>/godotai-sft
    python3 scripts/kaggle_push.py --output out/   # download the adapter produced by the last run

Authentication is left entirely to the official ``kaggle`` CLI, which reads
``$KAGGLE_API_TOKEN`` or ``~/.kaggle/access_token`` (kaggle-cli docs, read 2026-09-26).
This script only checks that *one of them exists*; it never reads, prints or copies
the value. The Kaggle username comes from ``$KAGGLE_USERNAME`` and is substituted
into ``training/kaggle/kernel-metadata.json`` in a temporary copy.

STATUS: exercised offline (prerequisite checks + metadata rendering are unit-tested);
a live ``--push`` was **not** run by the tooling that produced this repository.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
KERNEL_DIR = REPO / "training" / "kaggle"
ACCELERATORS = ("NvidiaTeslaT4", "NvidiaL4", "TpuV5E8", "TpuV6E8", "NvidiaTeslaA100", "NvidiaH100")  # kaggle-cli docs, Sep 2026


def token_configured(env: dict[str, str] | None = None, home: Path | None = None) -> str | None:
    """Name of the configured auth source, or None. Never returns the secret itself."""
    env = os.environ if env is None else env
    home = home or Path.home()
    if env.get("KAGGLE_API_TOKEN", "").strip():
        return "KAGGLE_API_TOKEN (environment)"
    if (home / ".kaggle" / "access_token").is_file():
        return "~/.kaggle/access_token"
    if (home / ".kaggle" / "kaggle.json").is_file():
        return "~/.kaggle/kaggle.json (legacy credentials)"
    if env.get("KAGGLE_USERNAME") and env.get("KAGGLE_KEY"):
        return "KAGGLE_USERNAME + KAGGLE_KEY (legacy environment)"
    return None


def render_metadata(username: str, accelerator: str | None = None, src: Path = KERNEL_DIR) -> dict:
    meta = json.loads((src / "kernel-metadata.json").read_text(encoding="utf-8"))
    for key in ("id",):
        meta[key] = meta[key].replace("KAGGLE_USERNAME", username)
    meta["dataset_sources"] = [d.replace("KAGGLE_USERNAME", username) for d in meta.get("dataset_sources", [])]
    if accelerator:
        if accelerator not in ACCELERATORS:
            raise SystemExit(f"unknown accelerator {accelerator!r}; documented: {', '.join(ACCELERATORS)}")
        meta["machine_shape"] = accelerator
    if not (src / meta["code_file"]).is_file():
        raise SystemExit(f"code_file {meta['code_file']} not found in {src}")
    return meta


def stage(meta: dict, src: Path = KERNEL_DIR) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="godotai-kaggle-"))
    shutil.copy2(src / meta["code_file"], tmp / meta["code_file"])
    (tmp / "kernel-metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return tmp


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--push", action="store_true")
    p.add_argument("--status", action="store_true")
    p.add_argument("--output", metavar="DIR", help="download the latest run's output files to DIR")
    p.add_argument("--accelerator", default="NvidiaTeslaT4", help="|".join(ACCELERATORS))
    p.add_argument("--timeout", type=int, default=11 * 3600, help="max run seconds (Kaggle GPU cap is 12 h)")
    p.add_argument("--no-run", action="store_true", help="save a version without executing it")
    p.add_argument("--check", action="store_true",
                   help="offline self-check (CI): render + stage the kernel metadata and byte-compile the "
                        "kernel script; no credentials needed, nothing is sent to Kaggle")
    args = p.parse_args(argv)

    if args.check:
        import py_compile

        username = os.environ.get("KAGGLE_USERNAME", "").strip() or "KAGGLE_USERNAME"
        meta = render_metadata(username, args.accelerator)
        staged = stage(meta)
        try:
            kernel_py = next(staged.glob("*.py"))
            py_compile.compile(str(kernel_py), cfile=os.path.join(tempfile.gettempdir(), "godotai_kernel_check.pyc"), doraise=True)
            print(f"check ok: kernel {meta['id']}  accelerator {meta['machine_shape']}  "
                  f"files staged: {sorted(f.name for f in staged.iterdir())}")
        finally:
            shutil.rmtree(staged, ignore_errors=True)
        print(f"auth source: {token_configured() or 'none (fine for --check; needed for --push/--status/--output)'}")
        return 0

    username = os.environ.get("KAGGLE_USERNAME", "").strip()
    if not username:
        print("KAGGLE_USERNAME is not set (your kaggle.com user slug; it is not a secret)", file=sys.stderr)
        return 2
    auth = token_configured()
    if not auth:
        print("no Kaggle credentials found — set KAGGLE_API_TOKEN or create ~/.kaggle/access_token "
              "(kaggle.com → Settings → API). Never paste the token into chats, issues or commits.", file=sys.stderr)
        return 2
    print(f"auth source: {auth}")
    meta = render_metadata(username, args.accelerator)
    slug = meta["id"]
    print(f"kernel: {slug}  accelerator: {meta['machine_shape']}  dataset: {', '.join(meta['dataset_sources'])}")

    if not (args.push or args.status or args.output):
        print("dry run only — pass --push / --status / --output to talk to Kaggle")
        return 0
    if shutil.which("kaggle") is None:
        print("the `kaggle` CLI is not installed: pip install kaggle", file=sys.stderr)
        return 2
    if args.push:
        staged = stage(meta)
        cmd = ["kaggle", "kernels", "push", "-p", str(staged), "--accelerator", meta["machine_shape"],
               "--timeout", str(args.timeout)]
        if args.no_run:
            cmd.append("--no-run")
        print("+", " ".join(cmd))
        rc = subprocess.run(cmd, check=False).returncode
        shutil.rmtree(staged, ignore_errors=True)
        if rc:
            return rc
    if args.status:
        rc = subprocess.run(["kaggle", "kernels", "status", slug], check=False).returncode
        if rc:
            return rc
    if args.output:
        Path(args.output).mkdir(parents=True, exist_ok=True)
        rc = subprocess.run(["kaggle", "kernels", "output", slug, "-p", args.output], check=False).returncode
        if rc:
            return rc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
