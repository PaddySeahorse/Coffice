// Unit tests for the History panel list rendering (doc 9.2): commit timeline
// rows, rollback wiring, per-commit preview, and the empty state.

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { HistoryPanel } from "./HistoryPanel";
import type { CommitInfo } from "../types";

const COMMITS: CommitInfo[] = [
  {
    hash: "9f3a1c7e",
    author: "AI-Writer",
    email: "ai@coffice.local",
    message: "pre: round-7f3a9b2",
    timestamp: "2026-07-31T14:12:05+00:00",
  },
  {
    hash: "4d0b8a21",
    author: "human:张三",
    email: "zhangsan@example.com",
    message: "撰写引言部分",
    timestamp: "2026-07-31T13:48:33+00:00",
  },
];

describe("HistoryPanel", () => {
  it("renders the co log timeline: hash, author, message, timestamp", () => {
    render(
      <HistoryPanel commits={COMMITS} busy={false} onRollback={vi.fn()} onPreview={vi.fn()} />,
    );
    // hashes are displayed short (7 chars): 9f3a1c7e -> 9f3a1c7
    expect(screen.getByText(/9f3a1c7 · AI-Writer/)).toBeInTheDocument();
    expect(screen.getByText(/4d0b8a2 · human:张三/)).toBeInTheDocument();
    expect(screen.getByText("pre: round-7f3a9b2")).toBeInTheDocument();
    expect(screen.getByText("撰写引言部分")).toBeInTheDocument();
    // timestamps are formatted, not raw ISO
    expect(screen.queryByText("2026-07-31T14:12:05+00:00")).not.toBeInTheDocument();
  });

  it("calls onRollback with the full commit hash", async () => {
    const onRollback = vi.fn();
    const user = userEvent.setup();
    render(
      <HistoryPanel commits={COMMITS} busy={false} onRollback={onRollback} onPreview={vi.fn()} />,
    );
    // the rollback button lives in the expanded detail row
    await user.click(screen.getByTestId("history-9f3a1c7"));
    await user.click(screen.getByTestId("rollback-9f3a1c7"));
    expect(onRollback).toHaveBeenCalledTimes(1);
    expect(onRollback).toHaveBeenCalledWith("9f3a1c7e");
  });

  it("shows an empty state when there are no commits", () => {
    render(
      <HistoryPanel commits={[]} busy={false} onRollback={vi.fn()} onPreview={vi.fn()} />,
    );
    expect(screen.getByText(/暂无提交/)).toBeInTheDocument();
    expect(screen.queryAllByTestId(/^rollback-/)).toHaveLength(0);
  });

  it("expands a commit to preview its diff", async () => {
    const user = userEvent.setup();
    const onPreview = vi.fn().mockResolvedValue([
      { status: "M", path: "word/document.xml" },
    ]);
    render(
      <HistoryPanel
        commits={COMMITS}
        busy={false}
        onRollback={vi.fn()}
        onPreview={onPreview}
      />,
    );
    await user.click(screen.getByTestId("history-9f3a1c7"));
    expect(onPreview).toHaveBeenCalledWith(
      COMMITS[0],
      COMMITS[1], // previous commit is the diff base
    );
    expect(await screen.findByText("word/document.xml")).toBeInTheDocument();
  });
});
