#!/usr/bin/env python3
"""spillway demo — a real 300 KB build log, paged and navigated, entirely locally.

Run it:

    python scripts/demo.py

It builds a throwaway HERMES_HOME under a temp directory, writes a realistic
Gradle-style build log, feeds it to the plugin exactly as the
``transform_tool_result`` hook would see a ``read_file`` result, then prints the
replacement the model gets and the two follow-up calls (grep, read) that recover
the part that matters. Nothing touches the real profile and no network is used.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

SCRATCH = Path(tempfile.mkdtemp(prefix="spillway-demo-"))
os.environ["HERMES_HOME"] = str(SCRATCH / "hermes-home")
(SCRATCH / "hermes-home").mkdir(parents=True)
HERE = Path(__file__).resolve()
for parent in HERE.parents:
    if (parent / "spillway" / "__init__.py").is_file():
        sys.path.insert(0, str(parent))
        break

from spillway import current_settings, store, tools, transform  # noqa: E402
from spillway.settings import Settings  # noqa: E402

TASKS = [
    "compileDebugKotlin", "compileDebugJavaWithJavac", "processDebugResources",
    "mergeDebugAssets", "transformDebugClassesWithAsm", "dexBuilderDebug",
    "packageDebug", "lintVitalAnalyzeDebug", "testDebugUnitTest",
]


def build_log(target_bytes: int = 300_000) -> str:
    """A plausible build log: timestamps, task lines, warnings, one real failure."""
    lines: list[str] = []
    start = 1_760_000_000.0
    counter = 0
    total = 0
    while total < target_bytes:
        counter += 1
        stamp = time.strftime("%H:%M:%S", time.gmtime(start + counter * 0.37))
        task = TASKS[counter % len(TASKS)]
        kind = counter % 17
        if kind == 0:
            line = f"{stamp} > Task :app:{task} UP-TO-DATE"
        elif kind == 3:
            line = (
                f"{stamp} WARN  [org.gradle.internal.execution] {task} skipped: input unchanged "
                f"(snapshot cache hit, key={counter:08x})"
            )
        elif kind == 9:
            line = (
                f"{stamp} DEBUG [com.example.build] resolving configuration variant debugRuntimeClasspath "
                f"artifact {counter}.jar from cache ~/.gradle/caches/modules-2/files-2.1"
            )
        else:
            line = (
                f"{stamp} INFO  [com.example.app] task {task} finished in {counter % 900 + 12}ms "
                f"({counter} of ~{target_bytes // 90:,} work units)"
            )
        lines.append(line)
        total += len(line) + 1

    # The interesting part sits in the middle, exactly where Hermes' own
    # truncation would lose it.
    at = len(lines) * 3 // 5
    stamp = time.strftime("%H:%M:%S", time.gmtime(start + at * 0.37))
    lines[at:at] = [
        f"{stamp} ERROR [com.example.app.MainActivity] Failed to compile "
        "src/main/kotlin/com/example/app/MainActivity.kt:184: unresolved reference: "
        "tenantContext  (exit code 1)",
        f"{stamp}   e: file:///work/app/src/main/kotlin/com/example/app/MainActivity.kt:184:31 "
        "unresolved reference: tenantContext",
        f"{stamp}   e: file:///work/app/src/main/kotlin/com/example/app/MainActivity.kt:190:9 "
        "type mismatch: inferred type is String but TenantId was expected",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    settings: Settings = current_settings()
    log = build_log(300_000)
    log_path = SCRATCH / "build.log"
    log_path.write_text(log, encoding="utf-8", newline="")

    print("=" * 78)
    print("spillway demo")
    print("=" * 78)
    print(f"scratch home : {os.environ['HERMES_HOME']}")
    print(f"build log    : {log_path}  ({len(log.encode('utf-8')):,} bytes, {store.count_lines(log):,} lines)")
    print(f"threshold    : {settings.min_chars:,} chars   replacement budget: {settings.max_result_chars:,}")
    print()

    replacement = transform.handle(
        tool_name="read_file",
        args={"path": str(log_path)},
        result=log,
        session_id="demo-session",
        tool_call_id="call_demo_1",
    )
    if replacement is None:
        print("ERROR: the hook returned None — the log was not paged.")
        return 1

    print("-" * 78)
    print("WHAT THE MODEL SEES (the exact replacement string)")
    print("-" * 78)
    print(replacement)
    print()
    print(f"[replacement length: {len(replacement):,} chars instead of {len(log):,}]")
    print()

    record = store.find(replacement.split("page: ", 1)[1].split()[0])
    assert record is not None, "page not indexed"
    page = record["id"]

    print("-" * 78)
    print(f"STEP 1 — spill_grep(page={page!r}, pattern='ERROR')")
    print("-" * 78)
    grep_out = tools.spill_grep({"page": page, "pattern": "ERROR", "context": 2})
    print(grep_out)
    print()

    match_line = next(i for i, line in enumerate(log.split("\n"), start=1) if "ERROR" in line)
    start = max(1, match_line - 3)
    end = match_line + 6
    print("-" * 78)
    print(f"STEP 2 — spill_read(page={page!r}, start_line={start}, end_line={end})")
    print("-" * 78)
    read_out = tools.spill_read({"page": page, "start_line": start, "end_line": end})
    print(read_out)
    print()

    on_disk = Path(record["path"]).read_bytes()
    exact = on_disk == log.encode("utf-8")
    stats = store.stats()
    print("-" * 78)
    print("RECOVERY CHECK")
    print("-" * 78)
    print(f"page file         : {record['path']}")
    print(f"byte-identical    : {exact}  ({len(on_disk):,} bytes on disk)")
    print(f"chars recovered   : {len(log):,} of {len(log):,} (lossless)")
    print(f"context avoided   : {len(log) - len(replacement):,} chars")
    print(f"sent instead      : {len(replacement):,} chars")
    print(f"pages / disk      : {stats['pages']:,} page(s), {stats['disk_bytes']:,} bytes")
    print(f"chars saved (all) : {stats['chars_saved']:,}")
    print()
    print(f"Scratch directory {SCRATCH} is removed when this script exits.")
    return 0 if exact else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        shutil.rmtree(SCRATCH, ignore_errors=True)
