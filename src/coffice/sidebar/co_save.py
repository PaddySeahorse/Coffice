"""Save-time preservation of ``.co/`` version history (sidebar save pipeline).

LibreOffice's ``storeToURL`` (and ``.uno:Save``) rewrite the whole Office
package, silently dropping the ``.co/`` directory in which the ``co`` CLI
keeps version history (ADR-001). The sidebar ``save`` command therefore wraps
the plain save in a pipeline:

    backup = backup_history(path)      # .co/ -> temp .co-bundle (or None)
    storeToURL(path)                   # the actual save, .co/ wiped by LO
    restore_history(path, backup)      # .co/ put back into the saved file
    commit_snapshot(path)              # co commit the new state (CLI only)

This module is pure Python (no ``uno`` import, stdlib only) so it ships
verbatim inside the .oxt (copied by ``extension/build.sh``) and is
unit-testable without LibreOffice. It mirrors the binary discovery and
zip-level ``.co/`` handling of :mod:`coffice.versioning` but depends on
nothing but the standard library, because the extension package only carries
``contract``/``doc_commands``/``co_save``.

Binary discovery order (matches ``coffice.versioning.co_client``):
``CO_BIN`` -> ``COFFICE_CO_BIN`` -> ``co`` on ``PATH`` -> ``~/.local/bin/co``
-> ``/usr/local/bin/co``. When no binary is found the export/import steps
fall back to plain ZIP moves so the history survives the save; only the final
``commit`` requires the CLI.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

#: zip-internal directory the co CLI uses to store version history.
CO_DIR_PREFIX = ".co/"

#: stdout line ``co commit`` prints on success (mirrors co_client._COMMIT_RE).
_COMMIT_RE = re.compile(r"^Committed ([0-9a-fA-F]{7,64})")

#: Author recorded for a human save from the sidebar (audit field 8.4).
DEFAULT_AUTHOR = "human"

#: Default commit message for a sidebar save.
DEFAULT_MESSAGE = "Saved from Coffice sidebar"


class CoSaveError(Exception):
    """A co-backed step of the save pipeline failed."""


def _co_names(path: Path) -> list[str]:
    """Return the zip-internal names of ``path`` that live under ``.co/``."""
    with zipfile.ZipFile(path) as zf:
        return [name for name in zf.namelist() if name.startswith(CO_DIR_PREFIX)]


def _run(
    binary: str, args: list[str], env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [binary, *args], capture_output=True, text=True, env=env, check=False
        )
    except OSError as exc:
        raise CoSaveError(f"could not run co binary {binary!r}: {exc}") from exc


def find_co_binary() -> str | None:
    """Locate the co binary (env CO_BIN/COFFICE_CO_BIN, PATH, common dirs)."""
    for env_var in ("CO_BIN", "COFFICE_CO_BIN"):
        candidate = os.environ.get(env_var)
        if candidate and os.path.isfile(candidate):
            return candidate
    for name in ("co", "co.exe"):
        found = shutil.which(name)
        if found:
            return found
    common_paths = [Path.home() / ".local/bin/co", Path("/usr/local/bin/co")]
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            common_paths.extend(
                [
                    Path(local_app_data) / "co" / "co.exe",
                    Path(local_app_data) / "bin" / "co.exe",
                ]
            )
        common_paths.extend(
            [
                Path.home() / ".local/bin/co.exe",
                Path.home() / "AppData/Local/co/co.exe",
            ]
        )
    for common in common_paths:
        if common.is_file():
            return str(common)
    return None


def backup_history(path: str, bin_path: str | None = None) -> str | None:
    """Export the document's ``.co/`` history to a temp ``.co-bundle``.

    Returns the bundle path, or ``None`` when the document carries no history
    to preserve. ``bin_path`` overrides binary discovery (used by tests).
    """
    src = Path(path)
    if not _co_names(src):
        return None
    fd, tmp = tempfile.mkstemp(prefix=f"{src.name}.co-save-", suffix=".co-bundle")
    os.close(fd)
    bundle = Path(tmp)
    binary = bin_path or find_co_binary()
    if binary is not None:
        proc = _run(binary, ["export", str(src), "--output", str(bundle)])
        if proc.returncode != 0:
            bundle.unlink(missing_ok=True)
            raise CoSaveError(
                f"co export failed: {proc.stderr.strip() or proc.stdout.strip()}"
            )
        return str(bundle)
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(
        bundle, "w", zipfile.ZIP_DEFLATED
    ) as zout:
        for name in _co_names(src):
            zout.writestr(name, zin.read(name))
    return str(bundle)


def restore_history(path: str, bundle: str, bin_path: str | None = None) -> None:
    """Restore the ``.co/`` history from ``bundle`` into ``path`` after save.

    Uses ``co import --force`` when the CLI is available (LibreOffice
    rewrote the file, so the bundle's recorded source SHA-256 no longer
    matches and ``--force`` is required), otherwise injects the bundle's
    ``.co/`` entries into the document ZIP directly.
    """
    doc_path = Path(path)
    binary = bin_path or find_co_binary()
    if binary is not None:
        proc = _run(binary, ["import", str(doc_path), str(bundle), "--force"])
        if proc.returncode != 0:
            raise CoSaveError(
                f"co import failed: {proc.stderr.strip() or proc.stdout.strip()}"
            )
        return
    with zipfile.ZipFile(bundle) as zin:
        co_data = {
            name: zin.read(name)
            for name in zin.namelist()
            if name.startswith(CO_DIR_PREFIX)
        }
    if not co_data:
        raise CoSaveError(f"bundle {bundle!r} contains no .co/ history to import")
    tmp = doc_path.with_name(doc_path.name + ".co-import.tmp")
    try:
        with zipfile.ZipFile(doc_path) as zin, zipfile.ZipFile(
            tmp, "w", zipfile.ZIP_DEFLATED
        ) as zout:
            for info in zin.infolist():
                zout.writestr(info, zin.read(info.filename))
            existing = set(zin.namelist())
            for name, data in co_data.items():
                if name not in existing:
                    zout.writestr(name, data)
        shutil.move(str(tmp), doc_path)
    finally:
        tmp.unlink(missing_ok=True)


def commit_snapshot(
    path: str, bin_path: str | None = None, message: str | None = None
) -> str | None:
    """Record a new co commit of the saved document (CLI only).

    Returns the commit hash, or ``None`` when the co CLI is unavailable (the
    history is preserved by :func:`backup_history`/:func:`restore_history`
    but the current state cannot be committed).
    """
    binary = bin_path or find_co_binary()
    if binary is None:
        return None
    env = dict(os.environ)
    env.setdefault("CO_AUTHOR_NAME", DEFAULT_AUTHOR)
    proc = _run(
        binary,
        ["commit", "-m", message or DEFAULT_MESSAGE, str(path)],
        env=env,
    )
    if proc.returncode != 0:
        raise CoSaveError(
            f"co commit failed: {proc.stderr.strip() or proc.stdout.strip()}"
        )
    match = _COMMIT_RE.search(proc.stdout or "")
    return match.group(1) if match else None
