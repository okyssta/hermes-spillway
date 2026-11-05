"""Page store: byte-exact text files plus a small SQLite index.

A *page* is the full text of one oversized tool result, written verbatim to
``pages/<sha256>.txt`` and addressed by a short, stable id (``sw_ab12cd34``).
The SQLite index only holds metadata — size, line count, tool, time, session —
so losing it degrades listing, never the page bytes themselves.

Design rules:

* writes are atomic (temp file in the same directory, fsync, ``os.replace``);
* the sha256 of the full UTF-8 bytes is the identity, so the same result twice
  costs one file and one row;
* a failed write, read or query raises to the caller, which is a *tool* or the
  fail-open transform hook — never the agent loop;
* ``forget`` unlinks the file first and only then drops the row, so "forget"
  cannot report success while bytes remain on disk.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_TEXT_ERRORS = "surrogatepass"  # round-trips lone surrogates instead of raising

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
    id            TEXT PRIMARY KEY,
    sha           TEXT NOT NULL UNIQUE,
    tool          TEXT NOT NULL DEFAULT '',
    chars         INTEGER NOT NULL DEFAULT 0,
    lines         INTEGER NOT NULL DEFAULT 0,
    stored_chars  INTEGER NOT NULL DEFAULT 0,
    truncated     INTEGER NOT NULL DEFAULT 0,
    preview       TEXT NOT NULL DEFAULT '',
    created       REAL NOT NULL DEFAULT 0,
    session_id    TEXT NOT NULL DEFAULT '',
    tool_call_id  TEXT NOT NULL DEFAULT '',
    sent_chars    INTEGER NOT NULL DEFAULT 0,
    path          TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS pages_created ON pages(created DESC);
"""

_init_lock = threading.Lock()
_initialised: set[str] = set()


# --------------------------------------------------------------------- paths


def pages_dir() -> Path:
    from .settings import data_dir

    path = data_dir() / "pages"
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    from .settings import data_dir

    return data_dir() / "index.db"


