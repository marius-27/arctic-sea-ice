"""Checks that the project configuration is internally consistent.

These tests guard the tooling set up in the bootstrap phase: the Python
version, the development dependency group and the pre-commit hooks. They do
not touch any scientific data.
"""

import sys
import tomllib
from pathlib import Path

import yaml

import arctic_sea_ice

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _pyproject() -> dict:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as f:
        return tomllib.load(f)


def test_interpreter_is_python_314() -> None:
    assert sys.version_info[:2] == (3, 14)


def test_python_version_file_matches_requires_python() -> None:
    pinned = (PROJECT_ROOT / ".python-version").read_text(encoding="utf-8").strip()
    requires = _pyproject()["project"]["requires-python"]
    assert pinned == "3.14"
    assert requires == f">={pinned}"


def test_dev_group_contains_quality_tools() -> None:
    dev = _pyproject()["dependency-groups"]["dev"]
    names = {spec.split(">=")[0].split("==")[0].strip() for spec in dev}
    assert {"ruff", "pytest", "pytest-cov", "pre-commit", "mypy"} <= names


def test_ruff_targets_project_python_version() -> None:
    assert _pyproject()["tool"]["ruff"]["target-version"] == "py314"


def test_pre_commit_config_has_required_hooks() -> None:
    with (PROJECT_ROOT / ".pre-commit-config.yaml").open(encoding="utf-8") as f:
        config = yaml.safe_load(f)
    hook_ids = {hook["id"] for repo in config["repos"] for hook in repo["hooks"]}
    assert {"ruff-check", "ruff-format", "uv-lock", "check-added-large-files"} <= hook_ids


def test_package_is_importable() -> None:
    assert callable(arctic_sea_ice.main)
