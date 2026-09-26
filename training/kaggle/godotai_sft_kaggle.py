#!/usr/bin/env python3
"""Kaggle *script kernel*: fine-tune an open-weight model on godotai trajectories.

Pushed by ``python3 scripts/kaggle_push.py --push`` (see training/README.md). What it
expects on Kaggle (all documented in the kaggle-cli docs, read 2026-09-26):

* accelerator ``NvidiaTeslaT4`` = "GPU T4 ×2" (``machine_shape`` in kernel-metadata.json);
  sessions are limited to 12 h for GPU notebooks, output under ``/kaggle/working`` (20 GB)
  is persisted with the kernel version;
* ``enable_internet: true`` — needed to ``pip install`` and to clone the repository;
* a private Kaggle *Dataset* ``<user>/godotai-sft-data`` containing ``sft.jsonl`` from
  ``python3 -m godotai dataset extract`` (mounted read-only at /kaggle/input/…);
* optional Kaggle *Secret* ``HF_TOKEN`` (Add-ons → Secrets) for gated base models —
  read with ``kaggle_secrets.UserSecretsClient``; no Kaggle/Cloudflare token is ever
  needed inside the kernel.

STATUS: written against the documented environment, **not executed on Kaggle** by the
tooling that produced it. The first push is an experiment; check ``kaggle kernels status``
and the log before trusting the output.
"""
from __future__ import annotations

import glob
import os
import subprocess
import sys
from pathlib import Path

REPO_URL = os.environ.get("GODOTAI_REPO", "https://github.com/joknok72-ctrl/Ai-game-godot.git")
REPO_REF = os.environ.get("GODOTAI_REF", "main")
BASE_MODEL = os.environ.get("GODOTAI_BASE_MODEL", "Qwen/Qwen2.5-Coder-7B-Instruct")
MAX_SEQ = os.environ.get("GODOTAI_MAX_SEQ", "4096")
EPOCHS = os.environ.get("GODOTAI_EPOCHS", "2")
WORK = Path("/kaggle/working")
OUT = WORK / "godotai-lora"


def sh(*cmd: str, check: bool = True) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(list(cmd), check=check)


def find_dataset() -> Path:
    hits = sorted(glob.glob("/kaggle/input/**/*.jsonl", recursive=True))
    if not hits:
        sys.exit("no .jsonl found under /kaggle/input — attach the godotai-sft-data dataset to this kernel")
    print("dataset:", hits[0])
    return Path(hits[0])


def hf_login_if_secret() -> None:
    try:
        from kaggle_secrets import UserSecretsClient  # only exists on Kaggle
        token = UserSecretsClient().get_secret("HF_TOKEN")
    except Exception:
        return
    if token:
        os.environ["HF_TOKEN"] = token   # read by huggingface_hub; never printed
        print("HF_TOKEN secret found (gated base models allowed)")


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    sh(sys.executable, "-m", "pip", "install", "-q", "-U",
       "transformers>=4.57", "peft>=0.17", "trl>=1.14", "datasets", "bitsandbytes", "accelerate")
    repo = WORK / "repo"
    if not repo.exists():
        sh("git", "clone", "--depth", "1", "--branch", REPO_REF, REPO_URL, str(repo))
    hf_login_if_secret()
    data = find_dataset()
    trainer = repo / "training" / "train_qlora.py"
    sh(sys.executable, str(trainer), "--data", str(data), "--dry-run", "--max-seq-length", MAX_SEQ)
    sh(sys.executable, str(trainer), "--data", str(data), "--model", BASE_MODEL, "--out", str(OUT),
       "--max-seq-length", MAX_SEQ, "--epochs", EPOCHS, "--save-steps", "50")
    print("done — adapter in", OUT, "(download with: kaggle kernels output <user>/godotai-sft -p ./out)")


if __name__ == "__main__":
    main()
