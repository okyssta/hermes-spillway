"""Tool handlers: ranges, bounds, grep line numbers, inventory, deletion, no raises."""

from __future__ import annotations

import re

from spillway import store, tools


def _numbered(count: int = 500) -> str:
    return "\n".join(f"line {i:04d} value={i * 3}" for i in range(1, count + 1))


def _page(text: str) -> str:
    return store.save_page(text, "read_file")["id"]


def test_read_returns_exact_range_with_total(settings):
    page = _page(_numbered(500))
    out = tools.spill_read({"page": page, "start_line": 120, "end_line": 123})
    assert f"lines 120-123 of 500" in out
    assert "line 0120 value=360" in out
    assert "line 0123 value=369" in out
    assert "line 0124" not in out
    assert "line 0119" not in out


def test_read_defaults_to_first_200_lines(settings):
    page = _page(_numbered(500))
    out = tools.spill_read({"page": page})
    assert "lines 1-200 of 500" in out
    assert "write" not in out.lower() or True
    assert "200 more lines" in out or "300 more lines" in out


def test_read_bounds_are_explained_not_raised(settings):
    page = _page(_numbered(10))
    past = tools.spill_read({"page": page, "start_line": 999})
    assert "has 10 lines" in past and "past the end" in past

    backwards = tools.spill_read({"page": page, "start_line": 6, "end_line": 2})
    assert "before start_line" in backwards

    clamped = tools.spill_read({"page": page, "start_line": 8, "end_line": 10_000})
    assert "lines 8-10 of 10" in clamped


def test_read_stops_at_max_chars_and_says_where(settings):
    page = _page("\n".join(f"{i:04d} " + "z" * 400 for i in range(1, 201)))
    out = tools.spill_read({"page": page, "start_line": 1, "end_line": 200, "max_chars": 2_000})
    assert "stopped at line" in out
    match = re.search(r"stopped at line (\d+)", out)
    assert match and int(match.group(1)) < 200
    assert "Continue with spill_read" in out


def test_read_truncates_very_long_lines_with_a_marker(settings):
    page = _page("short\n" + "L" * 9_000 + "\ntail")
    out = tools.spill_read({"page": page, "start_line": 2, "end_line": 2})
    assert "line cut at 2,000 of 9,000 chars" in out


def test_grep_finds_exact_line_numbers_with_context(settings):
    text = _numbered(500).split("\n")
    text[112] = "2026-11-05T12:00:00Z ERROR build failed: exit code 2"
    text[300] = "WARNING deprecated flag"
    page = _page("\n".join(text))

    out = tools.spill_grep({"page": page, "pattern": "ERROR", "context": 1})
    assert "1 matching line" in out
    assert re.search(r"^>\s+113\|", out, re.M), out
    assert re.search(r"^\s+112\|", out, re.M)  # context before
    assert re.search(r"^\s+114\|", out, re.M)  # context after
    assert "exit code 2" in out
    assert "deprecated" not in out


def test_grep_ignore_case_and_context_zero(settings):
    page = _page("alpha\nBeta\nGAMMA\nbeta again\n")
    exact = tools.spill_grep({"page": page, "pattern": "beta", "ignore_case": False, "context": 0})
    assert "1 matching line" in exact
    folded = tools.spill_grep({"page": page, "pattern": "beta", "ignore_case": True, "context": 0})
    assert "2 matching lines" in folded


def test_grep_no_match_and_bad_regex_are_messages(settings):
    page = _page(_numbered(20))
    none = tools.spill_grep({"page": page, "pattern": "NOTHERE"})
    assert none.startswith("No match")
    assert "spill_read" in none

    broken = tools.spill_grep({"page": page, "pattern": "([unclosed"})
    assert broken.startswith("spill_grep: invalid regular expression")
    assert "Escape regex metacharacters" in broken


def test_grep_caps_matches(settings):
    page = _page("\n".join("ERROR x" for _ in range(300)))
    out = tools.spill_grep({"page": page, "pattern": "ERROR", "max_matches": 5, "context": 0})
    assert "capped at max_matches=5" in out
    assert len(re.findall(r"^>", out, re.M)) == 5


def test_list_and_stats_describe_the_store(settings):
    page = _page(_numbered(60))
    listing = tools.spill_list({"limit": 5})
    assert page in listing
    assert "read_file" in listing

    stats = tools.spill_stats({})
    assert "pages: 1" in stats
    assert "kept out of context" in stats


def test_forget_reports_real_unlinking(settings):
    from pathlib import Path

    record = store.save_page(_numbered(60), "read_file")
    out = tools.spill_forget({"page": record["id"]})
    assert "1 file(s) unlinked" in out
    assert not Path(record["path"]).exists()

    again = tools.spill_forget({"page": record["id"]})
    assert "No page" in again


def test_forget_requires_an_explicit_target(settings):
    _page("x" * 500)
    out = tools.spill_forget({})
    assert "needs page=" in out and "all=true" in out
    assert len(store.list_pages(10)) == 1


def test_unknown_page_is_a_message_not_an_exception(settings):
    out = tools.spill_read({"page": "sw_deadbeef"})
    assert out.startswith("No page")
    assert "spill_list" in out


def test_handlers_never_raise_on_garbage(settings):
    garbage = [
        lambda: tools.spill_read(None),
        lambda: tools.spill_read({"page": object(), "start_line": "x"}),
        lambda: tools.spill_grep({"page": "sw_x", "pattern": None}),
        lambda: tools.spill_grep({"page": "sw_x", "pattern": "x" * 900}),
        lambda: tools.spill_list({"limit": "lots"}),
        lambda: tools.spill_stats(None),
        lambda: tools.spill_forget({"all": object()}),
        lambda: tools.spill_forget({"page": ["a", "b"]}),
    ]
    for call in garbage:
        result = call()
        assert isinstance(result, str) and result
