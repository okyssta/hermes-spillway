"""Tool handlers.

Contract: ``def handler(args: dict | None = None, **kwargs) -> str``, never
raises, always returns text the model can act on. Each handler validates its own
arguments — a wrong handle, a malformed regex or a missing range produces a
readable message plus the next call to make, not a stack trace.
"""

from __future__ import annotations

import functools
import re
from typing import Any

from . import store
from .settings import Settings

_READ_MAX_LINE = 2_000
_GREP_MAX_LINE = 1_000
_GREP_MAX_CHARS = 12_000


def get_settings() -> Settings:
    from . import current_settings

    return current_settings()


def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _human_bytes(size: int) -> str:
    value = float(max(0, size))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:,.0f} {unit}" if unit == "B" else f"{value:,.1f} {unit}"
        value /= 1024
    return f"{value:,.1f} GB"


def _guard(fn):
    @functools.wraps(fn)
    def wrapper(args: dict | None = None, **kwargs: Any) -> str:
        try:
            data = args if isinstance(args, dict) else {}
            return fn(data, **kwargs)
        except Exception as exc:  # never raise into the tool dispatcher
            return f"spillway: {type(exc).__name__}: {exc}"

    return wrapper


def _missing_page(page: str) -> str:
    return (
        f"No page {page!r} in the spillway index. Call spill_list() to see the pages that exist; "
        "a page survives until retention GC or spill_forget removes it."
    )


def _page_or_error(page: str) -> tuple[dict[str, Any], str] | str:
    if not page:
        return "This tool needs a page handle, e.g. page=\"sw_ab12cd34\". Call spill_list() to find one."
    try:
        return store.load(page)
    except KeyError:
        return _missing_page(page)
    except FileNotFoundError:
        return (
            f"Page {page!r} is indexed but its file is gone (deleted outside spillway, or the disk "
            "was cleaned). The bytes cannot be recovered; do not re-run a destructive tool for them."
        )


# ------------------------------------------------------------------ spill_read


@_guard
def spill_read(args: dict, **kwargs: Any) -> str:
    data = args
    page = str(data.get("page") or "").strip()
    loaded = _page_or_error(page)
    if isinstance(loaded, str):
        return loaded
    record, text = loaded
    lines = store.split_lines(text)
    total = len(lines)
    if total == 0:
        return f"page {record['id']} is empty (0 lines)."

    start = _int(data.get("start_line"), 1, 0, 10**12)
    if start < 1:
        start = 1
    if start > total:
        return (
            f"page {record['id']} has {total:,} lines; start_line={start:,} is past the end. "
            f"Use start_line={total:,} for the last line."
        )
    raw_end = _int(data.get("end_line"), min(total, start + 199), 0, 10**12)
    if raw_end < start:
        return (
            f"end_line={raw_end:,} is before start_line={start:,}. Use an inclusive range, e.g. "
            f"start_line={start:,}, end_line={min(total, start + 199):,}."
        )
    end = min(total, raw_end)
    max_chars = _int(data.get("max_chars"), 12_000, 500, 200_000)

    width = max(6, len(str(total)))
    header = (
        f"page {record['id']} — {record.get('tool') or 'tool'} · {int(record.get('chars') or 0):,} chars "
        f"· lines {start:,}-{end:,} of {total:,}"
    )
    if record.get("truncated"):
        header += f" · PAGE TRUNCATED at {int(record.get('stored_chars') or 0):,} chars"
    out = [header, "-" * min(72, len(header))]
    used = len(header) + len(out[1])
    last = start
    stopped = False
    for number in range(start, end + 1):
        line = lines[number - 1]
        shown = line
        suffix = ""
        if len(shown) > _READ_MAX_LINE:
            suffix = f" … [line cut at {_READ_MAX_LINE:,} of {len(line):,} chars]"
            shown = shown[:_READ_MAX_LINE]
        rendered = f"{number:>{width}}| {shown}{suffix}"
        if used + len(rendered) + 1 > max_chars:
            stopped = True
            break
        out.append(rendered)
        used += len(rendered) + 1
        last = number

    if stopped:
        out.append(
            f"… stopped at line {last:,} (max_chars={max_chars:,}). "
            f"Continue with spill_read(page=\"{record['id']}\", start_line={last + 1:,}, "
            f"end_line={min(total, last + 200):,})."
        )
    elif end < total:
        out.append(
            f"… {total - end:,} more lines. Continue with start_line={end + 1:,}."
        )
    return "\n".join(out)


# ------------------------------------------------------------------ spill_grep


