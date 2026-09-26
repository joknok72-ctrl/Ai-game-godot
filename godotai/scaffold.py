"""Create a new game project from a template in ``templates/``.

Templates are real, verified Godot projects. Placeholders:
``__GAME_NAME__``, ``__PACKAGE__`` (Android unique name), ``__GODOT_FEATURE__``
(major.minor feature tag) and ``__GODOT_TAG__``.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from .config import Config

TEXT_SUFFIXES = {".godot", ".cfg", ".gd", ".tscn", ".tres", ".md", ".yml", ".yaml", ".txt", ".json", ".svg", ".gitignore"}
_PACKAGE_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


class ScaffoldError(RuntimeError):
    pass


def list_templates(cfg: Config) -> list[str]:
    tdir = cfg.root / "templates"
    return sorted(p.name for p in tdir.iterdir() if p.is_dir() and (p / "project.godot").exists()) if tdir.is_dir() else []


def package_from_name(name: str, owner: str = "example") -> str:
    slug = re.sub(r"[^a-z0-9_]", "", name.lower().replace(" ", "_").replace("-", "_")) or "game"
    if slug[0].isdigit():
        slug = "g" + slug
    return f"com.{owner}.{slug}"


def create_project(cfg: Config, template: str, dest: Path, name: str, package: str | None = None) -> Path:
    src = cfg.root / "templates" / template
    if not (src / "project.godot").exists():
        raise ScaffoldError(f"template {template!r} not found; available: {list_templates(cfg)}")
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise ScaffoldError(f"destination {dest} is not empty")
    package = package or package_from_name(name)
    if not _PACKAGE_RE.match(package):
        raise ScaffoldError(f"invalid Android package name {package!r} (e.g. com.studio.mygame)")
    values = {
        "__GAME_NAME__": name,
        "__PACKAGE__": package,
        "__GODOT_FEATURE__": cfg.engine.major_minor,
        "__GODOT_TAG__": cfg.engine.tag,
    }
    for p in src.rglob("*"):
        rel = p.relative_to(src)
        if any(part in (".godot", ".import") for part in rel.parts):
            continue
        out = dest / rel
        if p.is_dir():
            out.mkdir(parents=True, exist_ok=True)
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        if p.suffix in TEXT_SUFFIXES or p.name in ("project.godot", ".gitignore"):
            text = p.read_text(encoding="utf-8")
            for k, v in values.items():
                text = text.replace(k, v)
            out.write_text(text, encoding="utf-8")
        else:
            shutil.copy2(p, out)
    return dest
