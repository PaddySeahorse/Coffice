#!/usr/bin/env bash
# Coffice Route 1 end-to-end smoke test (planning doc 10.2).
#
# Proves the closed loop works against the *real* stack:
#
#   1. starts LibreOffice headless (UNO socket),
#   2. creates a sample report.docx,
#   3. runs a scripted agent round over the real MCP server (the same
#      in-process tool execution path an agent session uses):
#      getOutline -> insertText -> snapshot -> insertText -> rollback,
#      then verifies the document content was restored,
#   4. exportDoc PURE + .co-bundle and verifies the pure export carries no
#      .co/ history while the bundle round-trips the history back via co.
#
# The script FAILS LOUDLY (non-zero exit with a message) on any step that
# fails, and SKIPS GRACEFULLY (exit 0, "SKIPPED: <reason>") when LibreOffice/
# PyUNO or the co CLI are not installed, so it is safe to run in CI where the
# external stack is absent.
#
# Requirements (see docs/route1.md):
#   - LibreOffice + python3-uno  (apt install libreoffice python3-uno)
#   - the co CLI                  (bash scripts/install_co.sh, or set CO_BIN)
#   - Python deps                 (make setup)
#
# Usage:
#   bash scripts/smoke_route1.sh
#   CO_BIN=/path/to/co bash scripts/smoke_route1.sh
#
# Environment:
#   COFFICE_LO_PORT   UNO socket port for the headless LibreOffice instance
#                     (default: 2002)
#   CO_BIN            co binary path (optional; falls back to $PATH)
#   PYTHON            python interpreter to use (default: .venv/bin/python,
#                     then venv/bin/python, then python3)
#
# Exit codes:
#   0  PASS, or SKIPPED (prerequisites missing)
#   1  any smoke step failed

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# ---------------------------------------------------------------------------
# interpreter selection (venv first, then system python)
# ---------------------------------------------------------------------------
if [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
elif [ -x "venv/bin/python" ]; then
    PY="venv/bin/python"
else
    PY="${PYTHON:-python3}"
fi

echo "==> smoke_route1: python=$PY root=$ROOT"

# ---------------------------------------------------------------------------
# prerequisite probe -- SKIPPED (exit 0) when LO/co are not installed
# ---------------------------------------------------------------------------
probe() {
    "$PY" - "$ROOT" <<'PYEOF'
import sys

root = sys.argv[1]
sys.path.insert(0, root + "/src")

missing = []
try:
    from coffice.core.lo_manager import lo_available

    if not lo_available():
        missing.append("LibreOffice/PyUNO (apt install libreoffice python3-uno)")
except Exception as exc:  # package/deps not installed -> treat as missing LO
    missing.append(f"Python deps not importable ({type(exc).__name__}: {exc}); run: make setup")

try:
    from coffice.versioning import co_available

    if not co_available():
        missing.append("co CLI (bash scripts/install_co.sh, or set CO_BIN)")
except Exception as exc:
    missing.append(f"co availability probe failed ({type(exc).__name__}: {exc})")

if missing:
    print("SKIP:" + "; ".join(missing))
else:
    print("OK")
PYEOF
}

PROBE_RESULT="$(probe)"
case "$PROBE_RESULT" in
    OK)
        echo "==> prerequisites OK (LibreOffice + co CLI found)"
        ;;
    SKIP:*)
        REASON="${PROBE_RESULT#SKIP:}"
        echo "SKIPPED: $REASON"
        echo "smoke_route1: no end-to-end run. Install the prerequisites and re-run: make smoke"
        exit 0
        ;;
    *)
        echo "ERROR: prerequisite probe failed unexpectedly:" >&2
        echo "$PROBE_RESULT" >&2
        exit 1
        ;;
esac

# ---------------------------------------------------------------------------
# run the end-to-end smoke driver (Python, in-process MCP server + real LO + co)
# ---------------------------------------------------------------------------
echo "==> running Route 1 closed-loop smoke driver"

set +e
"$PY" - "$ROOT" <<'PYEOF'
import asyncio
import os
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(sys.argv[1])
sys.path.insert(0, str(ROOT / "src"))


def step(message: str) -> None:
    print(f"[smoke] {message}", flush=True)


def call(server, name, **arguments):
    """Invoke one MCP tool in-process; returns its structured dict result."""
    args = dict(arguments)
    args.setdefault("docId", "default")
    result = asyncio.run(server.call_tool(name, args))
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], dict):
        return result[1]
    if isinstance(result, dict):
        return result
    raise AssertionError(f"unexpected result from {name}: {result!r}")


def docx_text(path: Path) -> str:
    """Crude text extraction from word/document.xml (for content checks)."""
    with zipfile.ZipFile(path) as zf:
        xml = zf.read("word/document.xml").decode("utf-8", "replace")
    stripped = re.sub(r"<[^>]+>", "", xml)
    return re.sub(r"\s+", " ", stripped)


