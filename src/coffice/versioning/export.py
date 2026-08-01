"""Document export/import flow honoring ADR-005 (planning doc ch. 14 / 14.6).

ADR-005 verified constraint
---------------------------
Word and WPS wipe the ``.co/`` directory embedded inside the Office ZIP on
save, so version history must never live *only* inside the file. This module
implements the export/import flow the ``exportDoc``/``importBundle`` MCP
tools (doc 14.3) and the sidebar export dialog (doc 14.6) build on:

- **PURE export** (default): the exported document copy carries no ``.co/``
  history -- 100% compatible with any editor. History is preserved in a
  ``<file>.co-bundle`` companion instead.
- **include-co export**: the copy keeps its embedded ``.co/``; the flow
  returns the ADR-005 warning so the UI can surface a dialog.
- **bundle companion**: ``<file>.co-bundle`` written via ``co export`` when
  the CLI is available, otherwise by zipping the document's own ``.co/``
  directory entries.
- **optional packaging**: zip document + bundle into ``<file>.coffice.zip``.
- **import**: restore history from a bundle onto a document (doc 14.6
  scenario B) via ``co import`` (with an automatic ``--force`` retry when the
  bundle's recorded source SHA no longer matches -- e.g. after Word/WPS
  rewrote the file), otherwise by injecting the bundle's ``.co/`` entries
  into the ZIP. With the CLI the current document state is appended as a new
  commit; without it the result carries a warning instead.
"""

from __future__ import annotations

import logging
import os
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

from coffice.versioning.co_client import (
    INCLUDE_CO_WARNING,
    CoClient,
    CoError,
    CoNotAvailableError,
)

logger = logging.getLogger(__name__)

#: zip-internal directory the co CLI uses to store version history.
CO_DIR_PREFIX = ".co/"

#: warning returned by the no-CLI import fallback: history is restored but
#: the current state cannot be appended as a new commit.
NO_CLI_IMPORT_WARNING = (
    "history restored without co CLI; the current document state was not "
    "appended as a new commit (install co via scripts/install_co.sh for "
    "full import semantics)"
)


@dataclass(frozen=True)
class ExportFlowResult:
    """Result of an :func:`export_document` run (ADR-005)."""

    out_path: str  #: path of the exported document copy
    bundle_path: str | None  #: path of the written .co-bundle, or None
    package_path: str | None  #: path of the .coffice.zip, or None
    include_co: bool  #: whether the exported copy retains .co/
    warnings: tuple[str, ...] = ()  #: non-empty when include_co (ADR-005)


@dataclass(frozen=True)
class ImportFlowResult:
    """Result of an :func:`import_bundle` run (doc 14.6 scenario B)."""

    bundle_path: str
    imported_commits: int  #: number of commits restored from the bundle
    commit_hash: str | None  #: hash of the appended current-state commit
    #: non-empty when the import degraded: a forced (SHA-mismatch) retry ran,
    #: or no co CLI was available (history restored but no commit appended).
    warning: str = ""


def _co_entries(names: list[str]) -> list[str]:
    """Return the zip-internal names that live under the ``.co/`` directory."""
    return [name for name in names if name.startswith(CO_DIR_PREFIX)]


def strip_co_from_zip(src: Path, dst: Path) -> None:
    """Copy ``src`` ZIP to ``dst`` dropping every ``.co/`` entry (PURE export).

    Fallback used when the co CLI is unavailable: produces a copy that is
    byte-compatible with any editor because the version history directory is
    removed. The stripped copy is written to a temp file and atomically moved
    over ``dst`` (mirroring :func:`inject_co_entries`), so ``dst == src`` is
    safe -- the source is never truncated mid-copy.
    """
    tmp = dst.with_name(dst.name + ".co-export.tmp")
    try:
        with zipfile.ZipFile(src) as zin, zipfile.ZipFile(
            tmp, "w", zipfile.ZIP_DEFLATED
        ) as zout:
            for info in zin.infolist():
                if info.filename.startswith(CO_DIR_PREFIX):
                    continue
                zout.writestr(info, zin.read(info.filename))
        shutil.move(str(tmp), dst)
    finally:
        tmp.unlink(missing_ok=True)


