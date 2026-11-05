"""spillway — lossless, addressable paging for oversized tool results.

A 2 MB build log or a 40,000-line test suite does not belong in a conversation.
Hermes' own truncation throws the middle away; compaction throws all of it away.
This plugin intercepts every tool result at the ``transform_tool_result`` hook,
writes anything over a threshold byte-for-byte to a content-addressed page, and
hands the model a short preview plus a handle. The model then greps or reads
exact line ranges from the page instead of re-running the tool.

What it adds:

* ``transform_tool_result`` — spills large results, fail-open.
* ``spill_read`` / ``spill_grep`` / ``spill_list`` / ``spill_stats`` /
  ``spill_forget`` — exact ranges, regex search, inventory, totals, and real
  deletion of the page files.
* A bundled skill that teaches the agent to grep before reading and never to
  re-run the tool.

Everything is local: two tools' worth of Python, one SQLite index and a
directory of text files under the profile's ``plugin-data/spillway/``. No model
call is made, nothing is summarised, and the page bytes are the tool's bytes.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from . import schemas, store, tools, transform
from .settings import PLUGIN_ID, SCHEMA_KEYS, Settings

logger = logging.getLogger(__name__)

_CTX: Any = None
_CACHED: Settings | None = None
_CACHED_AT = 0.0
_CACHE_SECONDS = 2.0


def current_settings() -> Settings:
    """Resolved settings, memoised briefly so a tool loop does not re-read config."""
    global _CACHED, _CACHED_AT
    now = time.monotonic()
    if _CACHED is not None and now - _CACHED_AT < _CACHE_SECONDS:
        return _CACHED
    _CACHED = Settings() if _CTX is None else Settings.from_ctx(_CTX)
    _CACHED_AT = now
    return _CACHED


def _reset_cache() -> None:
    """Testing / profile-switch hook: forget memoised settings and DB init state."""
    global _CACHED, _CACHED_AT
    _CACHED = None
    _CACHED_AT = 0.0
    store.reset_cache()


def register(ctx: Any) -> None:
    """Wire the hook, the five tools and the bundled skill into one profile."""
    global _CTX

    _CTX = ctx
    settings = current_settings()

    handlers = {
        "spill_read": tools.spill_read,
        "spill_grep": tools.spill_grep,
        "spill_list": tools.spill_list,
        "spill_stats": tools.spill_stats,
        "spill_forget": tools.spill_forget,
    }
    for schema in schemas.ALL:
        ctx.register_tool(
            name=schema["name"],
            toolset="spillway",
            schema=schema,
            handler=handlers[schema["name"]],
        )

    ctx.register_hook("transform_tool_result", transform.handle)

    skill_dir = Path(__file__).parent / "skills" / "spillway"
    if (skill_dir / "SKILL.md").is_file():
        try:
            ctx.register_skill("spillway", str(skill_dir))
        except Exception:  # optional surface on older hosts
            logger.debug("spillway: could not register bundled skill", exc_info=True)

    try:
        store.ensure_ready()
    except Exception:  # a read-only home must not stop the tools from registering
        logger.debug("spillway: could not prepare the page store", exc_info=True)

    logger.info(
        "spillway ready: threshold=%s chars, budget=%s, retention=%s",
        settings.min_chars,
        settings.max_result_chars,
        settings.retention_days or "forever",
    )


__all__ = ["register", "current_settings", "PLUGIN_ID", "SCHEMA_KEYS", "store", "transform", "tools"]
