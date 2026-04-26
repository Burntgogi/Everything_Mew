"""Configuration helpers for backend selection."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_EVERYTHING_EXE = Path(r"C:\Program Files\Everything\Everything.exe")


@dataclass(frozen=True)
class EverythingConfig:
    everything_exe: Path = DEFAULT_EVERYTHING_EXE
    sdk_dll: Path | None = None
    es_exe: Path | None = None

    @classmethod
    def from_env(cls) -> "EverythingConfig":
        everything_exe = Path(os.environ.get("EVERYTHING_EXE", str(DEFAULT_EVERYTHING_EXE)))
        sdk_raw = os.environ.get("EVERYTHING_SDK_DLL")
        es_raw = os.environ.get("EVERYTHING_ES_EXE")
        return cls(
            everything_exe=everything_exe,
            sdk_dll=Path(sdk_raw) if sdk_raw else None,
            es_exe=Path(es_raw) if es_raw else None,
        )
