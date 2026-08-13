// Sidebar shell (planning doc 9.1/9.2): collapsible left panel with the
// Chat / Context / Tools / Diff / History sections.

import type { ReactNode } from "react";
import type { SectionId } from "../types";

export interface SectionDef {
  id: SectionId;
  label: string;
  glyph: ReactNode;
}

export const SECTIONS: SectionDef[] = [
  {
    id: "chat",
    label: "Chat",
    glyph: <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m3 21 1.9-5.7a8.5 8.5 0 1 1 3.8 3.8z"/></svg>
  },
  {
    id: "context",
    label: "Context",
    glyph: <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z"/><polyline points="14 2 14 8 20 8"/></svg>
  },
  {
    id: "tools",
    label: "Tools",
    glyph: <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>
  },
  {
    id: "diff",
    label: "Diff",
    glyph: <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="18" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><path d="M13 6h3a2 2 0 0 1 2 2v7"/><path d="M11 18H8a2 2 0 0 1-2-2V9"/></svg>
  },
  {
    id: "history",
    label: "History",
    glyph: <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
  },
  {
    id: "settings",
    label: "Settings",
    glyph: <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/></svg>
  },
];

interface SidebarProps {
  activeSection: SectionId;
  collapsed: boolean;
  theme: "light" | "dark";
  onSelectSection: (section: SectionId) => void;
  onToggleCollapsed: () => void;
  onToggleTheme: () => void;
  onOpenExport: () => void;
  onOpenPalette: () => void;
  children: ReactNode;
}

export function Sidebar({
  activeSection,
  collapsed,
  theme,
  onSelectSection,
  onToggleCollapsed,
  onToggleTheme,
  onOpenExport,
  onOpenPalette,
  children,
}: SidebarProps) {
  return (
    <div className={`sidebar${collapsed ? " sidebar--collapsed" : ""}`} data-testid="sidebar">
      <nav className="sidebar-rail" aria-label="Agent Deck Section">
        <button
          type="button"
          className="rail-toggle"
          onClick={onToggleCollapsed}
          aria-label={collapsed ? "Expand Sidebar" : "Collapse Sidebar"}
          data-testid="rail-toggle"
        >
          {collapsed ? "»" : "«"}
        </button>
        {SECTIONS.map((section) => (
          <button
            key={section.id}
            type="button"
            className={`rail-item${activeSection === section.id ? " rail-item--active" : ""}`}
            onClick={() => onSelectSection(section.id)}
            aria-label={section.label}
            title={section.label}
            data-testid={`rail-${section.id}`}
          >
            <span className="rail-glyph" aria-hidden="true">{section.glyph}</span>
            {!collapsed && <span className="rail-label">{section.label}</span>}
          </button>
        ))}
        <div className="rail-spacer" />
        <button
          type="button"
          className="rail-item"
          onClick={onToggleTheme}
          aria-label="Toggle Theme"
          title={theme === "dark" ? "Switch to Light Mode" : "Switch to Dark Mode"}
          data-testid="rail-theme"
        >
          <span className="rail-glyph" aria-hidden="true">
            {theme === "dark" ? (
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/></svg>
            ) : (
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/></svg>
            )}
          </span>
          {!collapsed && <span className="rail-label">Theme</span>}
        </button>
        <button
          type="button"
          className="rail-item"
          onClick={onOpenExport}
          aria-label="Export"
          title="Export Document"
          data-testid="rail-export"
        >
          <span className="rail-glyph" aria-hidden="true">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" x2="12" y1="15" y2="3"/></svg>
          </span>
          {!collapsed && <span className="rail-label">Export</span>}
        </button>
        <button
          type="button"
          className="rail-item"
          onClick={onOpenPalette}
          aria-label="Command Palette"
          title="Command Palette (Cmd+K)"
          data-testid="rail-palette"
        >
          <span className="rail-glyph" aria-hidden="true">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M15 6v12a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3V6a3 3 0 1 0-3 3h12a3 3 0 1 0-3-3"/></svg>
          </span>
          {!collapsed && <span className="rail-label">Commands</span>}
        </button>
      </nav>
      <div className="sidebar-content">{children}</div>
    </div>
  );
}
