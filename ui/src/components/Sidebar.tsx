// Sidebar shell (planning doc 9.1/9.2): collapsible left panel with the
// Chat / Context / Tools / Diff / History sections.

import type { ReactNode } from "react";
import type { SectionId } from "../types";

export interface SectionDef {
  id: SectionId;
  label: string;
  glyph: string;
}

export const SECTIONS: SectionDef[] = [
  { id: "chat", label: "Chat", glyph: "💬" },
  { id: "context", label: "Context", glyph: "📄" },
  { id: "tools", label: "Tools", glyph: "🛠" },
  { id: "diff", label: "Diff", glyph: "⇄" },
  { id: "history", label: "History", glyph: "🕘" },
  { id: "settings", label: "Settings", glyph: "⚙" },
];

interface SidebarProps {
  activeSection: SectionId;
  collapsed: boolean;
  onSelectSection: (section: SectionId) => void;
  onToggleCollapsed: () => void;
  onOpenExport: () => void;
  onOpenPalette: () => void;
  children: ReactNode;
}

export function Sidebar({
  activeSection,
  collapsed,
  onSelectSection,
  onToggleCollapsed,
  onOpenExport,
  onOpenPalette,
  children,
}: SidebarProps) {
  return (
    <div className={`sidebar${collapsed ? " sidebar--collapsed" : ""}`} data-testid="sidebar">
      <nav className="sidebar-rail" aria-label="Agent Deck 分区">
        <button
          type="button"
          className="rail-toggle"
          onClick={onToggleCollapsed}
          aria-label={collapsed ? "展开侧边栏" : "收起侧边栏"}
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
          onClick={onOpenExport}
          aria-label="导出"
          title="导出文档"
          data-testid="rail-export"
        >
          <span className="rail-glyph" aria-hidden="true">⤓</span>
          {!collapsed && <span className="rail-label">导出</span>}
        </button>
        <button
          type="button"
          className="rail-item"
          onClick={onOpenPalette}
          aria-label="命令面板"
          title="命令面板 (Cmd+K)"
          data-testid="rail-palette"
        >
          <span className="rail-glyph" aria-hidden="true">⌘</span>
          {!collapsed && <span className="rail-label">命令</span>}
        </button>
      </nav>
      <div className="sidebar-content">{children}</div>
    </div>
  );
}
