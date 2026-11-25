"""Where JARVIS keeps its settings, database and logs.

Windows (default):  %APPDATA%\\JARVIS\\settings.yaml
                    %LOCALAPPDATA%\\JARVIS\\{data,logs}
Other OS:           ~/.config/jarvis, ~/.local/share/jarvis
Override:           set JARVIS_HOME to put everything in one folder
                    (portable mode, tests).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    config_dir: Path
    data_dir: Path
    log_dir: Path

    @property
    def settings_file(self) -> Path:
        return self.config_dir / "settings.yaml"

    @property
    def database_file(self) -> Path:
        return self.data_dir / "jarvis.db"

    def ensure(self) -> "AppPaths":
        for d in (self.config_dir, self.data_dir, self.log_dir):
            d.mkdir(parents=True, exist_ok=True)
        return self

    @classmethod
    def default(cls) -> "AppPaths":
        home_override = os.environ.get("JARVIS_HOME")
        if home_override:
            root = Path(home_override).expanduser().resolve()
            return cls(root / "config", root / "data", root / "logs")
        if sys.platform == "win32":
            roaming = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
            local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
            return cls(roaming / "JARVIS", local / "JARVIS" / "data", local / "JARVIS" / "logs")
        config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "jarvis"
        share = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "jarvis"
        return cls(config, share / "data", share / "logs")