def write_bundle_from_co_dir(src: Path, bundle_path: Path) -> bool:
    """Write a ``.co-bundle`` as a zip of ``src``'s ``.co/`` directory entries.

    Fallback used when the co CLI is unavailable. Returns False (and writes
    nothing) when the document carries no history to bundle.
    """
    with zipfile.ZipFile(src) as zin:
        co_data = {
            name: zin.read(name) for name in _co_entries(zin.namelist())
        }
    if not co_data:
        return False
    with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in co_data.items():
            zout.writestr(name, data)
    return True


def inject_co_entries(doc_path: Path, bundle_path: Path) -> int:
    """Fallback import: write the bundle's ``.co/`` entries into the doc ZIP.

    Used when the co CLI is unavailable. Preserves every existing ZIP entry
    and adds the history entries the bundle carries. Returns the number of
    ``.co/`` entries injected (a bundle without history raises
    :class:`ValueError` instead).
    """
    with zipfile.ZipFile(bundle_path) as zin:
        co_data = {
            name: zin.read(name)
            for name in _co_entries(zin.namelist())
        }
    if not co_data:
        raise ValueError(
            f"bundle {bundle_path!r} contains no .co/ history to import"
        )
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
    return len(co_data)


def export_document(
    path: str,
    *,
    include_co: bool = False,
    include_bundle: bool = True,
    out_path: str | None = None,
    package: bool = False,
    co_client: CoClient | None = None,
) -> ExportFlowResult:
    """Export a document honoring ADR-005.

    Args:
        path: the source document (a .docx/.xlsx/.pptx carrying ``.co/``).
        include_co: when True the exported copy keeps its embedded history
            and the ADR-005 warning is returned; when False (default) the
            copy is PURE (no ``.co/``) and fully editor-compatible.
        include_bundle: write a ``<out>.co-bundle`` companion carrying the
            full history. Defaults to True -- per ADR-005 history must not
            live only inside the file.
        out_path: where to write the exported document copy; defaults to
            ``path`` itself (the source is then overwritten per co export
            semantics).
        package: additionally zip document + bundle into
            ``<out>.coffice.zip``.
        co_client: optional co CLI wrapper; when available ``co export`` does
            the work, otherwise the module's own ZIP implementation is used.

    Returns an :class:`ExportFlowResult`; raises :class:`FileNotFoundError`
    when the source is missing and :class:`coffice.versioning.CoError` when
    the co CLI fails and no fallback applies.
    """
    src = Path(path)
    if not src.is_file():
        raise FileNotFoundError(f"document to export not found: {src}")
    out = Path(out_path) if out_path else src
    warnings: list[str] = []
    bundle_path: str | None = None

    co_did_export = False
    if co_client is not None and include_bundle:
        bundle_path = f"{out}.co-bundle"
        try:
            result = co_client.export(
                str(src),
                include_co=include_co,
                out_path=str(out),
                bundle_path=bundle_path,
            )
            if result.warning:
                warnings.append(result.warning)
            co_did_export = True
        except CoError as exc:
            logger.warning(
                "co export unavailable (%s); falling back to own ZIP export",
                exc,
            )
            bundle_path = None

    if not co_did_export:
        # Own implementation: no co CLI (or bundle not requested).
        # Capture the source's .co/ history first (ADR-005): the bundle
        # companion must be written even when the export targets the source
        # file itself, so history never lives only in the (rewritten) file.
        if include_bundle:
            candidate = Path(f"{out}.co-bundle")
            if write_bundle_from_co_dir(src, candidate):
                bundle_path = str(candidate)

        same_file = os.path.abspath(src) == os.path.abspath(out)
        if include_co:
            if not same_file:
                shutil.copy2(src, out)
            warnings.append(INCLUDE_CO_WARNING)
        elif not same_file:
            # PURE export to a distinct copy: strip .co/ (temp + atomic move).
            strip_co_from_zip(src, out)
        elif include_bundle:
            # PURE export where out is the source itself, without co CLI: the
            # bundle companion written above carries the full history (ADR-005),
            # so the source is left untouched rather than stripped in place,
            # which would destroy the only in-file copy if anything went wrong.
            pass
        else:
            # PURE same-file export with no bundle: history would live only
            # inside the file (ADR-005), so fail closed instead of silently
            # leaving a Word/WPS-wipeable .co/ behind.
            raise CoError(
                "PURE export to the same file without a .co-bundle companion "
                "would leave version history only inside the file (ADR-005). "
                "Install co via scripts/install_co.sh, request the bundle "
                "(includeBundle), or export to a different path."
            )

    package_path: str | None = None
    if package:
        package_path = f"{out}.coffice.zip"
        with zipfile.ZipFile(package_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(out, out.name)
            if bundle_path and Path(bundle_path).is_file():
                zf.write(bundle_path, Path(bundle_path).name)

    return ExportFlowResult(
        out_path=str(out),
        bundle_path=bundle_path,
        package_path=package_path,
        include_co=include_co,
        warnings=tuple(warnings),
    )


def import_bundle(
    doc_path: str,
    bundle_path: str,
    *,
    co_client: CoClient | None = None,
    author: str = "AI-Writer",
    human_operator: str | None = None,
) -> ImportFlowResult:
    """Restore history from a ``.co-bundle`` onto a document (doc 14.6 B).

    The bundle's commits are injected into the document's ``.co/`` history,
    then the current document state is appended as a new commit so the
    restored history continues from where the document actually is.

    When the co CLI is available, ``co import`` runs (retried with
    ``--force`` if the bundle's recorded source SHA no longer matches -- the
    Word/WPS rewrite case from ADR-005) and the current state is appended as
    a new commit; otherwise (no client, or the CLI is not installed) the
    bundle's ``.co/`` entries are injected directly into the ZIP. The no-CLI
    fallback cannot append a commit, so the result carries a warning and
    reports the number of injected ``.co/`` entries instead of a commit hash.
    """
    doc = Path(doc_path)
    bundle = Path(bundle_path)
    if not doc.is_file():
        raise FileNotFoundError(f"document to import into not found: {doc}")
    if not bundle.is_file():
        raise FileNotFoundError(f"bundle not found: {bundle}")

    warning = ""
    before = 0
    if co_client is not None:
        try:
            before = len(co_client.log(str(doc)))
        except CoError:
            before = 0

    injected = 0
    if co_client is not None:
        try:
            result = co_client.import_bundle(str(doc), str(bundle), force=False)
            warning = result.warning
        except CoNotAvailableError:
            logger.info("co CLI unavailable; importing bundle via ZIP injection")
            injected = inject_co_entries(doc, bundle)
            warning = NO_CLI_IMPORT_WARNING
        except CoError as exc:
            logger.info(
                "co import refused without --force (%s); retrying forced "
                "(Word/WPS rewrite case, ADR-005)",
                exc,
            )
            result = co_client.import_bundle(str(doc), str(bundle), force=True)
            warning = (
                result.warning
                or "history imported with --force: the bundle's source file "
                "SHA no longer matched (Word/WPS may have rewritten it)"
            )
    else:
        injected = inject_co_entries(doc, bundle)
        warning = NO_CLI_IMPORT_WARNING

    if co_client is not None and not injected:
        after = len(co_client.log(str(doc)))
        imported = max(0, after - before)
    else:
        imported = injected

    commit_hash: str | None = None
    if co_client is not None and not injected:
        commit_hash = co_client.commit(
            str(doc),
            f"import: {bundle.name}",
            author=author,
            human_operator=human_operator,
        )

    return ImportFlowResult(
        bundle_path=str(bundle),
        imported_commits=imported,
        commit_hash=commit_hash,
        warning=warning,
    )


__all__ = [
    "CO_DIR_PREFIX",
    "ExportFlowResult",
    "ImportFlowResult",
    "export_document",
    "import_bundle",
    "inject_co_entries",
    "strip_co_from_zip",
    "write_bundle_from_co_dir",
]
