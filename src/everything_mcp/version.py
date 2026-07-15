"""Resolve the package version from installed metadata or the source tree."""

from __future__ import annotations

import tomllib
from importlib import metadata
from pathlib import Path

_DISTRIBUTION_NAME = "everything-mew"
_SOURCE_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def _source_tree_version(pyproject_path: Path) -> str:
    try:
        with pyproject_path.open("rb") as pyproject_file:
            pyproject = tomllib.load(pyproject_file)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RuntimeError(f"Unable to read package version from {pyproject_path}.") from exc

    project = pyproject.get("project")
    if not isinstance(project, dict):
        raise RuntimeError(f"Missing [project] table in {pyproject_path}.")

    package_version = project.get("version")
    if not isinstance(package_version, str) or not package_version:
        raise RuntimeError(f"Missing [project].version in {pyproject_path}.")
    return package_version


def _resolve_version(pyproject_path: Path = _SOURCE_PYPROJECT) -> str:
    try:
        return metadata.version(_DISTRIBUTION_NAME)
    except metadata.PackageNotFoundError:
        return _source_tree_version(pyproject_path)


__version__ = _resolve_version()
