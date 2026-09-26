"""Install the pinned Godot editor + export templates on Linux, verifying SHA-512.

Sources (verified 2026-09-26): https://github.com/godotengine/godot-builds/releases
Layout follows what the editor expects:
  editor    → <bin_dir>/godot  (default ~/.local/bin, or /usr/local/bin with --system)
  templates → ~/.local/share/godot/export_templates/<version>.<release>/
"""
from __future__ import annotations

import hashlib
import os
import shutil
import stat
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from .config import Config
from .godot import export_templates_dir


class InstallError(RuntimeError):
    pass


def _download(url: str, dest: Path, log=print) -> None:
    log(f"↓ {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "godotai-installer"})
    with urllib.request.urlopen(req, timeout=120) as resp, dest.open("wb") as fh:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)
            done += len(chunk)
            if total and sys.stderr.isatty():
                print(f"\r  {done * 100 // total:3d}%", end="", file=sys.stderr)
        if total and sys.stderr.isatty():
            print(file=sys.stderr)


def sha512_of(path: Path) -> str:
    h = hashlib.sha512()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_checksums(cfg: Config, workdir: Path, log=print) -> dict[str, str]:
    """Checksums from the repo (preferred) or freshly downloaded from the release."""
    local = cfg.checksums_file()
    if local.is_file():
        text = local.read_text(encoding="utf-8")
        log(f"✓ using pinned checksums {local.relative_to(cfg.root)}")
    else:
        tmp = workdir / "SHA512-SUMS.txt"
        _download(cfg.engine.checksums_url, tmp, log)
        text = tmp.read_text(encoding="utf-8")
        log("! no pinned checksum file in repo — using the release's SHA512-SUMS.txt (trust on first use). "
            f"Commit it to {local.relative_to(cfg.root)} to pin it.")
    sums: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            sums[parts[1].lstrip("*")] = parts[0].lower()
    return sums


def _verify(path: Path, sums: dict[str, str], log=print) -> None:
    expected = sums.get(path.name)
    if not expected:
        raise InstallError(f"no SHA-512 entry for {path.name}")
    actual = sha512_of(path)
    if actual != expected:
        raise InstallError(f"SHA-512 mismatch for {path.name}\n expected {expected}\n actual   {actual}")
    log(f"✓ sha512 OK {path.name}")


def install_godot(cfg: Config, bin_dir: Path | None = None, system: bool = False,
                  templates: bool = True, force: bool = False, log=print) -> Path:
    eng = cfg.engine
    # Refuse before any download when the binary could not run here: directly in Termux (Bionic, no glibc) or an
    # asset for another CPU (x86_64 editor on an aarch64 phone / Oracle A1). GODOTAI_INSTALL_ANYWAY=1 overrides.
    from .hostenv import install_blocker
    blocker = install_blocker(eng)
    if blocker:
        raise InstallError(blocker)
    bin_dir = bin_dir or (Path("/usr/local/bin") if system else Path.home() / ".local" / "bin")
    bin_dir.mkdir(parents=True, exist_ok=True)
    target = bin_dir / "godot"
    tdir = export_templates_dir(eng)

    with tempfile.TemporaryDirectory(prefix="godotai-install-") as td:
        work = Path(td)
        sums = load_checksums(cfg, work, log)

        if target.exists() and not force:
            log(f"= editor already present at {target} (use --force to reinstall)")
        else:
            zip_path = work / eng.editor_zip_name
            _download(eng.url(eng.editor_zip_name), zip_path, log)
            _verify(zip_path, sums, log)
            with zipfile.ZipFile(zip_path) as zf:
                members = zf.namelist()
                exe = next((m for m in members if m.rstrip("/").endswith(eng.editor_binary_name)), None)
                if exe is None:
                    raise InstallError(f"{eng.editor_binary_name} not found in zip: {members[:5]}")
                if eng.is_mono:
                    # mono zips contain a folder with the binary + GodotSharp/; keep the folder intact
                    dest_dir = bin_dir.parent / "godot-mono"
                    zf.extractall(dest_dir)
                    real = dest_dir / exe
                    real.chmod(real.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    if target.exists() or target.is_symlink():
                        target.unlink()
                    target.symlink_to(real)
                else:
                    with zf.open(exe) as src, target.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
                    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            log(f"✓ editor installed → {target}")

        if templates:
            if tdir.is_dir() and any(tdir.iterdir()) and not force:
                log(f"= export templates already present at {tdir}")
            else:
                tpz = work / eng.templates_archive_name
                _download(eng.url(eng.templates_archive_name), tpz, log)
                _verify(tpz, sums, log)
                tdir.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(tpz) as zf:  # .tpz is a zip with a top-level templates/ folder
                    for m in zf.infolist():
                        if m.is_dir():
                            continue
                        rel = Path(m.filename)
                        if rel.parts and rel.parts[0] == "templates":
                            rel = Path(*rel.parts[1:])
                        out = tdir / rel
                        out.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(m) as src, out.open("wb") as dst:
                            shutil.copyfileobj(src, dst)
                        mode = (m.external_attr >> 16) & 0o777
                        if mode:  # keep the exec bit on the Linux/macOS template binaries
                            out.chmod(mode)
                log(f"✓ export templates installed → {tdir}")
    return target


def ensure_editor_initialised(godot_bin: Path, log=print) -> None:
    """Run the editor once headless so ~/.config/godot exists (mirrors godot-ci)."""
    import subprocess
    subprocess.run([str(godot_bin), "--headless", "--editor", "--quit"], capture_output=True, text=True,
                   timeout=180, check=False, env={**os.environ})
    log("✓ editor initialised (config directory created)")
