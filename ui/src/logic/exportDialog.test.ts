// Unit tests for the doc 14.6 export-dialog state logic: warning visibility,
// default bundle checked, param mapping, and reducer transitions.

import { describe, expect, it } from "vitest";
import {
  INCLUDE_CO_WARNING_TEXT,
  buildExportParams,
  createInitialExportState,
  exportDialogReducer,
  exportWarnings,
} from "./exportDialog";
import type { ExportResult } from "../types";

describe("export dialog state", () => {
  it("defaults: includeBundle on, includeCo off, no packaging", () => {
    const state = createInitialExportState();
    expect(state.includeCo).toBe(false);
    expect(state.includeBundle).toBe(true);
    expect(state.packageZip).toBe(false);
    expect(state.open).toBe(false);
  });

  it("hides the warning while 包含版本历史 is unchecked", () => {
    const state = createInitialExportState();
    expect(exportWarnings(state)).toEqual([]);
  });

  it("shows the mandatory warning when 包含版本历史 is checked", () => {
    let state = createInitialExportState();
    state = exportDialogReducer(state, { type: "toggle", field: "includeCo" });
    expect(state.includeCo).toBe(true);
    const warnings = exportWarnings(state);
    expect(warnings).toContain(INCLUDE_CO_WARNING_TEXT);
    expect(warnings).toHaveLength(1);
  });

  it("unchecking 包含版本历史 hides the warning again", () => {
    let state = createInitialExportState();
    state = exportDialogReducer(state, { type: "toggle", field: "includeCo" });
    state = exportDialogReducer(state, { type: "toggle", field: "includeCo" });
    expect(state.includeCo).toBe(false);
    expect(exportWarnings(state)).toEqual([]);
    // toggling the bundle never affects the warning
    state = exportDialogReducer(state, { type: "toggle", field: "includeBundle" });
    expect(exportWarnings(state)).toEqual([]);
  });

  it("toggling the bundle flips the default-on checkbox", () => {
    let state = createInitialExportState();
    state = exportDialogReducer(state, { type: "toggle", field: "includeBundle" });
    expect(state.includeBundle).toBe(false);
    state = exportDialogReducer(state, { type: "toggle", field: "includeBundle" });
    expect(state.includeBundle).toBe(true);
  });

  it("builds exportDoc params from the dialog state", () => {
    let state = createInitialExportState();
    state = exportDialogReducer(state, { type: "toggle", field: "includeCo" });
    state = exportDialogReducer(state, { type: "toggle", field: "packageZip" });
    const params = buildExportParams(state, "default", "/tmp/out.docx");
    expect(params).toEqual({
      docId: "default",
      includeCo: true,
      includeBundle: true,
      package: true,
      path: "/tmp/out.docx",
    });
  });

  it("open/begin/succeeded transitions clear the busy flag", () => {
    let state = createInitialExportState();
    state = exportDialogReducer(state, { type: "open" });
    expect(state.open).toBe(true);
    state = exportDialogReducer(state, { type: "begin" });
    expect(state.exporting).toBe(true);
    const result: ExportResult = {
      ok: true,
      path: "/tmp/out.docx",
      bundlePath: "/tmp/out.docx.co-bundle",
      packagePath: null,
      includeCo: false,
      warning: "",
      warnings: [],
    };
    state = exportDialogReducer(state, { type: "succeeded", result });
    expect(state.exporting).toBe(false);
    expect(state.done).toBe(true);
    expect(state.lastResult).toBe(result);
  });

  it("failed sets the error and clears the busy flag", () => {
    let state = createInitialExportState();
    state = exportDialogReducer(state, { type: "open" });
    state = exportDialogReducer(state, { type: "begin" });
    state = exportDialogReducer(state, { type: "failed", error: "boom" });
    expect(state.exporting).toBe(false);
    expect(state.error).toBe("boom");
    expect(state.done).toBe(false);
  });
});
