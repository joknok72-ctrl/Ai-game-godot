"""Secret detection + redaction, shared by the dataset extractor, the repo scan and tests.

The patterns are deliberately conservative (few false positives) and cover the
credential shapes this project can meet: Anthropic / OpenAI keys, GitHub tokens,
cloud API tokens assigned in text (``KAGGLE_API_TOKEN=…``, ``CLOUDFLARE_API_TOKEN=…``,
``TUNNEL_TOKEN=…`` for cloudflared, ``GODOTAI_CHAT_TOKEN=…`` for the chat server),
``Authorization: Bearer …`` headers, Android keystore passwords and PEM blocks.

Placeholders are recognised so documentation can show *how* to set a variable
(``export KAGGLE_API_TOKEN=<paste-your-token>``, ``${{ secrets.KAGGLE_API_TOKEN }}``)
without tripping the scan. A line that must contain a token-shaped string on
purpose (test fixtures, documentation of the *format*) can carry the marker
``secret-scan:allow``; like gitleaks' ``gitleaks:allow`` it is visible in review.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

REDACTED = "[REDACTED]"
ALLOW_MARKER = "secret-scan:allow"

_PLACEHOLDER_RE = re.compile(
    r"^(?:<[^>]*>|\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|\$\{\{.*\}\}|\*{3,}|x{6,}|your[-_ ]|paste|redacted|changeme|"
    r"example|dummy|placeholder|os\.environ|secrets\.|env\(|getenv)", re.I)

# name → compiled pattern. Group 1 (when present) is the secret value; otherwise the whole match.
PATTERNS: dict[str, re.Pattern[str]] = {
    "anthropic_key": re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}"),
    "openai_key": re.compile(r"\bsk-(?!ant-)(?:proj-)?[A-Za-z0-9_\-]{32,}"),
    "github_token": re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b|\bgithub_pat_[A-Za-z0-9_]{22,}\b"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "google_api_key": re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b"),
    "slack_token": re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"),
    # whole block when the footer is present (redaction), header alone otherwise (line scan)
    "private_key_block": re.compile(
        r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----(?:[\s\S]*?-----END (?:[A-Z]+ )?PRIVATE KEY-----)?"),
    "bearer_token": re.compile(r"\bBearer\s+([A-Za-z0-9_\-\.=/+]{24,})"),
    # NAME = value  for the variable names this project documents
    "assigned_secret": re.compile(
        r"\b((?:KAGGLE_API_TOKEN|KAGGLE_KEY|CLOUDFLARE_API_TOKEN|CF_API_TOKEN|CF_AIG_TOKEN|ANTHROPIC_API_KEY|"
        r"OPENAI_API_KEY|GITHUB_TOKEN|HF_TOKEN|GODOT_ANDROID_KEYSTORE_(?:DEBUG|RELEASE)_PASSWORD|"
        r"TUNNEL_TOKEN|CF_TUNNEL_TOKEN|CLOUDFLARE_TUNNEL_TOKEN|GODOTAI_CHAT_TOKEN)"
        r"\b\s*[:=]\s*[\"']?)([^\s\"'<>${}]{12,})", re.I),
    "generic_secret_assignment": re.compile(
        r"(?i)\b((?:api[_-]?key|api[_-]?token|access[_-]?token|secret[_-]?key|client[_-]?secret|auth[_-]?token)"
        r"\b\s*[:=]\s*[\"']?)([A-Za-z0-9_\-\.=/+]{24,})"),
}
# scanning is for text files only
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico", ".ogg", ".wav", ".mp3", ".ttf", ".otf", ".woff",
                 ".apk", ".aab", ".pck", ".zip", ".tpz", ".jar", ".class", ".so", ".dll", ".pyc", ".ipynb_checkpoints"}
SKIP_DIRS = {".git", ".godot", "__pycache__", "node_modules", ".venv", "build", "dist"}


@dataclass
class Finding:
    path: str
    line: int
    kind: str
    preview: str      # never contains the secret itself

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.kind} — {self.preview}"


def _value_of(kind: str, m: re.Match[str]) -> str:
    if kind in ("bearer_token",):
        return m.group(1)
    if kind in ("assigned_secret", "generic_secret_assignment"):
        return m.group(2)
    return m.group(0)


def _is_placeholder(value: str) -> bool:
    return bool(_PLACEHOLDER_RE.match(value)) or value.upper() == value and "_" in value and value.isidentifier()


def find_in_text(text: str) -> list[tuple[int, str]]:
    """Return (line_number, kind) for every likely secret in *text*."""
    out: list[tuple[int, str]] = []
    for i, line in enumerate(text.splitlines(), 1):
        if ALLOW_MARKER in line:
            continue
        for kind, pat in PATTERNS.items():
            for m in pat.finditer(line):
                if kind != "private_key_block" and _is_placeholder(_value_of(kind, m)):
                    continue
                out.append((i, kind))
    return out


def redact(text: str) -> tuple[str, int]:
    """Replace secret values with ``[REDACTED]``; return (new_text, count)."""
    count = 0

    def sub(kind: str, m: re.Match[str]) -> str:
        nonlocal count
        value = _value_of(kind, m)
        if kind != "private_key_block" and _is_placeholder(value):
            return m.group(0)
        count += 1
        if kind == "private_key_block":
            return REDACTED
        return m.group(0).replace(value, REDACTED)

    for kind, pat in PATTERNS.items():
        text = pat.sub(lambda m, k=kind: sub(k, m), text)
    return text, count


def redact_obj(obj, counter: list[int] | None = None):
    """Recursively redact every string inside a JSON-like structure."""
    counter = counter if counter is not None else [0]
    if isinstance(obj, str):
        new, n = redact(obj)
        counter[0] += n
        return new
    if isinstance(obj, list):
        return [redact_obj(x, counter) for x in obj]
    if isinstance(obj, dict):
        return {k: redact_obj(v, counter) for k, v in obj.items()}
    return obj


def iter_text_files(root: Path, skip_dirs: Iterable[str] = SKIP_DIRS) -> Iterable[Path]:
    skip = set(skip_dirs)
    for p in sorted(Path(root).rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if any(part in skip for part in rel.parts):
            continue
        if p.suffix.lower() in SKIP_SUFFIXES or p.stat().st_size > 2_000_000:
            continue
        yield p


def scan_tree(root: Path) -> list[Finding]:
    root = Path(root)
    findings: list[Finding] = []
    for p in iter_text_files(root):
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for line_no, kind in find_in_text(text):
            findings.append(Finding(p.relative_to(root).as_posix(), line_no, kind, f"looks like a {kind}"))
    return findings
