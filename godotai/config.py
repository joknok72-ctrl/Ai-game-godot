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

from .model_identity import ModelIdentity, ModelIdentityError, check_consistency

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


EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
ROUTES = ("direct", "cf_gateway", "workers_ai")
TASK_BUDGET_MIN = 20_000   # documented minimum for output_config.task_budget.total


PRIVATE_BASE_URL = "http://127.0.0.1:8000/v1"   # your own OpenAI-compatible server (vLLM / llama.cpp / Ollama)
OPENAI_VENDOR_URL = "https://api.openai.com/v1"


@dataclass(frozen=True)
class AgentConfig:
    # Defaults = *your* model on *your* server. A vendor API (Claude/OpenAI) is an explicit opt-in — and must then
    # be declared as such in [model] (see godotai/model_identity.py); the runtime never requires one.
    provider: str = "openai_compat"
    model: str = "godotai"                # served model name (vLLM --served-model-name, Ollama tag, LoRA module name)
    effort: str = "max"
    max_tokens: int = 64000
    max_iterations: int = 60
    max_verify_rounds: int = 4
    verify_frames: int = 120
    require_plan_approval: bool = True
    strict_tools: bool = False
    base_url: str | None = None           # explicit endpoint; None → see `endpoint`
    # --- Claude Fable 5.1 harness controls (all documented in docs/RESEARCH.md, read 2026-09-26) ---
    act_effort: str | None = None        # effort from plan approval onward (per-message effort, beta); None = same as effort
    batch_nudge: bool = True             # documented one-sentence nudge after every tool-result message
    long_output_note: bool = True        # documented max_tokens note appended to the task at xhigh/max effort
    progress_updates: bool = False       # beta: thinking.display="updates" → status lines between tool calls
    turn_scoped_system: bool = False     # beta: nudges as role:system + clear_at instead of a text block
    task_budget_tokens: int | None = None  # beta: advisory whole-task budget (output_config.task_budget)
    prefix_binding_drop: bool = False    # beta: drop (and report) thinking blocks whose prefix changed — debugging aid
    route: str = "direct"                # direct | cf_gateway (Cloudflare AI Gateway) | workers_ai (Cloudflare Workers AI)

    def __post_init__(self) -> None:
        if self.provider not in ("anthropic", "openai_compat"):
            raise ConfigError("agent.provider must be 'anthropic' or 'openai_compat'")
        if self.effort not in EFFORT_LEVELS:
            raise ConfigError("agent.effort must be one of low|medium|high|xhigh|max")
        if self.act_effort is not None and self.act_effort not in EFFORT_LEVELS:
            raise ConfigError("agent.act_effort must be one of low|medium|high|xhigh|max")
        if self.task_budget_tokens is not None and self.task_budget_tokens < TASK_BUDGET_MIN:
            raise ConfigError(f"agent.task_budget_tokens must be ≥ {TASK_BUDGET_MIN} (documented minimum)")
        if self.route not in ROUTES:
            raise ConfigError("agent.route must be one of direct|cf_gateway|workers_ai")
        if self.route == "workers_ai" and self.provider != "openai_compat":
            raise ConfigError("agent.route = workers_ai requires agent.provider = openai_compat")

    @property
    def uses_beta(self) -> bool:
        return bool(self.progress_updates or self.turn_scoped_system or self.task_budget_tokens
                    or self.prefix_binding_drop or (self.act_effort and self.act_effort != self.effort))

    @property
    def endpoint(self) -> str | None:
        """The base URL the OpenAI-compatible provider will actually talk to (``None`` = derived elsewhere).

        Precedence: ``base_url`` (toml / ``GODOTAI_BASE_URL``) → ``OPENAI_BASE_URL`` → if an ``OPENAI_API_KEY``
        is present, the OpenAI vendor API (an explicit choice) → otherwise **your private server**
        (``PRIVATE_BASE_URL``). Cloudflare routes derive their URL in ``make_provider`` unless ``base_url`` is set;
        the Anthropic provider has its own default.
        """
        if self.base_url:
            return self.base_url
        if self.provider != "openai_compat" or self.route != "direct":
            return None
        env_url = os.environ.get("OPENAI_BASE_URL", "").strip()
        if env_url:
            return env_url
        return OPENAI_VENDOR_URL if os.environ.get("OPENAI_API_KEY") else PRIVATE_BASE_URL

    @property
    def private_endpoint(self) -> bool:
        """True when the model is reached at a self-hosted / private URL (not a vendor API, not Cloudflare-managed)."""
        url = self.endpoint
        return bool(url) and self.route == "direct" and self.provider == "openai_compat" and "api.openai.com" not in url


