"""Where is this process running? — CPU architecture, Termux (Android), PRoot guest.

The pinned editor is an official **glibc Linux** build published for four CPU architectures
(``LINUX_PLATFORMS`` in :mod:`godotai.config`). Two facts about the host decide whether it can run at all:

* the CPU architecture must match ``engine.platform`` (an ``x86_64`` binary on an ``aarch64`` phone or an Oracle
  Ampere box fails with ``Exec format error``);
* **Termux itself is not a Linux distribution**: it is Android's Bionic userland under
  ``/data/data/com.termux/files/usr``. The official Godot binary is linked against glibc and does **not** run there
  natively. It can run inside a Debian/Ubuntu guest started with ``proot-distro`` (same phone, same files), or the
  engine work can be left to GitHub Actions (``.github/workflows/build-android.yml``). ``docs/TERMUX.md`` is the
  step-by-step guide (Arabic first).

Everything here is detection + wording; nothing is executed, downloaded or written. Detection results are exposed to
``doctor``, the chat page (``/api/status``) and the installer, which refuses to download a binary that cannot run.

Evidence for the wording (read 2026-09-26): Termux wiki "Differences from Linux" (Bionic, single prefix, no glibc);
termux/proot-distro README (``install``/``login`` options, host environment is not carried into the guest, guest
identity variables ``PD_CONTAINER`` / ``container=proot-distro``); godotengine/godot#82677 (Termux is not a targeted
platform). None of this was run on a physical Android device by this repository — see docs/TERMUX.md §7.
"""
from __future__ import annotations

import os
import platform
import sys
from dataclasses import asdict, dataclass
from typing import Mapping

from .config import EngineConfig

TERMUX_PREFIX = "/data/data/com.termux/files/usr"
ENV_INSTALL_ANYWAY = "GODOTAI_INSTALL_ANYWAY"       # "1": download an engine binary even if this host cannot run it
TERMUX_GUIDE = "docs/TERMUX.md"

# ``platform.machine()`` → the godot-builds asset suffix. Anything else (riscv64, ppc64le, …) has no official build.
_MACHINE_TO_PLATFORM = {
    "x86_64": "linux.x86_64", "amd64": "linux.x86_64",
    "aarch64": "linux.arm64", "arm64": "linux.arm64",
    "i386": "linux.x86_32", "i486": "linux.x86_32", "i586": "linux.x86_32", "i686": "linux.x86_32", "x86": "linux.x86_32",
    "armv7l": "linux.arm32", "armv8l": "linux.arm32", "armv6l": "linux.arm32", "arm": "linux.arm32",
}


def host_engine_platform(machine: str | None = None) -> str | None:
    """``linux.x86_64`` / ``linux.arm64`` / ``linux.x86_32`` / ``linux.arm32`` for this CPU, or ``None`` if unknown.

    ``armv8l`` is what a 32-bit userland reports on a 64-bit ARM kernel (some Termux installs on older phones), so it
    maps to the 32-bit asset — the 64-bit one would not load in that userland.
    """
    m = (machine if machine is not None else platform.machine()).strip().lower()
    return _MACHINE_TO_PLATFORM.get(m)


def is_proot_guest(env: Mapping[str, str] | None = None, release: str | None = None) -> bool:
    """True inside a PRoot-Distro container (Debian/Ubuntu on the phone).

    proot-distro exports ``PD_CONTAINER`` and ``container=proot-distro`` to every session (2026 Python rewrite) and
    reports a fake kernel release containing ``PRoot`` (``uname -r`` → ``6.17.0-PRoot-Distro`` by default); older
    releases set only the kernel string. Any of the three counts.
    """
    env = os.environ if env is None else env
    if (env.get("PD_CONTAINER") or "").strip():
        return True
    if (env.get("container") or "").strip().lower() == "proot-distro":
        return True
    rel = release if release is not None else platform.release()
    return "proot" in rel.lower()


def is_termux_native(env: Mapping[str, str] | None = None, prefix: str | None = None,
                     release: str | None = None) -> bool:
    """True when this Python runs **directly in Termux** (Bionic userland) — not in a proot guest, not on a PC.

    Signals: the interpreter lives under the Termux prefix (the strongest one — a Debian guest runs ``/usr/bin/python3``),
    or ``PREFIX``/``TERMUX_VERSION`` name Termux. A proot guest sees some Android variables (``ANDROID_ROOT`` …) but
    not these, and is excluded explicitly.
    """
    env = os.environ if env is None else env
    if is_proot_guest(env, release):
        return False
    py_prefix = prefix if prefix is not None else sys.prefix
    if py_prefix.startswith(TERMUX_PREFIX):
        return True
    if TERMUX_PREFIX in (env.get("PREFIX") or ""):
        return True
    return bool((env.get("TERMUX_VERSION") or "").strip())


