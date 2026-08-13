// Pure state logic for the doc 14.6 export dialog. Kept side-effect free so
// it is unit-testable in Vitest without rendering (warning visibility,
// default bundle checked, param mapping).

import type { ExportParams, ExportResult } from "../types";

/** Mandatory warning shown when "Include version history (.co/)" is checked (doc 14.6). */
export const INCLUDE_CO_WARNING_TEXT =
  "If checked, version history will be lost when opening this file in Microsoft Word or WPS";

export interface ExportDialogState {
  open: boolean;
  /** Include version history (.co/) — keeps .co/ inside the exported copy. */
  includeCo: boolean;
  /** Also export .co-bundle — companion file with the full history (default on). */
  includeBundle: boolean;
  /** Package as .coffice.zip — optional zip of document + bundle. */
  packageZip: boolean;
  exporting: boolean;
  error: string | null;
  done: boolean;
  lastResult: ExportResult | null;
}

export type ExportDialogAction =
  | { type: "open" }
  | { type: "close" }
  | { type: "toggle"; field: "includeCo" | "includeBundle" | "packageZip" }
  | { type: "begin" }
  | { type: "succeeded"; result: ExportResult }
  | { type: "failed"; error: string }
  | { type: "reset" };

export function createInitialExportState(): ExportDialogState {
  return {
    open: false,
    includeCo: false,
    includeBundle: true, // default on (ADR-005)
    packageZip: false,
    exporting: false,
    error: null,
    done: false,
    lastResult: null,
  };
}

/** The warnings the dialog must display for the current state. */
export function exportWarnings(state: ExportDialogState): string[] {
  const warnings: string[] = [];
  if (state.includeCo) {
    warnings.push(INCLUDE_CO_WARNING_TEXT);
  }
  return warnings;
}

/** Map dialog state onto the exportDoc tool arguments. */
export function buildExportParams(
  state: ExportDialogState,
  docId = "default",
  path: string | null = null,
): ExportParams {
  return {
    docId,
    includeCo: state.includeCo,
    includeBundle: state.includeBundle,
    package: state.packageZip,
    path,
  };
}

export function exportDialogReducer(
  state: ExportDialogState,
  action: ExportDialogAction,
): ExportDialogState {
  switch (action.type) {
    case "open":
      return { ...state, open: true, error: null, done: false };
    case "close":
      return { ...state, open: false };
    case "toggle":
      return { ...state, [action.field]: !state[action.field] };
    case "begin":
      return { ...state, exporting: true, error: null, done: false };
    case "succeeded":
      return {
        ...state,
        exporting: false,
        done: true,
        error: null,
        lastResult: action.result,
      };
    case "failed":
      return { ...state, exporting: false, error: action.error, done: false };
    case "reset":
      return createInitialExportState();
    default:
      return state;
  }
}
