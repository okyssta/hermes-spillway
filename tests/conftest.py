"""Shared harness: a throwaway HERMES_HOME per test, no Hermes host required.

The store and the tools are exercised the way they run in production — real
files, real SQLite — but under ``tmp_path`` so a developer's own pages are never
touched.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HERMES_SRC = Path(os.environ.get("HERMES_SRC", "/root/.hermes/hermes-agent"))


def pytest_configure(config):
    for path in (str(REPO_ROOT.parent), str(HERMES_SRC)):
        if path not in sys.path:
            sys.path.insert(0, path)


@pytest.fixture()
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / "hermes-home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    import spillway

    spillway._reset_cache()
    yield home
    spillway._reset_cache()


@pytest.fixture()
def settings(hermes_home):
    from spillway.settings import Settings

    return Settings()


@pytest.fixture()
def use_settings(monkeypatch):
    """Override the settings the hook and the tools resolve."""

    def apply(**overrides):
        from spillway import current_settings  # noqa: F401  (patched below)
        from spillway.settings import Settings

        value = Settings(**overrides)
        monkeypatch.setattr("spillway.current_settings", lambda: value)
        return value

    return apply
