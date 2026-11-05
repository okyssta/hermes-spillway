# spillway

**Lossless, addressable paging for oversized tool results.**

A 2 MB build log does not belong in a conversation, but its middle is usually
the only part that matters. Hermes truncates large tool results, so the middle
is exactly what disappears; if the result is small enough to keep, it pushes the
conversation toward compaction, which loses the rest.

`spillway` intercepts every tool result *before* it enters the context. Anything
over a threshold is written byte-for-byte to a content-addressed page on disk,
and the model receives a short preview plus a handle:

```
[spillway] read_file produced 300,450 chars in 2,858 lines — over the 20,000-char threshold, so the full result was written to a page instead of into the context window.
page: sw_445ef68b  (byte-exact, nothing summarised, nothing dropped)
preview: first 1,200 and last 800 chars
...
Use spill_grep(page="sw_445ef68b", pattern="ERROR") to find matching lines, or spill_read(page="sw_445ef68b", start_line=1, end_line=200) for an exact range. ...
```

The agent then greps the page for the error, reads the exact lines around it,
and quotes the tool's own line numbers. No model call, no compression, nothing
dropped.

## Why

- **The middle is the answer.** Head-and-tail truncation keeps the command and
  the exit code and throws away the error at line 1,714.
- **Re-running is not always possible.** A build log from a CI box, a deleted
  file, a failing command with side effects: the page is the only copy.
- **Compaction is expensive and lossy.** Paging costs one file write and ~2.6 KB
  of context instead of 300 KB, so the conversation lasts longer and nothing has
  to be summarised away.
- **Verifiability.** Every byte is on disk, addressed by sha256. "Line 1,714"
  means the same thing to the model, to you, and to `spill_read`.

## Quickstart

```bash
hermes plugins install okyssta/hermes-spillway --enable
# or, for local development:
git clone https://github.com/okyssta/hermes-spillway ~/.hermes/plugins/spillway
hermes plugins enable spillway
```

