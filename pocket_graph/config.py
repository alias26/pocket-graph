"""
Optional per-user configuration for pocket-graph.

Stored as JSON at:
  Windows:        %APPDATA%\\pocket-graph\\config.json
  macOS / Linux:  ~/.config/pocket-graph/config.json (or $XDG_CONFIG_HOME)

All keys optional. When set, they override the cwd-relative defaults.

Schema:
{
  "graph_out":     "/abs/path/graph-out",   // overrides <cwd>/graph-out/
  "llm_wiki":      "/abs/path/llm-wiki",    // overrides <cwd>/llm-wiki/
  "raw":           "/abs/path/raw"          // overrides <cwd>/raw/
}

The Claude Code skill is always installed globally -- there's no
per-user override for that, by design.
"""
from __future__ import annotations
import json
import os
import platform
from pathlib import Path


def config_dir() -> Path:
    if platform.system() == "Windows":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "pocket-graph"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "pocket-graph"
    return Path.home() / ".config" / "pocket-graph"


def config_path() -> Path:
    return config_dir() / "config.json"


def load() -> dict:
    p = config_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save(cfg: dict) -> Path:
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    return p


def get(key: str, default=None):
    return load().get(key, default)


__all__ = ["config_dir", "config_path", "load", "save", "get"]
