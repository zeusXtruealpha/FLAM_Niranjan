"""Configuration management"""

import json
import os
from pathlib import Path
from typing import Dict, Any


class Config:
    """Configuration manager"""
    
    DEFAULT_CONFIG = {
        "max_retries": 3,
        "backoff_base": 2,
        "worker_count": 1,
    }

    def __init__(self, config_dir: str = None):
        """Initialize configuration"""
        if config_dir is None:
            # Use user's home directory for config
            home = Path.home()
            config_dir = str(home / ".queuectl")
        
        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = self.config_dir / "config.json"
        self._config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from file"""
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r') as f:
                    config = json.load(f)
                    # Merge with defaults to ensure all keys exist
                    merged = self.DEFAULT_CONFIG.copy()
                    merged.update(config)
                    return merged
            except (json.JSONDecodeError, IOError):
                return self.DEFAULT_CONFIG.copy()
        return self.DEFAULT_CONFIG.copy()

    def _save_config(self):
        """Save configuration to file"""
        try:
            with open(self.config_file, 'w') as f:
                json.dump(self._config, f, indent=2)
        except IOError as e:
            raise RuntimeError(f"Failed to save config: {e}")

    def get(self, key: str, default=None):
        """Get configuration value"""
        return self._config.get(key, default)

    def set(self, key: str, value: Any):
        """Set configuration value"""
        if key not in self.DEFAULT_CONFIG:
            raise ValueError(f"Unknown config key: {key}")
        
        # Type validation
        if key == "max_retries" or key == "worker_count":
            value = int(value)
            if value < 0:
                raise ValueError(f"{key} must be non-negative")
        elif key == "backoff_base":
            value = float(value)
            if value <= 0:
                raise ValueError(f"backoff_base must be positive")
        
        self._config[key] = value
        self._save_config()

    def get_all(self) -> Dict[str, Any]:
        """Get all configuration values"""
        return self._config.copy()