def _connect() -> sqlite3.Connection:
    """A connection with its schema applied. Cheap; every call closes it."""
    path = db_path()
    key = str(path)
    conn = sqlite3.connect(key, timeout=15.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=15000")
        if key not in _initialised:
            with _init_lock:
                if key not in _initialised:
                    conn.executescript(_SCHEMA)
                    conn.commit()
                    _initialised.add(key)
    except Exception:
        conn.close()
        raise
    return conn


def ensure_ready() -> None:
    """Create the data directory, page directory and index (best effort)."""
    pages_dir()
    conn = _connect()
    conn.close()


def reset_cache() -> None:
    """Forget which databases this process has initialised (tests, profile switch)."""
    with _init_lock:
        _initialised.clear()


# --------------------------------------------------------------------- bytes


def _encode(text: str) -> bytes:
    return text.encode("utf-8", _TEXT_ERRORS)


def sha_for(text: str) -> str:
    return hashlib.sha256(_encode(text)).hexdigest()


def count_lines(text: str) -> int:
    """Physical lines, CRLF-aware, with no phantom line after a trailing newline."""
    if not text:
        return 0
    total = text.count("\n")
    if not text.endswith("\n"):
        total += 1
    return total


def split_lines(text: str) -> list[str]:
    """Split on ``\\n`` only and drop one trailing ``\\r`` — keeps lines 1:1 with bytes."""
    if not text:
        return []
    body = text[:-1] if text.endswith("\n") else text
    lines = [line[:-1] if line.endswith("\r") else line for line in body.split("\n")]
    return lines


def _preview(text: str, limit: int = 160) -> str:
    flat = " ".join(text[:4000].split())
    flat = flat.encode("utf-8", "replace").decode("utf-8")  # SQLite TEXT rejects lone surrogates
    return flat[:limit] + ("…" if len(flat) > limit else "")


def _truncate(text: str, limit: int) -> tuple[str, int]:
    """Store the first *limit* chars, on a line boundary, with a loud marker."""
    kept = text[:limit]
    cut = kept.rfind("\n")
    if cut >= int(limit * 0.9):
        kept = kept[: cut + 1]
    marker = (
        f"\n[spillway: page truncated at {len(kept):,} of {len(text):,} characters — "
        f"max_page_chars is {limit:,}; the remainder was NOT stored]\n"
    )
    return kept + marker, len(kept)


def _pick_id(conn: sqlite3.Connection, sha: str) -> str:
    """Shortest prefix of *sha* that is free, or owned by this same content."""
    for length in (8, 10, 12, 16, 32):
        candidate = "sw_" + sha[:length]
        row = conn.execute("SELECT sha FROM pages WHERE id = ?", (candidate,)).fetchone()
        if row is None or row["sha"] == sha:
            return candidate
    return "sw_" + sha[:32] + uuid.uuid4().hex[:4]


def _write_atomic(path: Path, data: bytes) -> None:
    if path.exists():  # content-addressed: identical bytes are already there
        return
    tmp = path.parent / f".{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
    try:
        with open(tmp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------- write


def save_page(
    text: str,
    tool: str,
    *,
    session_id: str = "",
    tool_call_id: str = "",
    max_page_chars: int = 8 * 1024 * 1024,
    retention_days: int = 0,
) -> dict[str, Any]:
    """Write *text* to a page and index it. Returns the page record.

    The file is keyed by the sha256 of the **full** text, so a truncated page is
    still identified by what it truncated; ``truncated`` says so in the record
    and the marker says so at the end of the file.
    """
    sha = sha_for(text)
    chars = len(text)
    lines = count_lines(text)
    truncated = chars > max_page_chars
    if truncated:
        stored_text, stored_chars = _truncate(text, max_page_chars)
    else:
        stored_text, stored_chars = text, chars
    stored_lines = count_lines(stored_text)

    path = pages_dir() / f"{sha}.txt"
    _write_atomic(path, _encode(stored_text))

    created = time.time()
    conn = _connect()
    try:
        with conn:
            page_id = _pick_id(conn, sha)
            cursor = conn.execute(
                "INSERT OR IGNORE INTO pages (id, sha, tool, chars, lines, stored_chars, truncated,"
                " preview, created, session_id, tool_call_id, sent_chars, path)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
                (
                    page_id,
                    sha,
                    str(tool or ""),
                    chars,
                    lines,
                    stored_chars,
                    1 if truncated else 0,
                    _preview(stored_text),
                    created,
                    str(session_id or ""),
                    str(tool_call_id or ""),
                    str(path),
                ),
            )
        row = conn.execute("SELECT * FROM pages WHERE sha = ?", (sha,)).fetchone()
    finally:
        conn.close()

    if retention_days > 0:
        try:
            gc(retention_days, keep_sha=sha)
        except Exception:
            pass  # GC is housekeeping; a failure must not lose this page

    record = dict(row) if row is not None else {}
    record["deduped"] = cursor.rowcount == 0
    record["truncated"] = bool(record.get("truncated"))
    return record


def note_sent(page_id: str, sent_chars: int) -> None:
    """Record how many chars the replacement actually cost the context."""
    conn = _connect()
    try:
        with conn:
            conn.execute("UPDATE pages SET sent_chars = ? WHERE id = ?", (int(sent_chars), page_id))
    finally:
        conn.close()


# ----------------------------------------------------------------------- read


def _row_to_record(row: sqlite3.Row) -> dict[str, Any]:
    record = dict(row)
    record["truncated"] = bool(record.get("truncated"))
    return record


def find(page_id: str = "", sha: str = "") -> dict[str, Any] | None:
    """Look a page up by id (``sw_…``, with or without ``.txt``) or full sha."""
    wanted = str(page_id or "").strip()
    if wanted.endswith(".txt"):
        wanted = wanted[:-4]
    if wanted.startswith("sw_"):
        key, column = wanted, "id"
    elif sha:
        key, column = sha.strip(), "sha"
    elif len(wanted) == 64:
        key, column = wanted, "sha"
    else:
        key, column = wanted, "id"
    if not key:
        return None
    conn = _connect()
    try:
        row = conn.execute(f"SELECT * FROM pages WHERE {column} = ?", (key,)).fetchone()
    finally:
        conn.close()
    return _row_to_record(row) if row is not None else None


def read_text(record: dict[str, Any]) -> str:
    """The page bytes, decoded exactly as they were written."""
    path = Path(str(record.get("path") or ""))
    if not str(path):
        raise FileNotFoundError("page has no file path")
    data = path.read_bytes()
    return data.decode("utf-8", _TEXT_ERRORS)


def load(page_id: str) -> tuple[dict[str, Any], str]:
    """Record + text for *page_id*. Raises ``KeyError`` for an unknown page."""
    record = find(page_id)
    if record is None:
        raise KeyError(f"no page {page_id!r}")
    return record, read_text(record)


# ---------------------------------------------------------------------- housekeeping


def _safe_unlink(path_text: str) -> bool:
    """Unlink a page file only if it sits inside our own pages directory."""
    if not path_text:
        return False
    path = Path(path_text)
    try:
        root = pages_dir().resolve()
        target = path.resolve()
    except OSError:
        return False
    if target.parent != root:
        return False
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def forget(page_id: str = "", everything: bool = False) -> dict[str, Any]:
    """Delete page files and rows. The file goes first; a failure raises."""
    conn = _connect()
    try:
        if everything:
            rows = conn.execute("SELECT id, sha, chars, path FROM pages").fetchall()
        else:
            rows = conn.execute(
                "SELECT id, sha, chars, path FROM pages WHERE id = ?", (str(page_id or "").strip(),)
            ).fetchall()
        removed_files = 0
        for row in rows:
            if _safe_unlink(row["path"]):
                removed_files += 1
        with conn:
            if everything:
                conn.execute("DELETE FROM pages")
            else:
                conn.execute("DELETE FROM pages WHERE id = ?", (str(page_id or "").strip(),))
    finally:
        conn.close()
    return {
        "pages": [row["id"] for row in rows],
        "files_removed": removed_files,
        "chars": sum(int(row["chars"] or 0) for row in rows),
    }


def gc(retention_days: int, *, keep_sha: str = "") -> dict[str, Any]:
    """Drop pages older than *retention_days*, never the one just written."""
    if retention_days <= 0:
        return {"pages": [], "files_removed": 0}
    cutoff = time.time() - retention_days * 86_400
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, sha, path FROM pages WHERE created < ? AND sha != ?", (cutoff, keep_sha)
        ).fetchall()
        removed_files = sum(1 for row in rows if _safe_unlink(row["path"]))
        with conn:
            for row in rows:
                conn.execute("DELETE FROM pages WHERE id = ?", (row["id"],))
    finally:
        conn.close()
    return {"pages": [row["id"] for row in rows], "files_removed": removed_files}


def list_pages(limit: int = 20) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM pages ORDER BY created DESC LIMIT ?", (max(1, int(limit)),)
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_record(row) for row in rows]


def stats() -> dict[str, Any]:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS pages, COALESCE(SUM(chars),0) AS chars,"
            " COALESCE(SUM(stored_chars),0) AS stored_chars,"
            " COALESCE(SUM(lines),0) AS lines,"
            " COALESCE(SUM(sent_chars),0) AS sent_chars,"
            " COALESCE(SUM(truncated),0) AS truncated,"
            " MIN(created) AS oldest, MAX(created) AS newest"
            " FROM pages"
        ).fetchone()
        rows = conn.execute("SELECT path FROM pages").fetchall()
    finally:
        conn.close()
    disk = 0
    for item in rows:
        try:
            disk += os.path.getsize(item["path"])
        except OSError:
            pass
    data = dict(row) if row is not None else {}
    data["disk_bytes"] = disk
    data["chars_saved"] = max(0, int(data.get("chars") or 0) - int(data.get("sent_chars") or 0))
    return data


def iso(created: float) -> str:
    try:
        return datetime.fromtimestamp(float(created), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError, OSError):
        return "?"
