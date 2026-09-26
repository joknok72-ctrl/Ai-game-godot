#!/usr/bin/env python3
"""Fail when the working tree contains something that looks like a credential.

    python3 scripts/secret_scan.py            # scan the repository
    python3 scripts/secret_scan.py path/ ...  # scan other trees (e.g. a generated game, a dataset)

Runs in CI before anything else (see .github/workflows/ci.yml) and is the same
detector the dataset extractor uses to redact transcripts (godotai.secrets).
Findings never print the matched value — only path, line and kind.
A line that must contain a token-shaped string on purpose can carry the marker
``secret-scan:allow`` (visible in review, like gitleaks' ``gitleaks:allow``).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from godotai import secrets  # noqa: E402


def main(argv: list[str]) -> int:
    roots = [Path(a) for a in argv] or [REPO]
    total = 0
    for root in roots:
        if not root.exists():
            print(f"secret-scan: {root} does not exist", file=sys.stderr)
            return 2
        findings = secrets.scan_tree(root)
        for f in findings:
            print(f"{root / f.path}:{f.line}: {f.kind}")
        total += len(findings)
    if total:
        print(f"\nsecret-scan: {total} finding(s). Remove the value, use an environment variable / GitHub Actions "
              f"secret instead, and ROTATE the credential — anything committed to git history must be treated as leaked.",
              file=sys.stderr)
        return 1
    print(f"secret-scan: clean ({', '.join(str(r) for r in roots)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
