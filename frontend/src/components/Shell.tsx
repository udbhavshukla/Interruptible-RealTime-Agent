import React from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";
import { useAura } from "../store/AuraContext";
import { useTheme } from "../theme/ThemeContext";
import { StatusDot } from "./ui";

const NAV_MAIN = [
  { to: "/", label: "Overview", icon: "◈", end: true },
  { to: "/agent", label: "Live Agent", icon: "◉" },
  { to: "/sessions", label: "Sessions", icon: "▤" },
  { to: "/activity", label: "Activity", icon: "≋" },
  { to: "/tools", label: "Tools", icon: "⬢" },
  { to: "/multimodal", label: "Multimodal", icon: "◐" },
  { to: "/analytics", label: "Analytics", icon: "▦" },
];
const NAV_SYS = [
  { to: "/system", label: "System Health", icon: "⬣" },
  { to: "/settings", label: "Settings", icon: "⚙" },
];

export function Logo({ compact = false }: { compact?: boolean }) {
  return (
    <Link to="/" style={{ display: "flex", alignItems: "center", gap: 11, padding: "18px 20px 14px" }}>
      <span style={{
        width: 34, height: 34, borderRadius: 11, flex: "none",
        background: "var(--grad-brand)",
        display: "flex", alignItems: "center", justifyContent: "center",
        fontWeight: 800, fontSize: 17, color: "var(--color-avatar-text)",
        boxShadow: "0 4px 18px color-mix(in srgb, var(--color-accent) 40%, transparent)",
      }}>◍</span>
      {!compact && (
        <span>
          <span style={{ display: "block", fontWeight: 800, fontSize: 16, letterSpacing: "0.14em" }}>AURA</span>
          <span style={{ display: "block", fontSize: 10, color: "var(--text-faint)", letterSpacing: "0.04em" }}>Interruptible Real-Time AI</span>
        </span>
      )}
    </Link>
  );
}