@dataclass(frozen=True)
class HostInfo:
    machine: str                       # platform.machine()
    engine_platform: str | None        # asset this CPU can run, or None
    termux_native: bool                # Bionic userland: the glibc editor cannot run here
    proot_guest: bool                  # Debian/Ubuntu under proot (on a phone or elsewhere)
    python: str                        # interpreter path (tells the two apart at a glance)

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def label(self) -> str:
        where = "Termux (Android, Bionic)" if self.termux_native else "PRoot guest" if self.proot_guest else "Linux"
        return f"{where}, {self.machine} → {self.engine_platform or 'no official Godot build for this CPU'}"


def describe_host(env: Mapping[str, str] | None = None) -> HostInfo:
    env = os.environ if env is None else env
    return HostInfo(machine=platform.machine(), engine_platform=host_engine_platform(),
                    termux_native=is_termux_native(env), proot_guest=is_proot_guest(env), python=sys.executable or "")


# ----------------------------------------------------------------------------------------------------------------------
# Wording shared by the installer, doctor, the chat page and the engine wrapper
# ----------------------------------------------------------------------------------------------------------------------
def platform_mismatch(engine: EngineConfig, host_platform: str | None = None) -> str | None:
    """A one-paragraph explanation when ``engine.platform`` cannot run on this CPU, else ``None``.

    ``host_platform=None`` → detect; an unknown CPU (no official build) is *not* reported as a mismatch here — the
    installer's checksum lookup will say that the asset does not exist.
    """
    host = host_platform if host_platform is not None else host_engine_platform()
    if host is None or host == engine.platform:
        return None
    return (f"engine.platform is {engine.platform!r} but this machine is {platform.machine()} ({host}); the "
            f"{engine.editor_zip_name} binary cannot execute here. Set GODOTAI_ENGINE_PLATFORM={host} (or "
            f"[engine].platform in godot.toml) — the official {engine.tag} build for {host} is listed in the pinned "
            f"SHA512-SUMS.txt. Set {ENV_INSTALL_ANYWAY}=1 only to download a binary for another machine on purpose.")


def termux_native_hint(lang: str = "en") -> str:
    """Why the editor cannot run directly in Termux, and the two working alternatives (Arabic or English)."""
    if lang == "ar":
        return ("أنت داخل Termux نفسه (بيئة Android/Bionic، ليست توزيعة لينكس): محرك Godot الرسمي مبني على glibc ولا "
                "يعمل هنا مباشرة، لذلك لن يُنزَّل. الحلّان المجرَّبان منطقيًا:\n"
                "  (أ) شغّل المحرك داخل Debian بواسطة proot-distro على نفس الهاتف — الخطوات: docs/TERMUX.md §2.5\n"
                "      (pkg install proot-distro && proot-distro install debian:bookworm && proot-distro login debian --shared-home)\n"
                "  (ب) اترك المحرك لـ GitHub Actions: الذكاء الاصطناعي يرفع اللعبة إلى مستودعك ويشغّل build-android.yml الذي "
                "يستورد المشروع ويصدّر APK على خادم GitHub — docs/TERMUX.md §3.\n"
                "المحادثة والتخطيط وأدوات GitHub تعمل من Termux مباشرة؛ التحقق بالمحرك هو ما يحتاج (أ) أو (ب).")
    return ("This is Termux itself (Android/Bionic userland, not a Linux distribution): the official Godot editor is a "
            "glibc build and cannot execute here, so it will not be downloaded. Two sound alternatives:\n"
            "  (a) run the engine inside Debian via proot-distro on the same phone — docs/TERMUX.md §2.5\n"
            "      (pkg install proot-distro && proot-distro install debian:bookworm && proot-distro login debian --shared-home)\n"
            "  (b) leave the engine to GitHub Actions: the AI pushes the game to your repository and dispatches "
            "build-android.yml, which imports the project and exports the APK on a GitHub runner — docs/TERMUX.md §3.\n"
            "Chat, planning and the GitHub tools work from Termux directly; only engine verification needs (a) or (b).")


def install_blocker(engine: EngineConfig, env: Mapping[str, str] | None = None) -> str | None:
    """Reason the installer must refuse on this host (Termux native, or CPU/asset mismatch), or ``None`` to proceed.

    ``GODOTAI_INSTALL_ANYWAY=1`` disables both checks (e.g. preparing a bin dir for another machine).
    """
    env = os.environ if env is None else env
    if (env.get(ENV_INSTALL_ANYWAY) or "").strip().lower() in ("1", "true", "yes", "on"):
        return None
    if is_termux_native(env):
        return termux_native_hint("en")
    return platform_mismatch(engine)


def exec_failure_hint(engine: EngineConfig, env: Mapping[str, str] | None = None) -> str:
    """Appended to an ``OSError`` from launching the binary: the most likely cause on this host, in one sentence."""
    env = os.environ if env is None else env
    if is_termux_native(env):
        return termux_native_hint("en")
    mismatch = platform_mismatch(engine)
    if mismatch:
        return mismatch
    return ("Check that the file is the official Linux editor for this CPU (see `file <path>`), is executable, and "
            "that the runtime libraries it links against are installed (deploy/paas/Dockerfile lists them for Debian).")
