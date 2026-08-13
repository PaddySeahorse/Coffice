// History list helpers (History panel, doc 9.2): formatting and ordering of
// co log commits. Pure functions so rendering is trivially testable.

import type { CommitInfo } from "../types";

/** First 7 characters of a commit hash, or the full hash when shorter. */
export function shortHash(hash: string): string {
  return hash.length > 7 ? hash.slice(0, 7) : hash;
}

/** Format an ISO-8601 timestamp for display (fallback: raw string). */
export function formatTimestamp(timestamp: string | null): string {
  if (!timestamp) return "";
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) {
    return timestamp;
  }
  return date.toLocaleString();
}

/** "abc1234 · John Doe" — compact one-line commit identity. */
export function commitLabel(commit: CommitInfo): string {
  const hash = shortHash(commit.hash);
  const author = commit.author?.trim() ? commit.author.trim() : "unknown";
  return `${hash} · ${author}`;
}

/**
 * Newest-first ordering used by the timeline. `co log` already returns
 * newest-first; this guards against reordered payloads from other sources.
 */
export function sortCommitsNewestFirst(commits: CommitInfo[]): CommitInfo[] {
  return [...commits].sort((a, b) => {
    const ta = a.timestamp ? new Date(a.timestamp).getTime() : 0;
    const tb = b.timestamp ? new Date(b.timestamp).getTime() : 0;
    if (Number.isNaN(ta) && Number.isNaN(tb)) return 0;
    if (Number.isNaN(ta)) return 1;
    if (Number.isNaN(tb)) return -1;
    return tb - ta;
  });
}
