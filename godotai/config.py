"""Configuration: the engine pin and agent settings.

Everything that needs to know *which* Godot version this AI targets goes through
this module, so that changing ``godot.toml`` is enough to retarget the whole
system to a future version.
"""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_FILENAME = "godot.toml"
ENV_CONFIG_PATH = "GODOTAI_CONFIG"

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?$")


class ConfigError(RuntimeError):
    """Raised when godot.toml is missing or malformed."""


@dataclass(frozen=True)
class EngineConfig:
    version: str = "4.7.2"
    release: str = "stable"
    flavor: str = "standard"
    platform: str = "linux.x86_64"
    download_base: str = "https://github.com/godotengine/godot-builds/releases/download"

    def __post_init__(self) -> None:
        if not _VERSION_RE.match(self.version):
            raise ConfigError(f"engine.version must look like 4.7.2, got {self.version!r}")
        if not re.match(r"^[a-z]+\d*$", self.release):
            raise ConfigError(f"engine.release must look like 'stable' or 'rc1', got {self.release!r}")
        if self.flavor not in ("standard", "mono"):
            raise ConfigError("engine.flavor must be 'standard' or 'mono'")

    # -- naming helpers (match the official godot-builds asset names) -------
    @property
    def tag(self) -> str:
        """Release tag, e.g. ``4.7.2-stable``."""
        return f"{self.version}-{self.release}"

    @property
    def version_string(self) -> str:
        """Prefix printed by ``godot --version``, e.g. ``4.7.2.stable``."""
        return f"{self.version}.{self.release}"

    @property
    def major_minor(self) -> str:
        m = _VERSION_RE.match(self.version)
        assert m is not None
        return f"{m.group(1)}.{m.group(2)}"

    @property
    def is_mono(self) -> bool:
        return self.flavor == "mono"

    @property
    def editor_zip_name(self) -> str:
        if self.is_mono:
            return f"Godot_v{self.tag}_mono_{self.platform.replace('.', '_')}.zip"
        return f"Godot_v{self.tag}_{self.platform}.zip"

    @property
    def editor_binary_name(self) -> str:
        """Name of the executable inside the zip (mono zips contain a folder)."""
        if self.is_mono:
            return f"Godot_v{self.tag}_mono_{self.platform.replace('.', '_')}"
        return f"Godot_v{self.tag}_{self.platform}"

    @property
    def templates_archive_name(self) -> str:
        if self.is_mono:
            return f"Godot_v{self.tag}_mono_export_templates.tpz"
        return f"Godot_v{self.tag}_export_templates.tpz"

    @property
    def templates_dirname(self) -> str:
        """Folder Godot expects under ``~/.local/share/godot/export_templates``."""
        return f"{self.version_string}.mono" if self.is_mono else self.version_string

    @property
    def editor_settings_filename(self) -> str:
        """Editor settings are stored per minor version since 4.3."""
        return f"editor_settings-{self.major_minor}.tres"

    def url(self, filename: str) -> str:
        return f"{self.download_base}/{self.tag}/{filename}"

    @property
    def checksums_url(self) -> str:
        return self.url("SHA512-SUMS.txt")


@dataclass(frozen=True)
class AndroidConfig:
    jdk_major: int = 17
    cmdline_tools_url: str = (
        "https://dl.google.com/android/repository/commandlinetools-linux-15859902_latest.zip"
    )
    sdk_packages: tuple[str, ...] = (
        "platform-tools",
        "build-tools;35.0.1",
        "platforms;android-35",
        "cmdline-tools;latest",
        "cmake;3.10.2.4988404",
        "ndk;28.1.13356709",
    )
    preset_name: str = "Android"


@dataclass(frozen=True)
class AgentConfig:
    provider: str = "anthropic"
    model: str = "claude-fable-5-1"
    effort: str = "max"
    max_tokens: int = 64000
    max_iterations: int = 60
    max_verify_rounds: int = 4
    verify_frames: int = 120
    require_plan_approval: bool = True
    strict_tools: bool = False
    base_url: str | None = None

    def __post_init__(self) -> None:
        if self.provider not in ("anthropic", "openai_compat"):
            raise ConfigError("agent.provider must be 'anthropic' or 'openai_compat'")
        if self.effort not in ("low", "medium", "high", "xhigh", "max"):
            raise ConfigError("agent.effort must be one of low|medium|high|xhigh|max")


