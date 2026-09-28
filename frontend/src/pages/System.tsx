import { BarRow, GlassCard, PageHeader, SectionTitle, StatusDot } from "../components/ui";

const SERVICES = [
  { name: "Fast Path", detail: "ACK p50 · 142 ms", uptime: "99.99%", req: "1.2k", err: "0.0%" },
  { name: "Slow Path", detail: "tool exec · 4 running", uptime: "99.97%", req: "318", err: "0.3%" },
  { name: "Coordination", detail: "re-plan p50 · 310 ms", uptime: "99.99%", req: "342", err: "0.0%" },
  { name: "State Manager", detail: "v-counter · 0 leaks", uptime: "100%", req: "2.1k", err: "0.0%" },
  { name: "Tool Registry", detail: "6 manifests loaded", uptime: "100%", req: "318", err: "0.0%" },
  { name: "Multimodal", detail: "STT + vision ready", uptime: "99.95%", req: "96", err: "0.1%" },
  { name: "WebSocket", detail: "connected · 41 ms ping", uptime: "99.98%", req: "—", err: "0.0%" },
];

export default function System() {
  return (
    <div className="page">
      <PageHeader title="System Health" sub="Live service status and request flow · mock backend" />
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", marginBottom: 16 }}>
        {SERVICES.map((s) => (
          <GlassCard key={s.name} hover>
            <div style={{ display: "flex", gap: 9, alignItems: "center", marginBottom: 8 }}>
              <StatusDot tone="online" />
              <b style={{ fontSize: 13.5 }}>{s.name}</b>
              <span style={{ marginLeft: "auto", fontSize: 10.5, color: "var(--success)", fontWeight: 800 }}>
                {s.name === "WebSocket" ? "CONNECTED" : "ONLINE"}
              </span>
            </div>
            <div style={{ fontSize: 12, color: "var(--text-dim)" }}>{s.detail}</div>
            <div className="mono" style={{ fontSize: 11, color: "var(--text-faint)", marginTop: 8 }}>
              up {s.uptime} · req {s.req} · err {s.err}
            </div>
          </GlassCard>
        ))}
      </div>
      <div className="cols-2">
        <GlassCard>
          <SectionTitle>Request flow</SectionTitle>
          <FlowDiagram />
        </GlassCard>
        <GlassCard>
          <SectionTitle>Load</SectionTitle>
          <BarRow label="Fast path traffic" value="1.2k req" pct={86} />
          <BarRow label="Slow path traffic" value="318 req" pct={42} color="var(--color-chart-2)" />
          <BarRow label="Interruptions" value="27 today" pct={30} color="var(--color-warning)" />
          <BarRow label="Error budget used" value="0.1%" pct={3} color="var(--color-success)" />
          <div className="metric-sub" style={{ marginTop: 10 }}>SLO: 99.9% fast-ack under 250 ms · currently 99.99%.</div>
        </GlassCard>
      </div>
    </div>
  );
}

function FlowDiagram() {
  const node: React.CSSProperties = {
    border: "1px solid var(--color-border-strong)", borderRadius: 12,
    padding: "9px 14px", fontSize: 12.5, fontWeight: 700, textAlign: "center",
    background: "color-mix(in srgb, var(--color-accent) 8%, transparent)",
    color: "var(--color-text)",
  };
  const arrow: React.CSSProperties = { textAlign: "center", color: "var(--color-accent-ink)", fontSize: 15, lineHeight: 1.1 };
  return (
    <div style={{ maxWidth: 420, margin: "0 auto" }}>
      <div style={node}>USER</div><div style={arrow}>↓</div>
      <div style={node}>FAST PATH <span style={{ fontWeight: 400, color: "var(--text-dim)" }}>· ack 142 ms</span></div><div style={arrow}>↓</div>
      <div style={node}>COORDINATION</div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, margin: "8px 0" }}>
        <div style={{ ...node, borderColor: "var(--color-border-strong)", background: "color-mix(in srgb, var(--color-info) 10%, transparent)" }}>STATE<br /><span style={{ fontWeight: 400, fontSize: 11, color: "var(--text-dim)" }}>versions · bindings</span></div>
        <div style={node}>SLOW PATH<br /><span style={{ fontWeight: 400, fontSize: 11, color: "var(--text-dim)" }}>async tools</span></div>
      </div>
      <div style={arrow}>↓</div>
      <div style={node}>TOOLS <span style={{ fontWeight: 400, color: "var(--text-dim)" }}>· registry · idempotent</span></div><div style={arrow}>↓</div>
      <div style={{ ...node, borderColor: "var(--color-border-strong)", background: "color-mix(in srgb, var(--color-success) 10%, transparent)" }}>FINAL RESULT</div>
    </div>
  );
}
