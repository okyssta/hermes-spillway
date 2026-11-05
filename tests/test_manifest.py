"""Manifest and settings must not drift apart.

``hermes plugins validate`` compares declared tools/hooks with what register()
actually registers; this file compares the *config* surface, which validation
does not, and pins the manifest fields the catalog expects.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _manifest() -> dict:
    hermes_src = Path("/root/.hermes/hermes-agent")
    if str(hermes_src) not in sys.path:
        sys.path.insert(0, str(hermes_src))
    try:
        import hermes_yaml as yaml  # the CLI's own loader
    except ImportError:  # the pytest venv has PyYAML but not ruamel
        import yaml

    return yaml.safe_load((REPO / "plugin.yaml").read_text(encoding="utf-8"))


def test_manifest_identity_fields():
    manifest = _manifest()
    assert manifest["name"] == "spillway"
    assert manifest["manifest_version"] == 2
    assert manifest["api_version"] == 1
    assert manifest["requires_hermes"] == ">=0.22.0"
    assert manifest["license"] == "MIT"
    assert manifest["author"] == "kyssta"
    assert manifest["homepage"] == "https://github.com/okyssta/hermes-spillway"
    assert manifest["version"]


def test_config_schema_matches_settings_one_for_one():
    from spillway.settings import DEFAULTS, SCHEMA_KEYS

    schema = _manifest()["config_schema"]
    assert tuple(schema) == SCHEMA_KEYS
    for key, spec in schema.items():
        expected = DEFAULTS[key]
        if isinstance(expected, tuple):
            expected = list(expected)
        assert spec["default"] == expected, f"default for {key} drifted from settings.DEFAULTS"
        assert spec.get("description")
        assert spec.get("label")


def test_declared_capabilities_match_registrations():
    from spillway import schemas

    manifest = _manifest()
    assert manifest["provides_tools"] == list(schemas.TOOL_NAMES)
    assert manifest["provides_hooks"] == ["transform_tool_result"]


def test_description_ends_with_disclosure():
    description = str(_manifest()["description"]).strip()
    assert "Disclosure —" in description
    tail = description.rsplit("Disclosure —", 1)[1]
    assert tail.strip()
    assert description.rstrip().endswith(tail.strip())


def test_bundled_skill_frontmatter_exists():
    skill = (REPO / "skills" / "spillway" / "SKILL.md").read_text(encoding="utf-8")
    assert skill.startswith("---")
    assert "name: spillway" in skill
    assert "[spillway]" in skill
    assert "spill_grep" in skill
