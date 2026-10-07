"""Configuration helpers for backend selection."""

from __future__ import annotations

import os
from pathlib import Path
from typing import NamedTuple

DEFAULT_EVERYTHING_EXE = Path(r"C:\Program Files\Everything\Everything.exe")
BACKEND_ENV = "EVERYTHING_MCP_BACKEND"
BACKEND_CHOICES = ("auto", "native", "sdk", "es")


class EverythingConfig(NamedTuple):
    everything_exe: Path = DEFAULT_EVERYTHING_EXE
    sdk_dll: Path | None = None
    es_exe: Path | None = None
    instance: str | None = None
    backend: str = "auto"

    @classmethod
    def from_env(cls) -> "EverythingConfig":
        everything_exe = Path(os.environ.get("EVERYTHING_EXE", str(DEFAULT_EVERYTHING_EXE)))
        sdk_raw = os.environ.get("EVERYTHING_SDK_DLL")
        es_raw = os.environ.get("EVERYTHING_ES_EXE")
        instance = os.environ.get("EVERYTHING_INSTANCE", "").strip() or None
        backend = os.environ.get(BACKEND_ENV, "").strip().lower() or "auto"
        if backend not in BACKEND_CHOICES:
            raise ValueError(f"{BACKEND_ENV} must be one of: {', '.join(BACKEND_CHOICES)}")
        return cls(
            everything_exe=everything_exe,
            sdk_dll=Path(sdk_raw) if sdk_raw else None,
            es_exe=Path(es_raw) if es_raw else None,
            instance=instance,
            backend=backend,
        )