Nothing else is required. The first tool result over 20,000 chars is paged; the
five tools appear immediately. Tune it in `config.yaml` under
`plugins.entries.spillway.settings` (or the Desktop plugin settings form):

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Page oversized results at all |
| `min_chars` | `20000` | Results at or above this size become a page |
| `max_page_chars` | `8388608` | Cap per page; larger results are stored truncated **with an explicit marker** |
| `preview_head` | `1200` | Leading chars shown in the replacement |
| `preview_tail` | `800` | Trailing chars shown in the replacement |
| `max_result_chars` | `4000` | Hard cap on the replacement itself |
| `retention_days` | `0` | Delete pages older than this on write; `0` keeps them |
| `ignore_tools` | `[]` | Tool names never paged (spillway's own tools are always excluded) |

## Tools

| Tool | What it does |
|---|---|
| `spill_read(page, start_line=1, end_line=200, max_chars=12000)` | Exact 1-based inclusive line range, with line numbers and the page's total line count. Long lines are cut with a marker; a cut window says where it stopped. |
| `spill_grep(page, pattern, context=2, max_matches=50, ignore_case=true)` | Python-regex search with context. Invalid patterns return a message. Output is capped at 12,000 chars. |
| `spill_list(limit=20)` | Recent pages: handle, tool, size, lines, time, preview. |
| `spill_stats()` | Pages, bytes on disk, chars paged, chars kept out of context, retention. |
| `spill_forget(page \| all)` | **Deletes the page files** (unlink, then row) — not just index entries. Refuses to run without an explicit target. |

The bundled skill teaches the agent the three rules: never re-run the tool,
grep before reading, quote line numbers from the page.

## How it differs from summarising and compression

Summarising plugins and compression plugins are both reasonable answers to a
full context window; they solve a different problem from paging.

| Approach | What you keep | What you lose | Cost |
|---|---|---|---|
| Summarising plugins (model writes the gist of a tool result) | Meaning | Exact lines, identifiers, stack frames | An extra model call |
| Compression plugins (shrink text, deduplicate, abbreviate) | Most meaning, less text | Byte-exactness; some tokens | CPU, and trust in the codec |
| **spillway** | **Every byte, addressed** | **Nothing — but you must ask for the range** | One file write, ~2.6 KB of context |

If what you want is "tell me what this log means", a summariser is the right
tool. If you need "what exactly failed on line 1,714, and what were the three
lines after it", paging is: the page is the original, not an interpretation of
it, and the answer is checkable after the fact. The two compose — a summary can
point at a page handle.

## Where the bytes live

```
<hermes home>/plugin-data/spillway/
├── index.db          # SQLite: id, sha256, tool, chars, lines, times, session
└── pages/
    └── <sha256>.txt  # the full result, UTF-8, byte-exact
```

Writes are atomic (`tempfile` in the same directory, `fsync`, `os.replace`); the
sha256 of the full result is the identity, so the same output twice costs one
file. The SQLite index holds metadata only — losing it degrades listing, never
the page content.

## Limits

- **Pages are local files.** They contain whatever the tool returned, including
  secrets a command may have printed. They live under the profile's
  `plugin-data/`, persist until retention or `spill_forget`, and are readable by
  anything that can read your home directory. `spill_forget` really unlinks.
- **`max_page_chars` truncates, loudly.** A result larger than 8 MiB by default
  is stored as its first N chars ending in
  `[spillway: page truncated at … the remainder was NOT stored]`; `spill_stats`
  counts those pages. This is the one non-lossless path and it is opt-out-able
  by raising the cap.
- **The replacement is capped at 4,000 chars.** If you configure a tiny budget,
  the preview shrinks (head first, then tail) and the instructions stay.
- **Grep is line-oriented** with Python `re`. Binary-ish text passes through
  unchanged, but a regex over NULs is not useful; read a range instead.
- **No index write, no page access.** If `plugin-data` is unwritable the hook
  fails open (original result, no paging) rather than interrupting the turn.
- **Only `transform_tool_result` results are paged.** Streamed terminal output,
  images and file attachments are untouched.

## Tests and validation

```bash
PYTHONPATH=/root/candidates/spillway:/root/.hermes/hermes-agent \
  /root/peripheral/.venv/bin/python -m pytest -q
hermes plugins validate /root/candidates/spillway
python scripts/demo.py
```

46 tests cover the threshold, a byte-exact 300 KB unicode/CRLF round-trip, the
5 MB replacement budget, dedupe by sha, grep line numbers and context, read
ranges and bounds, the ignore lists, fail-open on a broken store, `forget`
unlinking files, retention GC, truncation markers, concurrent writes,
NUL/surrogate text and manifest/settings/registration parity.

## Demo output

`python scripts/demo.py` writes a 300 KB Gradle-style log into a throwaway
`HERMES_HOME`, runs the real transform, then greps and reads the page. Real
output, with repeated log lines elided for width:

```
build log    : .../build.log  (300,450 bytes, 2,858 lines)
threshold    : 20,000 chars   replacement budget: 4,000

WHAT THE MODEL SEES (the exact replacement string)
[spillway] read_file produced 300,450 chars in 2,858 lines — over the 20,000-char threshold, so the full result was written to a page instead of into the context window.
page: sw_445ef68b  (byte-exact, nothing summarised, nothing dropped)
preview: first 1,200 and last 800 chars
----- head -----
08:53:20 INFO  [com.example.app] task compileDebugJavaWithJavac finished in 13ms (1 of ~3,333 work units)
...
----- tail -----
...
09:10:56 INFO  [com.example.app] task processDebugResources finished in 167ms (2855 of ~3,333 work units)
----- end preview -----
Use spill_grep(page="sw_445ef68b", pattern="ERROR") to find matching lines, or spill_read(...) for an exact range. Quote the page's line numbers in your answer. Do not re-run read_file for more output — the page already holds it.

[replacement length: 2,611 chars instead of 300,450]

STEP 1 — spill_grep(page='sw_445ef68b', pattern='ERROR')
page sw_445ef68b — 2,858 lines — 1 matching line for /ERROR/ (context 2, ignore_case=true)
   1712| 09:03:53 INFO  [com.example.app] task processDebugResources finished in 824ms ...
   1713| 09:03:53 INFO  [com.example.app] task mergeDebugAssets finished in 825ms ...
>  1714| 09:03:53 ERROR [com.example.app.MainActivity] Failed to compile src/main/kotlin/com/example/app/MainActivity.kt:184: unresolved reference: tenantContext  (exit code 1)
   1715| 09:03:53   e: file:///work/app/src/main/kotlin/com/example/app/MainActivity.kt:184:31 unresolved reference: tenantContext
   1716| 09:03:53   e: file:///.../MainActivity.kt:190:9 type mismatch: inferred type is String but TenantId was expected

STEP 2 — spill_read(page='sw_445ef68b', start_line=1711, end_line=1720)
page sw_445ef68b — read_file · 300,450 chars · lines 1,711-1,720 of 2,858
  1714| 09:03:53 ERROR [com.example.app.MainActivity] Failed to compile ...
  ...

RECOVERY CHECK
byte-identical    : True  (300,450 bytes on disk)
chars recovered   : 300,450 of 300,450 (lossless)
context avoided   : 297,839 chars
sent instead      : 2,611 chars
pages / disk      : 1 page(s), 300,450 bytes
```

## Disclosure

`spillway` reads tool results handed to it by Hermes and writes them, unmodified,
under `<hermes home>/plugin-data/spillway/`. It reads no other files, does not
touch `~/.hermes/.env` or `auth.json`, makes no network requests, no telemetry
and no model calls. It registers one hook (`transform_tool_result`) and five
tools; it does not override or monkey-patch core. Page files persist until
`retention_days` collects them or `spill_forget` deletes them; `forget` unlinks
the file before dropping the index row, and refuses to unlink anything outside
its own `pages/` directory. `max_page_chars` is the only lossy path and marks
what it truncated in the file itself.

MIT licensed. Author: kyssta.
