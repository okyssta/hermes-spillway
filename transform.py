"""The `transform_tool_result` hook: spill oversized results into a page.

The contract with Hermes is narrow and total: receive the result, return either
``None`` (leave it exactly as it was) or a replacement string. Every path is
wrapped so that a broken store, an unwritable disk or a surprising result type
returns ``None`` — the agent keeps its original output and the turn continues.

The replacement is deliberately self-describing. It names the page, shows the
head and tail of the real bytes, and tells the model which two tools to use
next and not to re-run the tool. Its own length is capped by
``max_result_chars`` so paging cannot recursively fill the context.
"""

from __future__ import annotations

import logging
from typing import Any

from . import store
from .schemas import TOOL_NAMES
from .settings import Settings

logger = logging.getLogger(__name__)

#: Results that already are a spillway replacement are never re-paged.
_MARKER = "[spillway]"
_TAIL_SUFFIX = "\n[preview clipped to fit the replacement budget]"


def _should_skip(tool_name: str, result: Any, settings: Settings) -> bool:
    if not isinstance(result, str) or not result:
        return True
    if len(result) < settings.min_chars:
        return True
    name = str(tool_name or "").strip()
    if name in TOOL_NAMES or name in settings.ignore_tools:
        return True
    if result.startswith(_MARKER):  # another spillway in the chain already handled it
        return True
    return False


def build_replacement(
    record: dict[str, Any],
    text: str,
    settings: Settings,
    *,
    tool_name: str = "",
) -> str:
    """The exact string the model will see in place of the result."""
    page = str(record.get("id") or "")
    chars = int(record.get("chars") or len(text))
    lines = int(record.get("lines") or store.count_lines(text))
    truncated = bool(record.get("truncated"))
    stored_chars = int(record.get("stored_chars") or len(text))
    budget = max(200, int(settings.max_result_chars))
    label = str(tool_name or record.get("tool") or "tool")

    head_budget = min(int(settings.preview_head), len(text))
    tail_budget = min(int(settings.preview_tail), max(0, len(text) - head_budget))

    def render(head_n: int, tail_n: int) -> str:
        if head_n + tail_n >= len(text):
            body = f"----- full output ({len(text):,} chars) -----\n{text}\n----- end -----"
            shown = f"preview: the whole result ({len(text):,} chars)"
        else:
            head = text[:head_n]
            tail = text[-tail_n:] if tail_n else ""
            parts = ["----- head -----", head]
            if tail:
                parts += ["----- tail -----", tail]
            parts.append("----- end preview -----")
            body = "\n".join(parts)
            shown = f"preview: first {head_n:,} and last {tail_n:,} chars"

        if truncated:
            custody = (
                f"page: {page}  (truncated: stored the first {stored_chars:,} of {chars:,} chars "
                f"because max_page_chars is {settings.max_page_chars:,}; the tail was NOT kept)"
            )
        else:
            custody = f"page: {page}  (byte-exact, nothing summarised, nothing dropped)"

        return (
            f"{_MARKER} {label} produced {chars:,} chars in {lines:,} lines — over the "
            f"{settings.min_chars:,}-char threshold, so the full result was written to a page "
            f"instead of into the context window.\n"
            f"{custody}\n"
            f"{shown}\n"
            f"{body}\n"
            f"Use spill_grep(page=\"{page}\", pattern=\"ERROR\") to find matching lines, or "
            f"spill_read(page=\"{page}\", start_line=1, end_line=200) for an exact range. "
            f"Quote the page's line numbers in your answer. Do not re-run {label} for more "
            f"output — the page already holds it."
        )

    out = render(head_budget, tail_budget)
    for _ in range(60):
        if len(out) <= budget:
            return out
        if head_budget > 120:
            head_budget = max(120, head_budget * 3 // 4)
        elif tail_budget > 100:
            tail_budget = max(100, tail_budget * 3 // 4)
        else:
            break
        out = render(head_budget, tail_budget)

    # Pathological budget: keep the handle and the instruction, drop preview text.
    fallback = (
        f"{_MARKER} {label} produced {chars:,} chars in {lines:,} lines. Full result is page "
        f"{page}. Use spill_grep(page=\"{page}\", pattern=...) or spill_read(page=\"{page}\", "
        f"start_line=1, end_line=200); do not re-run the tool."
    )
    if len(fallback) > budget:
        clipped = "\n[clipped]"
        return fallback[: max(0, budget - len(clipped))] + clipped
    return out[: max(0, budget - len(_TAIL_SUFFIX))] + _TAIL_SUFFIX


def handle(
    tool_name: str = "",
    args: Any = None,
    result: Any = None,
    **kwargs: Any,
) -> str | None:
    """Hook entry point. Returns a replacement string, or ``None`` to leave the result alone."""
    try:
        from . import current_settings

        settings = current_settings()
        if not settings.enabled or _should_skip(tool_name, result, settings):
            return None

        record = store.save_page(
            result,
            str(tool_name or ""),
            session_id=str(kwargs.get("session_id") or ""),
            tool_call_id=str(kwargs.get("tool_call_id") or ""),
            max_page_chars=settings.max_page_chars,
            retention_days=settings.retention_days,
        )
        if not record.get("id"):
            return None

        replacement = build_replacement(record, result, settings, tool_name=str(tool_name or ""))
        try:
            store.note_sent(str(record["id"]), len(replacement))
        except Exception:  # bookkeeping only
            logger.debug("spillway: could not record replacement size", exc_info=True)
        return replacement
    except Exception:
        # Fail open: the agent still gets the original result.
        logger.debug("spillway: transform failed, leaving the result untouched", exc_info=True)
        return None