@dataclass(frozen=True)
class Config:
    engine: EngineConfig = field(default_factory=EngineConfig)
    android: AndroidConfig = field(default_factory=AndroidConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    model: ModelIdentity = field(default_factory=ModelIdentity)
    path: Path | None = None

    def __post_init__(self) -> None:
        # The one lie this project must never tell: a vendor model presented as "your" model (or the reverse).
        try:
            check_consistency(self.model, self.agent.provider, self.agent.model, self.agent.endpoint, self.agent.route)
        except ModelIdentityError as exc:
            raise ConfigError(str(exc)) from exc

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
    model = data.setdefault("model", {})
    mapping = {
        "GODOTAI_PROVIDER": (agent, "provider"),
        "GODOTAI_MODEL": (agent, "model"),
        "GODOTAI_EFFORT": (agent, "effort"),
        "GODOTAI_ACT_EFFORT": (agent, "act_effort"),
        "GODOTAI_BASE_URL": (agent, "base_url"),
        "GODOTAI_ROUTE": (agent, "route"),
        "GODOTAI_ENGINE_VERSION": (engine, "version"),
        "GODOTAI_ENGINE_RELEASE": (engine, "release"),
        # identity of the weights behind [agent] (see godotai/model_identity.py)
        "GODOTAI_MODEL_NAME": (model, "name"),
        "GODOTAI_MODEL_KIND": (model, "kind"),
        "GODOTAI_BASE_MODEL": (model, "base_model"),
        "GODOTAI_BASE_LICENSE": (model, "base_license"),
        "GODOTAI_ADAPTER": (model, "adapter"),
        "GODOTAI_SERVING": (model, "serving"),
        "GODOTAI_REFERENCE_MODEL": (model, "reference_model"),
    }
    for env_name, (section, key) in mapping.items():
        value = os.environ.get(env_name)
        if value:
            section[key] = value
    bool_mapping = {
        "GODOTAI_PROGRESS_UPDATES": "progress_updates",
        "GODOTAI_BATCH_NUDGE": "batch_nudge",
        "GODOTAI_TURN_SCOPED_SYSTEM": "turn_scoped_system",
        "GODOTAI_PREFIX_BINDING_DROP": "prefix_binding_drop",
        "GODOTAI_LONG_OUTPUT_NOTE": "long_output_note",
    }
    for env_name, key in bool_mapping.items():
        value = os.environ.get(env_name)
        if value:
            agent[key] = value.strip().lower() in ("1", "true", "yes", "on")
    budget = os.environ.get("GODOTAI_TASK_BUDGET")
    if budget:
        try:
            agent["task_budget_tokens"] = int(budget)
        except ValueError as exc:
            raise ConfigError(f"GODOTAI_TASK_BUDGET must be an integer, got {budget!r}") from exc
    if agent.get("task_budget_tokens") == 0:      # 0 (toml or env) = feature off, same as unset
        agent["task_budget_tokens"] = None
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
    model_d = dict(data.get("model", {}))
    if "sdk_packages" in android_d:
        android_d["sdk_packages"] = tuple(android_d["sdk_packages"])
    if agent_d.get("base_url") == "":            # "" in toml/env = "use the provider default", same as unset
        agent_d["base_url"] = None
    try:
        return Config(
            engine=EngineConfig(**engine_d),
            android=AndroidConfig(**android_d),
            agent=AgentConfig(**agent_d),
            model=ModelIdentity(**model_d),
            path=cfg_path,
        )
    except TypeError as exc:
        raise ConfigError(f"unknown key in {cfg_path}: {exc}") from exc
    except ModelIdentityError as exc:
        raise ConfigError(f"{cfg_path}: {exc}") from exc


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
