"""Settings for `spillway`.

Every key in :data:`SCHEMA_KEYS` is mirrored one-for-one in ``plugin.yaml``'s
``config_schema``; ``tests/test_manifest.py`` asserts the two key sets are
identical, so the Desktop settings form, ``hermes config set`` and this module
cannot drift apart. Values are coerced defensively: a typo in ``config.yaml``
must never stop a tool result from being shown.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PLUGIN_ID = "spillway"

# One entry per plugin.yaml config_schema key, in the same order.
SCHEMA_KEYS = (
    "enabled",
    "min_chars",
    "max_page_chars",
    "preview_head",
    "preview_tail",
    "max_result_chars",
    "retention_days",
    "ignore_tools",
)

# Defaults live here *and* in plugin.yaml; a test compares them.
DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "min_chars": 20_000,
    "max_page_chars": 8 * 1024 * 1024,
    "preview_head": 1_200,
    "preview_tail": 800,
    "max_result_chars": 4_000,
    "retention_days": 0,
    "ignore_tools": (),
}


def hermes_home() -> Path:
    """The active Hermes home (context override -> ``HERMES_HOME`` -> default)."""
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home())
    except Exception:  # pragma: no cover - standalone / older hosts
        env = os.environ.get("HERMES_HOME", "").strip()
        if env:
            return Path(os.path.expandvars(os.path.expanduser(env)))
        return Path.home() / ".hermes"


def data_dir() -> Path:
    """``<hermes home>/plugin-data/spillway`` — created on demand."""
    root = hermes_home() / "plugin-data" / PLUGIN_ID
    root.mkdir(parents=True, exist_ok=True)
    return root


def _bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off", ""}:
            return False
    return default


def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _tools(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        raw = value.replace(",", "\n").splitlines()
    elif isinstance(value, (list, tuple, set)):
        raw = list(value)
    else:
        return ()
    seen: list[str] = []
    for item in raw:
        name = str(item).strip()
        if name and name not in seen:
            seen.append(name)
    return tuple(seen)


@dataclass(frozen=True)
class Settings:
    """Resolved, clamped settings for one turn."""

    enabled: bool = True
    min_chars: int = 20_000
    max_page_chars: int = 8 * 1024 * 1024
    preview_head: int = 1_200
    preview_tail: int = 800
    max_result_chars: int = 4_000
    retention_days: int = 0
    ignore_tools: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_ctx(cls, ctx: Any) -> "Settings":
        get = getattr(ctx, "get_config", None)
        if not callable(get):  # pragma: no cover - older hosts
            return cls()

        def value(key: str) -> Any:
            fallback = DEFAULTS[key]
            try:
                resolved = get(key, default=fallback)
            except TypeError:  # hosts whose get_config takes one argument
                resolved = get(key)
            return fallback if resolved is None else resolved

        return cls(
            enabled=_bool(value("enabled"), True),
            min_chars=_int(value("min_chars"), 20_000, 200, 100_000_000),
            max_page_chars=_int(value("max_page_chars"), 8 * 1024 * 1024, 1_000, 512 * 1024 * 1024),
            preview_head=_int(value("preview_head"), 1_200, 0, 60_000),
            preview_tail=_int(value("preview_tail"), 800, 0, 60_000),
            max_result_chars=_int(value("max_result_chars"), 4_000, 400, 60_000),
            retention_days=_int(value("retention_days"), 0, 0, 36_500),
            ignore_tools=_tools(value("ignore_tools")),
        )

    def describe(self) -> str:
        return "\n".join(
            [
                f"enabled: {self.enabled}",
                f"spill threshold: {self.min_chars:,} chars",
                f"page cap: {self.max_page_chars:,} chars",
                f"preview: first {self.preview_head:,} / last {self.preview_tail:,} chars",
                f"replacement budget: {self.max_result_chars:,} chars",
                f"retention: {self.retention_days or 'forever'}",
                f"ignored tools: {', '.join(self.ignore_tools) or '(none)'}",
            ]
        )
