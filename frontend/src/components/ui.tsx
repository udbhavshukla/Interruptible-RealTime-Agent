import React from "react";

/* ---------- layout primitives ---------- */
export function GlassCard({ children, className = "", hover = false, style }: {
  children: React.ReactNode; className?: string; hover?: boolean; style?: React.CSSProperties;
}) {
  return <div className={`glass glass-pad${hover ? " glass-hover" : ""} ${className}`} style={style}>{children}</div>;
}

export function PageHeader({ title, sub, right }: { title: string; sub: string; right?: React.ReactNode }) {
  return (
    <div className="page-head" style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16, marginBottom: 20 }}>
      <div><h1>{title}</h1><p>{sub}</p></div>
      {right && <div style={{ display: "flex", gap: 10, flex: "none" }}>{right}</div>}
    </div>
  );
}

export function SectionTitle({ children, right }: { children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", margin: "4px 2px 12px" }}>
      <div className="metric-label" style={{ fontSize: 12 }}>{children}</div>
      {right}
    </div>
  );
}

/* ---------- indicators ---------- */
export function StatusDot({ tone = "online" }: { tone?: "online" | "busy" | "warn" | "bad" }) {
  return <span className={`dot ${tone}`} />;
}

export function Badge({ tone = "", children }: { tone?: string; children: React.ReactNode }) {
  return <span className={`badge ${tone}`}>{children}</span>;
}

/* ---------- metrics / charts ---------- */
export function MetricCard({ icon, label, value, sub, subTone }: {
  icon: string; label: string; value: string; sub?: React.ReactNode; subTone?: string;
}) {
  return (
    <GlassCard hover>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <div className="metric-label">{label}</div>
          <div className="metric-value" style={{ marginTop: 6 }}>{value}</div>
          {sub && <div className="metric-sub"><span className={subTone ?? ""}>{sub}</span></div>}
        </div>
        <div style={{ fontSize: 22, opacity: 0.85 }}>{icon}</div>
      </div>
    </GlassCard>
  );
}

export function Sparkline({ points, color = "var(--color-chart-1)", w = 120, h = 34 }: { points: number[]; color?: string; w?: number; h?: number }) {
  const max = Math.max(...points, 1);
  const min = Math.min(...points, 0);
  const step = w / Math.max(points.length - 1, 1);
  const d = points.map((p, i) => `${i === 0 ? "M" : "L"}${(i * step).toFixed(1)},${(h - 3 - ((p - min) / (max - min || 1)) * (h - 6)).toFixed(1)}`).join(" ");
  const id = React.useId().replace(/:/g, "");
  return (
    <svg width={w} height={h} style={{ display: "block" }}>
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.45" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={`${d} L${w},${h} L0,${h} Z`} fill={`url(#${id})`} />
      <path d={d} fill="none" stroke={color} strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

export function BarRow({ label, value, pct, color = "var(--color-chart-1)" }: { label: string; value: string; pct: number; color?: string }) {
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12.5, marginBottom: 5 }}>
        <span style={{ color: "var(--text-dim)" }}>{label}</span>
        <span className="mono">{value}</span>
      </div>
      <div style={{ height: 7, borderRadius: 5, background: "var(--color-line)", overflow: "hidden" }}>
        <div style={{ width: `${pct}%`, height: "100%", borderRadius: 5, background: `linear-gradient(90deg, ${color}, color-mix(in srgb, ${color} 55%, transparent))` }} />
      </div>
    </div>
  );
}

/* ---------- overlays ---------- */
export function Modal({ title, onClose, children, wide }: {
  title: string; onClose: () => void; children: React.ReactNode; wide?: boolean;
}) {
  return (
    <>
      <div className="overlay-back" onClick={onClose} />
      <div className="glass modal glass-pad" style={wide ? { width: "min(760px, 94vw)" } : undefined}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
          <b style={{ fontSize: 15 }}>{title}</b>
          <button className="btn btn-ghost btn-sm" onClick={onClose}>✕</button>
        </div>
        {children}
      </div>
    </>
  );
}

export function Drawer({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <>
      <div className="overlay-back" onClick={onClose} />
      <div className="glass drawer glass-pad">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
          <b style={{ fontSize: 15 }}>{title}</b>
          <button className="btn btn-ghost btn-sm" onClick={onClose}>✕</button>
        </div>
        {children}
      </div>
    </>
  );
}

export function JsonView({ data }: { data: unknown }) {
  const html = React.useMemo(() => {
    return JSON.stringify(data, null, 2)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/"([^"]+)":/g, '<span class="k">"$1"</span>:')
      .replace(/: "([^"]*)"/g, ': <span class="s">"$1"</span>')
      .replace(/: (\d[\d.]*)/g, ': <span class="n">$1</span>')
      .replace(/: (true|false|null)/g, ': <span class="b">$1</span>');
  }, [data]);
  return <div className="json" dangerouslySetInnerHTML={{ __html: html }} />;
}

export function Toggle({ on, onChange }: { on: boolean; onChange: (v: boolean) => void }) {
  return <button className={`toggle${on ? " on" : ""}`} onClick={() => onChange(!on)} aria-pressed={on} />;
}

export function fmtTime(ts: number): string {
  const d = new Date(ts);
  return d.toTimeString().slice(0, 8);
}