export function Sidebar({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  const { connected } = useAura();
  const render = (items: typeof NAV_MAIN) =>
    items.map((n) => (
      <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => `nav-item${isActive ? " active" : ""}`} title={n.label}>
        <span className="nav-icon">{n.icon}</span>
        <span className="nav-label">{n.label}</span>
      </NavLink>
    ));
  return (
    <aside className={`sidebar${collapsed ? " collapsed" : ""}`}>
      <Logo compact={collapsed} />
      <div style={{ flex: 1, overflowY: "auto", paddingBottom: 8 }}>
        <div className="side-section metric-label" style={{ padding: "8px 26px 6px", fontSize: 10.5 }}>Overview</div>
        {render(NAV_MAIN)}
        <div className="side-section metric-label" style={{ padding: "14px 26px 6px", fontSize: 10.5 }}>System</div>
        {render(NAV_SYS)}
      </div>
      <div style={{ padding: 14, borderTop: "1px solid var(--glass-border)" }}>
        <div className="glass" style={{ borderRadius: 12, padding: "10px 12px", display: "flex", gap: 10, alignItems: "center" }}>
          <StatusDot tone={connected ? "online" : "bad"} />
          <span className="side-foot-text" style={{ fontSize: 12 }}>
            <b style={{ display: "block" }}>{connected ? "Agent Online" : "Offline"}</b>
            <span style={{ color: "var(--text-faint)" }}>{connected ? "WebSocket connected" : "reconnecting…"}</span>
          </span>
        </div>
        <div className="side-foot-text" style={{ display: "flex", gap: 10, alignItems: "center", marginTop: 12, padding: "0 4px" }}>
          <span style={{ width: 30, height: 30, borderRadius: "50%", background: "var(--grad-brand)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 13, fontWeight: 800, flex: "none", color: "var(--color-avatar-text)" }}>D</span>
          <span style={{ fontSize: 12 }}><b style={{ display: "block" }}>Demo User</b><span style={{ color: "var(--text-faint)" }}>Hackathon build</span></span>
        </div>
        <button className="btn btn-ghost btn-sm side-foot-text" style={{ marginTop: 10, width: "100%", justifyContent: "center" }} onClick={onToggle}>
          {collapsed ? "→" : "← Collapse"}
        </button>
      </div>
    </aside>
  );
}

const TITLES: Record<string, string> = {
  "/": "Overview", "/agent": "Live Agent", "/sessions": "Sessions", "/activity": "Activity",
  "/tools": "Tools", "/multimodal": "Multimodal", "/analytics": "Analytics",
  "/system": "System Health", "/settings": "Settings",
};

export function TopBar({ onPalette, onToggleSidebar }: { onPalette: () => void; onToggleSidebar: () => void }) {
  const { pathname } = useLocation();
  const { connected } = useAura();
  const { mode, setMode } = useTheme();
  const title = TITLES[pathname] ?? "AURA";
  const cycle = () => setMode(mode === "dark" ? "light" : mode === "light" ? "system" : "dark");
  const icon = mode === "dark" ? "☾" : mode === "light" ? "☀" : "◐";
  return (
    <header className="topbar">
      <button className="btn btn-ghost btn-sm" onClick={onToggleSidebar} title="Toggle sidebar">☰</button>
      <b style={{ fontSize: 15 }}>{title}</b>
      <button className="btn btn-sm tb-search" style={{ marginLeft: 8, color: "var(--text-faint)", fontWeight: 500 }} onClick={onPalette}>
        ⌕ Search or command… <span className="mono" style={{ fontSize: 11, opacity: 0.7 }}>⌘K</span>
      </button>
      <div style={{ flex: 1 }} />
      <span className="badge green"><StatusDot tone={connected ? "online" : "bad"} />&nbsp;{connected ? "Agent Online" : "Offline"}</span>
      <button className="btn btn-ghost btn-sm tb-help" title={`Theme: ${mode} — click to switch`} aria-label={`Theme: ${mode}. Activate to switch theme.`} onClick={cycle}>{icon}</button>
      <button className="btn btn-ghost btn-sm" title="Notifications">🔔</button>
      <button className="btn btn-ghost btn-sm tb-help" title="Help">?</button>
      <span style={{ width: 32, height: 32, borderRadius: "50%", background: "var(--grad-brand)", display: "flex", alignItems: "center", justifyContent: "center", fontWeight: 800, fontSize: 13, color: "var(--color-avatar-text)" }}>D</span>
    </header>
  );
}

const COMMANDS = [
  { label: "Open Live Agent", hint: "Demo", to: "/agent" },
  { label: "Open Sessions", hint: "Navigate", to: "/sessions" },
  { label: "Open Activity", hint: "Navigate", to: "/activity" },
  { label: "Open Tools", hint: "Navigate", to: "/tools" },
  { label: "Open Analytics", hint: "Navigate", to: "/analytics" },
  { label: "Open Multimodal", hint: "Navigate", to: "/multimodal" },
  { label: "Open System Health", hint: "Navigate", to: "/system" },
  { label: "Start Demo — flight interruption", hint: "Demo", to: "/agent?demo=1" },
];

export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [q, setQ] = React.useState("");
  const nav = useNavigate();
  React.useEffect(() => {
    if (open) setQ("");
  }, [open ]);
  if (!open) return null;
  const items = COMMANDS.filter((c) => c.label.toLowerCase().includes(q.toLowerCase()));
  const go = (to: string) => { nav(to); onClose(); };
  return (
    <>
      <div className="overlay-back" onClick={onClose} />
      <div className="glass palette">
        <input autoFocus className="input" placeholder="Type a command…" value={q} onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && items[0]) go(items[0].to); if (e.key === "Escape") onClose(); }} />
        <div style={{ padding: "6px 0 10px", maxHeight: 320, overflowY: "auto" }}>
          {items.map((c) => (
            <div key={c.label} className="palette-item" onClick={() => go(c.to)}>
              <span style={{ color: "var(--cyan)" }}>→</span>
              <span style={{ flex: 1 }}>{c.label}</span>
              <span className="badge">{c.hint}</span>
            </div>
          ))}
          {items.length === 0 && <div style={{ padding: 16, color: "var(--text-faint)", fontSize: 13 }}>No matching commands.</div>}
        </div>
      </div>
    </>
  );
}

export function MobileNav() {
  const items = [
    { to: "/", label: "Home", icon: "◈", end: true },
    { to: "/agent", label: "Agent", icon: "◉" },
    { to: "/activity", label: "Activity", icon: "≋" },
    { to: "/tools", label: "Tools", icon: "⬢" },
    { to: "/settings", label: "More", icon: "⚙" },
  ];
  return (
    <nav className="mobile-nav">
      {items.map((n) => (
        <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => (isActive ? "active" : "")}>
          <span style={{ fontSize: 17 }}>{n.icon}</span>{n.label}
        </NavLink>
      ))}
    </nav>
  );
}