@dataclass(frozen=True)
class Config:
    engine: EngineConfig = field(default_factory=EngineConfig)
    android: AndroidConfig = field(default_factory=AndroidConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    path: Path | None = None

    @property
    def root(self) -> Path:
        """Repository root (directory containing godot.toml)."""
        return self.path.parent if self.path else Path.cwd()

    def checksums_file(self) -> Path:
        return self.root / "engine" / "checksums" / self.engine.tag / "SHA512-SUMS.txt"


def find_config_file(start: Path | None = None) -> Path | None:
    env = os.environ.get(ENV_CONFIG_PATH)
    if env:
        p = Path(env).expanduser()
        return p if p.is_file() else None
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        p = candidate / CONFIG_FILENAME
        if p.is_file():
            return p
    # fall back to the file shipped next to the package (repo checkout)
    packaged = Path(__file__).resolve().parent.parent / CONFIG_FILENAME
    return packaged if packaged.is_file() else None


def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    agent = data.setdefault("agent", {})
    engine = data.setdefault("engine", {})
    mapping = {
        "GODOTAI_PROVIDER": (agent, "provider"),
        "GODOTAI_MODEL": (agent, "model"),
        "GODOTAI_EFFORT": (agent, "effort"),
        "GODOTAI_BASE_URL": (agent, "base_url"),
        "GODOTAI_ENGINE_VERSION": (engine, "version"),
        "GODOTAI_ENGINE_RELEASE": (engine, "release"),
    }
    for env_name, (section, key) in mapping.items():
        value = os.environ.get(env_name)
        if value:
            section[key] = value
    return data


def load_config(path: Path | None = None) -> Config:
    """Load ``godot.toml`` (searched upward from cwd unless *path* is given)."""
    cfg_path = Path(path) if path else find_config_file()
    data: dict[str, Any] = {}
    if cfg_path is not None:
        try:
            data = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:  # pragma: no cover - defensive
            raise ConfigError(f"{cfg_path}: {exc}") from exc
    data = _apply_env_overrides(data)

    engine_d = dict(data.get("engine", {}))
    android_d = dict(data.get("android", {}))
    agent_d = dict(data.get("agent", {}))
    if "sdk_packages" in android_d:
        android_d["sdk_packages"] = tuple(android_d["sdk_packages"])
    try:
        return Config(
            engine=EngineConfig(**engine_d),
            android=AndroidConfig(**android_d),
            agent=AgentConfig(**agent_d),
            path=cfg_path,
        )
    except TypeError as exc:
        raise ConfigError(f"unknown key in {cfg_path}: {exc}") from exc


# --------------------------------------------------------------------------
# Version guard helpers
# --------------------------------------------------------------------------
_GODOT_VERSION_OUTPUT_RE = re.compile(r"(\d+\.\d+(?:\.\d+)?)\.([a-z]+\d*)")


def parse_godot_version_output(output: str) -> str | None:
    """Extract ``4.7.2.stable`` from ``godot --version`` output.

    Godot prints e.g. ``4.7.2.stable.official.ed1daf0bf``; a ``4.7.stable`` (no
    patch number) is normalised to ``4.7.0.stable``.
    """
    for line in output.splitlines():
        m = _GODOT_VERSION_OUTPUT_RE.search(line.strip())
        if m:
            version, release = m.group(1), m.group(2)
            if version.count(".") == 1:
                version += ".0"
            return f"{version}.{release}"
    return None


def version_matches(output: str, engine: EngineConfig) -> bool:
    found = parse_godot_version_output(output)
    if found is None:
        return False
    expected = engine.version_string
    if engine.version.count(".") == 1:
        expected = f"{engine.version}.0.{engine.release}"
    return found == expected