@_guard
def spill_grep(args: dict, **kwargs: Any) -> str:
    data = args
    page = str(data.get("page") or "").strip()
    pattern = str(data.get("pattern") or "")
    if not pattern:
        return "spill_grep needs a pattern, e.g. pattern=\"ERROR|Traceback\"."
    if len(pattern) > 500:
        return "spill_grep: pattern is longer than 500 chars — narrow it or use spill_read."
    ignore_case = _bool(data.get("ignore_case"), True)
    context = _int(data.get("context"), 2, 0, 20)
    max_matches = _int(data.get("max_matches"), 50, 1, 200)

    loaded = _page_or_error(page)
    if isinstance(loaded, str):
        return loaded
    record, text = loaded

    flags = re.IGNORECASE if ignore_case else 0
    try:
        regex = re.compile(pattern, flags)
    except re.error as exc:
        return (
            f"spill_grep: invalid regular expression {pattern!r}: {exc}. "
            "Escape regex metacharacters (\\. \\[ \\() or simplify the pattern."
        )

    lines = store.split_lines(text)
    total = len(lines)
    hits: list[int] = []
    for index, line in enumerate(lines):
        if regex.search(line):
            hits.append(index)
            if len(hits) >= max_matches:
                break

    scope = f"page {record['id']} — {total:,} lines"
    if not hits:
        return (
            f"No match for /{pattern}/ in {scope} (ignore_case={str(ignore_case).lower()}, "
            f"{int(record.get('chars') or 0):,} chars). Try a shorter pattern, or "
            f"spill_read(page=\"{record['id']}\", start_line=1, end_line=200) to page through."
        )

    windows: list[list[int]] = []
    for index in hits:
        first = max(0, index - context)
        last = min(total - 1, index + context)
        if windows and first <= windows[-1][1] + 1:
            windows[-1][1] = max(windows[-1][1], last)
        else:
            windows.append([first, last])

    matched = set(hits)
    width = max(6, len(str(total)))
    header = (
        f"{scope} — {len(hits):,} matching line{'s' if len(hits) != 1 else ''} for /{pattern}/ "
        f"(context {context}, ignore_case={str(ignore_case).lower()})"
    )
    if len(hits) >= max_matches:
        header += f", capped at max_matches={max_matches}"
    out = [header, "-" * min(72, len(header))]
    used = len(header) + len(out[1])
    truncated_output = False
    previous_end = -1
    for first, last in windows:
        if previous_end >= 0 and first > previous_end + 1:
            out.append("  …")
            used += 4
        previous_end = last
        for index in range(first, last + 1):
            line = lines[index]
            shown = line
            if len(shown) > _GREP_MAX_LINE:
                shown = shown[:_GREP_MAX_LINE] + f" … [line cut at {_GREP_MAX_LINE:,} chars]"
            marker = ">" if index in matched else " "
            rendered = f"{marker}{index + 1:>{width}}| {shown}"
            if used + len(rendered) + 1 > _GREP_MAX_CHARS:
                truncated_output = True
                break
            out.append(rendered)
            used += len(rendered) + 1
        if truncated_output:
            break

    if truncated_output:
        out.append(
            f"… output capped at {_GREP_MAX_CHARS:,} chars; narrow the pattern or raise context "
            "precision with more specific text."
        )
    elif len(hits) >= max_matches:
        out.append(
            f"… stopped after {max_matches} matching lines (max_matches). Add text to the pattern "
            "to narrow it."
        )
    out.append(
        f"Read around a hit with spill_read(page=\"{record['id']}\", start_line=N, end_line=M)."
    )
    return "\n".join(out)


# ------------------------------------------------------------------ spill_list


@_guard
def spill_list(args: dict, **kwargs: Any) -> str:
    limit = _int(args.get("limit"), 20, 1, 100)
    rows = store.list_pages(limit)
    if not rows:
        return "spillway has no pages yet. Large tool results are paged automatically."
    out = [f"spillway pages — {len(rows)} most recent", "-" * 72]
    for row in rows:
        preview = str(row.get("preview") or "").replace("\n", " ")
        flag = " [truncated]" if row.get("truncated") else ""
        out.append(
            f"{row['id']}  {store.iso(row['created'])}  {int(row['chars']):,} chars  "
            f"{int(row['lines']):,} lines{flag}  {row.get('tool') or 'tool'}"
        )
        if preview:
            out.append(f"    {preview[:140]}")
    out.append("Use spill_grep(page=..., pattern=...) or spill_read(page=..., start_line=...).")
    return "\n".join(out)


# ------------------------------------------------------------------ spill_stats


@_guard
def spill_stats(args: dict, **kwargs: Any) -> str:
    settings = get_settings()
    data = store.stats()
    pages = int(data.get("pages") or 0)
    if not pages:
        return (
            "spillway: 0 pages stored.\n"
            f"Threshold: results over {settings.min_chars:,} chars are paged; "
            f"replacement budget {settings.max_result_chars:,} chars."
        )
    oldest = store.iso(data["oldest"]) if data.get("oldest") else "?"
    newest = store.iso(data["newest"]) if data.get("newest") else "?"
    return "\n".join(
        [
            "spillway stats",
            f"pages: {pages:,}",
            f"on disk: {_human_bytes(int(data.get('disk_bytes') or 0))} "
            f"({int(data.get('stored_chars') or 0):,} stored chars, {int(data.get('lines') or 0):,} lines)",
            f"paged: {int(data.get('chars') or 0):,} chars across {pages:,} results",
            f"kept out of context: {int(data.get('chars_saved') or 0):,} chars "
            f"({int(data.get('sent_chars') or 0):,} chars of replacements instead)",
            f"truncated at max_page_chars: {int(data.get('truncated') or 0):,} pages",
            f"range: {oldest} → {newest}",
            f"retention: {settings.retention_days or 'forever'} · "
            f"threshold: {settings.min_chars:,} chars",
        ]
    )


# ------------------------------------------------------------------ spill_forget


@_guard
def spill_forget(args: dict, **kwargs: Any) -> str:
    page = str(args.get("page") or "").strip()
    everything = _bool(args.get("all"), False)
    if not page and not everything:
        return (
            "spill_forget needs page=\"sw_…\" or all=true. It deletes the page files from disk, "
            "so it refuses to run without an explicit target."
        )
    result = store.forget(page, everything=everything)
    if not result["pages"]:
        return _missing_page(page) if page else "spillway had no pages to forget."
    label = "all pages" if everything else ", ".join(result["pages"])
    return (
        f"Deleted {len(result['pages'])} page(s) ({label}); "
        f"{result['files_removed']} file(s) unlinked, {result['chars']:,} chars removed from disk. "
        "This is irreversible."
    )
