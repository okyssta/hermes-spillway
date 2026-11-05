"""Tool schemas for spillway.

Descriptions assume a model that has never heard of this plugin. They say what a
page *is*, that it is byte-exact, and which call to make next — because the
replacement text is the only introduction most models get.
"""

from __future__ import annotations

SPILL_READ = {
    "name": "spill_read",
    "description": (
        "Read an exact, 1-based inclusive line range from a spillway page — the full text of an "
        "oversized tool result that was saved instead of pasted into the conversation. Use it when "
        "a result began with '[spillway]' and you need the actual lines, not a summary. Returns the "
        "real text with its line numbers and the page's total line count. Nothing is summarised "
        "and nothing is missing: line 1,203 is the tool's line 1,203."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "page": {
                "type": "string",
                "description": "Page handle from the replacement, e.g. 'sw_ab12cd34'.",
            },
            "start_line": {
                "type": "integer",
                "description": "First line to return, 1-based (default 1).",
            },
            "end_line": {
                "type": "integer",
                "description": "Last line to return, inclusive. Default 200. Clamped to the page.",
            },
            "max_chars": {
                "type": "integer",
                "description": "Budget for the returned text (default 12000). Long lines are cut with a marker.",
            },
        },
        "required": ["page"],
    },
}

SPILL_GREP = {
    "name": "spill_grep",
    "description": (
        "Search a spillway page with a regular expression and get matching line numbers plus "
        "surrounding context. Use this FIRST on a page — it is far cheaper than reading the range "
        "and answers 'where is the error?'. Python regex syntax; the pattern is validated, an "
        "invalid one returns a message instead of raising. Output is capped, so narrow the pattern "
        "rather than asking for everything."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "page": {"type": "string", "description": "Page handle, e.g. 'sw_ab12cd34'."},
            "pattern": {
                "type": "string",
                "description": "Regular expression, e.g. 'ERROR|Traceback', 'exit code \\d+', 'tenant_id'.",
            },
            "context": {
                "type": "integer",
                "description": "Lines of context before and after each match (default 2).",
            },
            "max_matches": {
                "type": "integer",
                "description": "Stop after this many matching lines (default 50, max 200).",
            },
            "ignore_case": {
                "type": "boolean",
                "description": "Case-insensitive match (default true).",
            },
        },
        "required": ["page", "pattern"],
    },
}

SPILL_LIST = {
    "name": "spill_list",
    "description": (
        "List the most recent spillway pages: handle, the tool that produced each, size in chars "
        "and lines, when it was written, and the first few words. Use it when you have lost a "
        "handle or want to know what is paged from earlier in this session."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "How many pages to show (default 20, max 100)."}
        },
        "required": [],
    },
}

SPILL_STATS = {
    "name": "spill_stats",
    "description": (
        "Show spillway's totals: pages stored, bytes on disk, characters paged, and characters "
        "kept out of the context window. Useful to explain where the context budget went, and to "
        "check retention before relying on an old page."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

SPILL_FORGET = {
    "name": "spill_forget",
    "description": (
        "Delete spillway pages from disk — one page by handle, or all of them. This really removes "
        "the files, not just the index entries, so use it when the user asks to forget captured "
        "output or when a page holds something sensitive. Irreversible; the tool result itself is "
        "not re-runnable from spillway afterwards."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "page": {"type": "string", "description": "Page handle to delete, e.g. 'sw_ab12cd34'."},
            "all": {
                "type": "boolean",
                "description": "Delete every page. Requires all=true and is refused when both page and all are empty.",
            },
        },
        "required": [],
    },
}

ALL = (SPILL_READ, SPILL_GREP, SPILL_LIST, SPILL_STATS, SPILL_FORGET)
TOOL_NAMES = tuple(schema["name"] for schema in ALL)
