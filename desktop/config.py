#!/usr/bin/env python3
"""Configuration persistence for Watchdog app."""

import json
import sys
from pathlib import Path
from typing import Any


def get_config_dir() -> Path:
    """Get platform-appropriate config directory."""
    if sys.platform == "darwin":
        config_dir = Path.home() / "Library" / "Application Support" / "Watchdog"
    elif sys.platform == "win32":
        config_dir = Path.home() / "AppData" / "Local" / "Watchdog"
    else:
        config_dir = Path.home() / ".config" / "watchdog"
    
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


class Config:
    """Simple JSON-based configuration."""
    
    def __init__(self):
        self.path = get_config_dir() / "app-config.json"
        self._data = self._load()
    
    def _load(self) -> dict:
        """Load config from disk."""
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, IOError):
                return {}
        return {}
    
    def _save(self):
        """Save config to disk."""
        try:
            self.path.write_text(
                json.dumps(self._data, indent=2),
                encoding="utf-8"
            )
        except IOError as e:
            print(f"Warning: Could not save config: {e}")
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get a config value."""
        return self._data.get(key, default)
    
    def set(self, key: str, value: Any):
        """Set a config value and persist."""
        self._data[key] = value
        self._save()
    
    def delete(self, key: str):
        """Delete a config key."""
        if key in self._data:
            del self._data[key]
            self._save()
    
    def clear(self):
        """Clear all config."""
        self._data = {}
        self._save()
