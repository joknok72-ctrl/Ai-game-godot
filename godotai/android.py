"""Android SDK setup + Godot editor settings for headless APK export.

Everything here follows the official 4.7 "Exporting for Android" page
(docs.godotengine.org/en/stable/tutorials/export/exporting_for_android.html):
OpenJDK 17, the sdkmanager package list from ``godot.toml``, a debug keystore
generated with ``keytool``, and the two editor settings ``export/android/java_sdk_path``
and ``export/android/android_sdk_path``.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from .config import Config
from .godot import editor_settings_path


class AndroidSetupError(RuntimeError):
    pass


def find_java_home(jdk_major: int) -> Path | None:
    env = os.environ.get("JAVA_HOME")
    if env and Path(env, "bin", "java").exists():
        return Path(env)
    for base in (Path("/usr/lib/jvm"), Path("/opt/java"), Path("/usr/local/lib/jvm")):
        if base.is_dir():
            for d in sorted(base.iterdir()):
                if str(jdk_major) in d.name and (d / "bin" / "java").exists():
                    return d
    java = shutil.which("java")
    if java:
        return Path(os.path.realpath(java)).parent.parent
    return None


def java_major_version(java_home: Path) -> int | None:
    try:
        out = subprocess.run([str(java_home / "bin" / "java"), "-version"], capture_output=True, text=True,
                             timeout=30, check=False)
    except OSError:
        return None
    text = out.stderr + out.stdout
    for token in text.replace('"', " ").split():
        if token[0].isdigit() and "." in token:
            major = token.split(".")[0]
            if major == "1":  # 1.8 style
                return int(token.split(".")[1])
            return int(major)
    return None


def sdk_root() -> Path:
    return Path(os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT") or "/opt/android-sdk")


def _restore_zip_permissions(zf: zipfile.ZipFile, dest: Path) -> None:
    """zipfile.extractall drops Unix mode bits; put them back (sdkmanager must be executable)."""
    for info in zf.infolist():
        mode = (info.external_attr >> 16) & 0o777
        target = dest / info.filename
        if mode and target.exists() and not info.is_dir():
            target.chmod(mode)
        elif target.is_file() and target.parent.name == "bin":
            target.chmod(0o755)


def _sdkmanager(root: Path) -> Path | None:
    for cand in (root / "cmdline-tools" / "latest" / "bin" / "sdkmanager",
                 root / "cmdline-tools" / "bin" / "sdkmanager"):
        if cand.exists():
            return cand
    found = shutil.which("sdkmanager")
    return Path(found) if found else None


def install_sdk(cfg: Config, root: Path | None = None, log=print) -> Path:
    """Download cmdline-tools if missing, accept licences, install the pinned packages."""
    root = root or sdk_root()
    root.mkdir(parents=True, exist_ok=True)
    mgr = _sdkmanager(root)
    if mgr is None:
        with tempfile.TemporaryDirectory() as td:
            zip_path = Path(td) / "cmdline-tools.zip"
            log(f"↓ {cfg.android.cmdline_tools_url}")
            urllib.request.urlretrieve(cfg.android.cmdline_tools_url, zip_path)
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(Path(td) / "x")
                _restore_zip_permissions(zf, Path(td) / "x")
            # Google's zip extracts to cmdline-tools/; sdkmanager requires cmdline-tools/latest/
            src = Path(td) / "x" / "cmdline-tools"
            dest = root / "cmdline-tools" / "latest"
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                shutil.rmtree(dest)
            shutil.move(str(src), str(dest))
        mgr = _sdkmanager(root)
        if mgr is None:
            raise AndroidSetupError("sdkmanager not found after extracting cmdline-tools")
    env = {**os.environ, "ANDROID_HOME": str(root), "ANDROID_SDK_ROOT": str(root)}
    java_home = find_java_home(cfg.android.jdk_major)
    if java_home:
        env["JAVA_HOME"] = str(java_home)
    log("✓ accepting SDK licences")
    subprocess.run(f"yes | {mgr} --sdk_root={root} --licenses", shell=True, capture_output=True, text=True,
                   env=env, check=False, timeout=600)
    log(f"↓ sdkmanager {' '.join(cfg.android.sdk_packages)}")
    proc = subprocess.run([str(mgr), f"--sdk_root={root}", *cfg.android.sdk_packages], capture_output=True,
                          text=True, env=env, check=False, timeout=3600)
    if proc.returncode != 0:
        raise AndroidSetupError(f"sdkmanager failed ({proc.returncode}):\n{(proc.stderr or proc.stdout)[-3000:]}")
    log(f"✓ Android SDK ready at {root}")
    return root


def ensure_debug_keystore(path: Path | None = None, log=print) -> Path:
    """Standard Android debug keystore (alias androiddebugkey / password android)."""
    path = path or (Path.home() / ".android" / "debug.keystore")
    if path.exists():
        return path
    keytool = shutil.which("keytool")
    if not keytool:
        raise AndroidSetupError("keytool (JDK) not found; install OpenJDK 17")
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([keytool, "-keyalg", "RSA", "-genkeypair", "-alias", "androiddebugkey", "-keypass", "android",
                    "-keystore", str(path), "-storepass", "android",
                    "-dname", "CN=Android Debug,O=Android,C=US", "-validity", "9999"],
                   check=True, capture_output=True, text=True, timeout=120)
    log(f"✓ debug keystore created at {path}")
    return path


def write_editor_settings(cfg: Config, java_home: Path, sdk: Path, debug_keystore: Path | None,
                          log=print) -> Path:
    """Create/patch editor_settings-<major.minor>.tres with the Android paths."""
    path = editor_settings_path(cfg.engine)
    path.parent.mkdir(parents=True, exist_ok=True)
    wanted = {
        "export/android/java_sdk_path": f'"{java_home}"',
        "export/android/android_sdk_path": f'"{sdk}"',
        "export/android/force_system_user": "false",
        "export/android/shutdown_adb_on_exit": "true",
    }
    if debug_keystore is not None:
        wanted.update({
            "export/android/debug_keystore": f'"{debug_keystore}"',
            "export/android/debug_keystore_user": '"androiddebugkey"',
            "export/android/debug_keystore_pass": '"android"',
        })
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    else:
        lines = ['[gd_resource type="EditorSettings" format=3]', "", "[resource]"]
    keys_present = set()
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key in wanted:
            out.append(f"{key} = {wanted[key]}")
            keys_present.add(key)
        else:
            out.append(line)
    if "[resource]" not in out:
        out.append("[resource]")
    for key, value in wanted.items():
        if key not in keys_present:
            out.append(f"{key} = {value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    log(f"✓ editor settings written → {path}")
    return path


def setup(cfg: Config, sdk: Path | None = None, install_packages: bool = True, log=print) -> dict[str, str]:
    java_home = find_java_home(cfg.android.jdk_major)
    if java_home is None:
        raise AndroidSetupError(f"OpenJDK {cfg.android.jdk_major} not found. On Debian/Ubuntu: "
                                f"apt-get install -y openjdk-{cfg.android.jdk_major}-jdk-headless")
    major = java_major_version(java_home)
    if major is not None and major < cfg.android.jdk_major:
        raise AndroidSetupError(f"JDK {major} found at {java_home}; Godot {cfg.engine.major_minor} needs {cfg.android.jdk_major}+")
    root = sdk or sdk_root()
    if install_packages:
        install_sdk(cfg, root, log)
    ks = ensure_debug_keystore(log=log)
    settings = write_editor_settings(cfg, java_home, root, ks, log)
    return {"java_home": str(java_home), "android_sdk": str(root), "debug_keystore": str(ks),
            "editor_settings": str(settings)}
