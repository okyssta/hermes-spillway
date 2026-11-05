---
name: spillway
description: "Work with paged tool results: grep before reading, quote line numbers."
version: 1.0.0
author: kyssta
license: MIT
metadata:
  hermes:
    tags: [context, tools, paging, grep, logs]
---

# Paged tool results

When a tool result begins with `[spillway]`, the full output was too large to
put in the conversation. It is **not** truncated and it is **not** summarised:
every byte is on a page, and the replacement you got is a preview plus a handle.

```
[spillway] terminal produced 412,904 chars in 3,112 lines ...
page: sw_ab12cd34  (byte-exact, nothing summarised, nothing dropped)
```

## The three rules

1. **Never re-run the tool to "get more output".** The command may be expensive,
   destructive, or already gone (a build log, a deleted file). The page holds the
   exact result — re-running buys nothing and can change the answer.
2. **Grep before you read.** `spill_grep(page="sw_…", pattern="ERROR|Traceback")`
   answers "where is it?" for a fraction of the context that reading ranges costs.
   Only `spill_read` the window around a hit.
3. **Quote line numbers from the page.** They are the tool's own line numbers.
   When you report a finding, write `build.log line 1,204` (or the page handle
   plus line), so a human can check it with the same call.

## Calls

| Need | Call |
|---|---|
| Find the error / a key / a test name | `spill_grep(page="sw_…", pattern="ERROR", context=2)` |
| Read an exact window | `spill_read(page="sw_…", start_line=1180, end_line=1210)` |
| Recover a lost handle | `spill_list()` |
| Know what paging saved | `spill_stats()` |
| Delete the bytes (privacy) | `spill_forget(page="sw_…")` or `spill_forget(all=true)` |

`spill_read` is 1-based and inclusive, shows the page's total line count, and
tells you where it stopped if `max_chars` cut the window short — continue from
the line it names. Lines longer than 2,000 chars are cut with an explicit
marker; grep the line or read a neighbouring range if you need the rest.

## Choosing a pattern

- Logs: `ERROR|FATAL|Traceback|exit code [1-9]`, then widen with
  `WARN|failed|timeout` if the first pass is empty.
- Tests: `FAILED|assert|Error:` then the test id.
- JSON/prose: a distinctive key or identifier from the preview, not `{` or `.`.
- Case-insensitive by default. Pass `ignore_case=false` for identifiers that
  differ by case (`UserID` vs `userid`).
- The regex is Python syntax and is validated: a bad pattern returns a message,
  not a crash. Escape `(`, `[`, `.`, `*` when you mean them literally.

## When not to use it

- The result is small and complete in the conversation — just read it.
- You need the whole thing verbatim (e.g. to hand to another tool): read it in
  ranges and join them, or ask the user; do not guess from the preview.
- The page is gone (`No page …`): it was forgotten or aged out by retention. Say
  so rather than re-running something destructive.

## Privacy

Pages are local files under the profile's `plugin-data/spillway/`. When the user
asks to forget captured output, `spill_forget` really unlinks the files; check
`spill_list()` first if you need to name what is being deleted.