def main() -> None:
    from coffice.core.document import Document
    from coffice.core.lo_manager import get_manager
    from coffice.mcp import create_server
    from coffice.versioning import CoClient, resolve_bin_path

    workdir = Path(tempfile.mkdtemp(prefix="coffice-smoke-"))
    doc_path = workdir / "report.docx"

    # ---- (1) start LibreOffice headless ----------------------------------
    step("starting LibreOffice headless (UNO socket)")
    manager = get_manager()
    manager.ensure_running()
    desktop = manager.get_desktop()
    step("  LibreOffice headless instance up")

    # ---- (2) create a sample report.docx ----------------------------------
    step(f"creating sample report.docx ({doc_path})")
    doc = Document.create(desktop)
    doc.insert_text(0, "Coffice Smoke Report", style="Heading 1")
    doc.insert_text("end", "\nIntro paragraph.\n")
    doc.insert_text("end", "Findings", style="Heading 2")
    doc.insert_text("end", "\nBody of the findings.\n")
    doc.save(str(doc_path))
    doc.close()

    # initialize the co repository and wire the real MCP server on the doc
    co = CoClient(bin_path=resolve_bin_path())
    co.init(str(doc_path))
    step("  co repository initialized inside report.docx")
    os.environ["COFFICE_DOC_PATH"] = str(doc_path)
    server = create_server()

    # ---- (3) scripted agent round over the MCP server ----------------------
    step("agent round: getOutline")
    outline = call(server, "getOutline")
    headings = outline.get("outline") or []
    assert headings, f"getOutline returned no headings: {outline!r}"
    assert any(h.get("level") == 1 for h in headings), "expected a Heading 1 in outline"
    step(f"  outline OK ({len(headings)} heading(s))")

    step("agent round: insertText (low risk, runs immediately)")
    inserted = call(server, "insertText", pos="end", text="\nSmoke-injected paragraph.\n")
    assert inserted.get("ok"), f"insertText failed: {inserted!r}"
    step("  insertText OK")

    step("agent round: snapshot (manual checkpoint via co)")
    snap = call(server, "snapshot", label="smoke checkpoint")
    assert snap.get("ok") and snap.get("hash"), f"snapshot failed: {snap!r}"
    snap_hash = snap["hash"]
    step(f"  snapshot OK ({snap_hash})")

    step("agent round: insertText (edit that rollback must undo)")
    second = call(server, "insertText", pos="end", text="\nDO NOT KEEP THIS LINE.\n")
    assert second.get("ok"), f"second insertText failed: {second!r}"
    step("  second insertText OK")

    step(f"agent round: rollback -> {snap_hash}")
    rolled = call(server, "rollback", hash=snap_hash)
    assert rolled.get("ok"), f"rollback failed: {rolled!r}"
    step("  rollback OK")

    step("verify content restored on disk after rollback")
    text = docx_text(doc_path)
    assert "Smoke-injected paragraph" in text, "pre-snapshot content missing after rollback"
    assert "DO NOT KEEP THIS LINE" not in text, "post-snapshot content survived rollback"
    step("  content restored OK")

    # ---- (4) exportDoc PURE + .co-bundle -----------------------------------
    step("exportDoc (PURE, with .co-bundle companion)")
    export = call(
        server,
        "exportDoc",
        includeCo=False,
        includeBundle=True,
        path=str(workdir / "export.docx"),
    )
    assert export.get("ok"), f"exportDoc failed: {export!r}"
    pure = Path(export["path"])
    bundle = Path(export["bundlePath"])
    assert pure.is_file(), f"pure export missing: {pure}"
    assert bundle.is_file(), f"bundle missing: {bundle}"

    with zipfile.ZipFile(pure) as zf:
        names = zf.namelist()
    assert not any(n.startswith(".co/") for n in names), "PURE export must not contain .co/"
    step("  pure export has no .co/ (verified, Word/WPS-compatible)")

    step("verify history round-trips from the .co-bundle")
    restored = workdir / "restored.docx"
    shutil.copy2(pure, restored)  # a fresh copy with no history
    co.import_bundle(str(restored), str(bundle), force=True)
    commits = co.log(str(restored))
    assert commits, "no history found after importing the .co-bundle"
    step(f"  bundle history OK ({len(commits)} commit(s) restored via co import)")

    print("\nSMOKE PASS: Route 1 closed loop verified "
          "(LO headless -> MCP tools -> co history -> rollback -> PURE export + bundle)")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - the bash wrapper prints the status
        print(f"\nSMOKE FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
PYEOF
SMOKE_STATUS=$?
set -e

if [ "$SMOKE_STATUS" -ne 0 ]; then
    echo "FAILED: smoke_route1 exited $SMOKE_STATUS (see traceback above)" >&2
    exit 1
fi

echo "==> smoke_route1 PASS"
