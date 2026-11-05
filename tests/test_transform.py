"""The transform hook: threshold, budget, dedupe, ignore lists, fail-open."""

from __future__ import annotations

from pathlib import Path

import pytest

from spillway import store, transform


def _log(lines: int = 400, width: int = 80) -> str:
    return "\n".join(f"{i:06d} step ok " + "x" * width for i in range(1, lines + 1)) + "\n"


def test_short_result_is_unchanged(settings):
    small = "x" * (settings.min_chars - 1)
    assert transform.handle(tool_name="read_file", args={}, result=small) is None


def test_non_string_result_is_unchanged(settings):
    assert transform.handle(tool_name="read_file", args={}, result={"output": "x" * 50_000}) is None
    assert transform.handle(tool_name="read_file", args={}, result=b"x" * 50_000) is None
    assert transform.handle(tool_name="read_file", args={}, result=None) is None


def test_empty_string_is_unchanged(settings):
    assert transform.handle(tool_name="read_file", args={}, result="") is None


def test_large_result_becomes_a_page_with_instructions(settings):
    text = _log()
    replacement = transform.handle(tool_name="read_file", args={}, result=text)
    assert isinstance(replacement, str)
    assert replacement.startswith("[spillway] read_file produced")
    assert f"{len(text):,} chars" in replacement
    assert "page: sw_" in replacement
    assert 'spill_grep(page="sw_' in replacement
    assert 'spill_read(page="sw_' in replacement
    assert "Do not re-run read_file" in replacement
    # The head and tail of the real text are visible.
    assert text[:100] in replacement
    assert text[-40:] in replacement
    # The middle is not.
    assert text[len(text) // 2 : len(text) // 2 + 80] not in replacement


def test_replacement_stays_within_budget_for_5mb(use_settings):
    use_settings()
    text = "A" * (5 * 1024 * 1024)
    replacement = transform.handle(tool_name="terminal", args={}, result=text)
    assert isinstance(replacement, str)
    assert len(replacement) <= 4000
    # A single-line result must not smuggle 5 MB through the preview.
    assert replacement.count("A") < 4000


def test_replacement_respects_custom_budget(use_settings):
    use_settings(max_result_chars=900)
    replacement = transform.handle(tool_name="read_file", args={}, result=_log())
    assert len(replacement) <= 900
    assert "page: sw_" in replacement


def test_same_bytes_dedupe_to_one_page(settings):
    text = _log()
    first = transform.handle(tool_name="read_file", args={}, result=text)
    second = transform.handle(tool_name="terminal", args={}, result=text)
    assert first.splitlines()[0] and second.splitlines()[0]
    rows = store.list_pages(10)
    assert len(rows) == 1
    files = list((Path(store.pages_dir())).glob("*.txt"))
    assert len(files) == 1
    # The handle is stable across the two spills.
    page = rows[0]["id"]
    assert page in first and page in second


def test_own_tools_are_never_paged(settings):
    text = _log()
    for name in ("spill_read", "spill_grep", "spill_list", "spill_stats", "spill_forget"):
        assert transform.handle(tool_name=name, args={}, result=text) is None


def test_ignore_tools_is_honoured(use_settings):
    use_settings(ignore_tools=("terminal",))
    text = _log()
    assert transform.handle(tool_name="terminal", args={}, result=text) is None
    assert transform.handle(tool_name="read_file", args={}, result=text) is not None


def test_already_spilled_text_is_never_repaged(use_settings):
    use_settings()
    already = "[spillway] read_file produced 99 chars\n" + "x" * 30_000
    assert transform.handle(tool_name="read_file", args={}, result=already) is None


def test_fail_open_when_store_breaks(monkeypatch, settings):
    def boom(*args, **kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(store, "save_page", boom)
    assert transform.handle(tool_name="read_file", args={}, result=_log()) is None


def test_fail_open_when_settings_break(monkeypatch, settings):
    def boom():
        raise RuntimeError("config unreadable")

    monkeypatch.setattr("spillway.current_settings", boom)
    assert transform.handle(tool_name="read_file", args={}, result=_log()) is None


@pytest.mark.parametrize("tool_name", ["", None])
def test_missing_tool_name_still_pages(settings, tool_name):
    replacement = transform.handle(tool_name=tool_name, args={}, result=_log())
    assert isinstance(replacement, str)
    assert "page: sw_" in replacement
