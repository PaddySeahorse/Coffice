// Export dialog (planning doc 14.6): checkbox "Include version history (.co/)" with the
// mandatory warning text when checked, checkbox "Also export .co-bundle" (default
// on), optional .coffice.zip packaging, and [Export]/[Cancel] buttons. Wired to
// the exportDoc tool through the agent facade.

import { useEffect, useReducer } from "react";
import {
  buildExportParams,
  createInitialExportState,
  exportDialogReducer,
  exportWarnings,
} from "../logic/exportDialog";
import { getApiConfig } from "../api/config";
import type { ExportResult } from "../types";

interface ExportDialogProps {
  open: boolean;
  onClose: () => void;
  onExport: (params: ReturnType<typeof buildExportParams>) => Promise<ExportResult>;
}

export function ExportDialog({ open, onClose, onExport }: ExportDialogProps) {
  const [state, dispatch] = useReducer(
    exportDialogReducer,
    undefined,
    createInitialExportState,
  );

  useEffect(() => {
    if (open) dispatch({ type: "open" });
  }, [open]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && state.open && !state.exporting) {
        dispatch({ type: "close" });
        onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [state.open, state.exporting, onClose]);

  if (!state.open) return null;

  const warnings = exportWarnings(state);

  const exportAction = async () => {
    dispatch({ type: "begin" });
    try {
      const result = await onExport(buildExportParams(state));
      if (result.ok) {
        dispatch({ type: "succeeded", result });
      } else {
        dispatch({ type: "failed", error: result.error ?? "Export failed" });
      }
    } catch (error) {
      dispatch({
        type: "failed",
        error: error instanceof Error ? error.message : String(error),
      });
    }
  };

  return (
    <div className="modal-overlay" data-testid="export-dialog" role="dialog" aria-modal="true" aria-labelledby="export-dialog-title">
      <div className="modal">
        <h2 className="modal-title" id="export-dialog-title">Export Document</h2>
        <label className="checkbox-row" data-testid="checkbox-include-co">
          <input
            type="checkbox"
            checked={state.includeCo}
            onChange={() => dispatch({ type: "toggle", field: "includeCo" })}
          />
          <span>Include version history (.co/)</span>
        </label>
        {warnings.map((warning) => (
          <p key={warning} className="export-warning" data-testid="export-warning">
            ⚠ {warning}
          </p>
        ))}
        <label className="checkbox-row" data-testid="checkbox-include-bundle">
          <input
            type="checkbox"
            checked={state.includeBundle}
            onChange={() => dispatch({ type: "toggle", field: "includeBundle" })}
          />
          <span>Also export .co-bundle</span>
        </label>
        <label className="checkbox-row" data-testid="checkbox-package">
          <input
            type="checkbox"
            checked={state.packageZip}
            onChange={() => dispatch({ type: "toggle", field: "packageZip" })}
          />
          <span>Package as .coffice.zip</span>
        </label>

        {state.error && (
          <p className="export-error" data-testid="export-error">
            {state.error}
          </p>
        )}
        {state.done && state.lastResult && (
          <div className="export-result" data-testid="export-result">
            <p>Export successful: {state.lastResult.path}</p>
            {state.lastResult.path && (
              <p>
                <a
                  href={`${getApiConfig().agentBaseUrl}download?path=${encodeURIComponent(state.lastResult.path)}`}
                  className="btn btn--primary"
                  data-testid="link-download"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Click to download
                </a>
              </p>
            )}
            {state.lastResult.bundlePath && <p>Version History: {state.lastResult.bundlePath}</p>}
            {state.lastResult.packagePath && <p>Package: {state.lastResult.packagePath}</p>}
          </div>
        )}

        <div className="modal-actions">
          <button
            type="button"
            className="btn btn--primary"
            data-testid="btn-export"
            disabled={state.exporting}
            onClick={() => void exportAction()}
          >
            {state.exporting ? "Exporting…" : "Export"}
          </button>
          <button
            type="button"
            className="btn"
            data-testid="btn-cancel"
            disabled={state.exporting}
            onClick={() => {
              dispatch({ type: "close" });
              onClose();
            }}
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
