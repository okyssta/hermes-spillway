"""The page store: byte-exact files, dedupe, truncation marker, retention GC."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from spillway import store

UNICODE_LOG = "".join(
    f"{i:06d}\t配置 — 日本語 → ok 🚀 café\r\n" for i in range(1, 9_000)
)  # ~300 KB, CRLF, multi-byte, emoji


def test_byte_exact_roundtrip_of_unicode_crlf_log(settings):
    assert len(UNICODE_LOG.encode("utf-8")) > 300_000
    record = store.save_page(UNICODE_LOG, "read_file")
    path = Path(record["path"])

    assert path.read_bytes() == UNICODE_LOG.encode("utf-8")
    loaded_record, text = store.load(record["id"])
    assert text == UNICODE_LOG
    assert loaded_record["chars"] == len(UNICODE_LOG)
    assert loaded_record["lines"] == store.count_lines(UNICODE_LOG)
    assert loaded_record["truncated"] is False


def test_split_lines_is_one_to_one_with_physical_lines():
    text = "a\r\nb\r\nc\n"
    lines = store.split_lines(text)
    assert lines == ["a", "b", "c"]
    assert store.count_lines(text) == 3
    assert store.count_lines("") == 0
    assert store.count_lines("no trailing newline") == 1


def test_duplicate_content_reuses_one_file_and_one_row(settings):
    text = UNICODE_LOG
    first = store.save_page(text, "read_file")
    second = store.save_page(text, "terminal")
    assert first["id"] == second["id"]
    assert second["deduped"] is True
    assert len(list(Path(store.pages_dir()).glob("*.txt"))) == 1
    assert len(store.list_pages(10)) == 1


def test_oversize_page_is_truncated_with_an_explicit_marker(settings):
    text = "line\n" * 4_000
    record = store.save_page(text, "terminal", max_page_chars=2_000)
    assert record["truncated"] is True
    assert record["chars"] == len(text)
    assert record["stored_chars"] < len(text)

    stored = Path(record["path"]).read_text(encoding="utf-8", errors="surrogatepass")
    assert stored.startswith("line\n")
    assert "page truncated at" in stored
    assert "the remainder was NOT stored" in stored
    assert "max_page_chars is 2,000" in stored


def test_retention_gc_removes_old_pages_but_never_the_new_one(settings):
    old = store.save_page("old content\n" * 100, "read_file")
    old_path = Path(old["path"])
    conn = sqlite3.connect(str(store.db_path()))
    with conn:
        conn.execute("UPDATE pages SET created = ? WHERE id = ?", (time.time() - 10 * 86_400, old["id"]))
    conn.close()

    fresh = store.save_page("fresh content\n" * 100, "read_file", retention_days=2)
    assert not old_path.exists(), "retention GC must unlink the expired page file"
    assert Path(fresh["path"]).exists(), "GC must never delete the page just written"
    ids = {row["id"] for row in store.list_pages(10)}
    assert ids == {fresh["id"]}


def test_retention_zero_keeps_everything(settings):
    first = store.save_page("a" * 500, "read_file", retention_days=0)
    second = store.save_page("b" * 500, "read_file", retention_days=0)
    assert {first["id"], second["id"]} <= {row["id"] for row in store.list_pages(10)}


def test_forget_unlinks_the_file_and_drops_the_row(settings):
    record = store.save_page(UNICODE_LOG, "read_file")
    path = Path(record["path"])
    assert path.exists()

    result = store.forget(record["id"])
    assert record["id"] in result["pages"]
    assert result["files_removed"] == 1
    assert not path.exists(), "forget must unlink the page file, not just the index row"
    assert store.find(record["id"]) is None
    assert store.read_text  # sanity: module still importable after deletion


def test_forget_all_removes_every_file(settings):
    one = store.save_page("one\n" * 100, "read_file")
    two = store.save_page("two\n" * 200, "terminal")
    result = store.forget(everything=True)
    assert set(result["pages"]) == {one["id"], two["id"]}
    assert not Path(one["path"]).exists()
    assert not Path(two["path"]).exists()
    assert store.list_pages(10) == []


def test_forget_cannot_escape_the_pages_directory(settings, tmp_path):
    outside = tmp_path / "important.txt"
    outside.write_text("do not delete", encoding="utf-8")
    record = store.save_page("safe\n" * 100, "read_file")
    conn = sqlite3.connect(str(store.db_path()))
    with conn:
        conn.execute("UPDATE pages SET path = ? WHERE id = ?", (str(outside), record["id"]))
    conn.close()

    store.forget(record["id"])
    assert outside.exists(), "a tampered index row must not let forget unlink outside pages/"


def test_binary_ish_text_with_nul_and_lone_surrogate_roundtrips(settings):
    text = "before\x00after\n\x01\x02\x03\nlone:\ud800:end\n"
    record = store.save_page(text, "terminal")
    assert Path(record["path"]).read_bytes() == text.encode("utf-8", "surrogatepass")
    assert store.load(record["id"])[1] == text


def test_concurrent_writes_are_safe(settings):
    from concurrent.futures import ThreadPoolExecutor

    payloads = [f"thread payload {i}\n" * 2_000 for i in range(10)]
    with ThreadPoolExecutor(max_workers=10) as pool:
        records = list(pool.map(lambda body: store.save_page(body, "read_file"), payloads))
    assert len({record["id"] for record in records}) == len(payloads)
    assert len(store.list_pages(50)) == len(payloads)
    for body, record in zip(payloads, records):
        assert store.load(record["id"])[1] == body


def test_missing_plugin_data_directory_is_recreated(settings):
    import shutil

    shutil.rmtree(Path(store.db_path()).parent)
    store.reset_cache()
    record = store.save_page("after the directory vanished\n" * 50, "read_file")
    assert Path(record["path"]).exists()
    assert store.find(record["id"]) is not None
