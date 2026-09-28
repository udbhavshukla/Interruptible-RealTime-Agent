import { Link, useNavigate } from "react-router-dom";
import { useAura } from "../store/AuraContext";
import { Badge, GlassCard, MetricCard, PageHeader, SectionTitle, Sparkline, StatusDot, fmtTime } from "../components/ui";

const SERVICES = ["Fast Path", "Slow Path", "Coordination", "Tool Registry", "Multimodal"];

export default function Overview() {
  const { events } = useAura();
  const nav = useNavigate();
  const recent = [...events].slice(-6).reverse();
  const hour = new Date().getHours();
  const greet = hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
  return (
    <div className="page">
      <div style={{ padding: "10px 2px 22px", maxWidth: 760 }}>
        <div style={{ fontSize: 13, fontWeight: 800, letterSpacing: "0.22em", color: "var(--cyan)" }}>AURA · INTERRUPTIBLE REAL-TIME AI</div>
        <h1 style={{ margin: "10px 0 8px", fontSize: "clamp(28px, 4.5vw, 38px)", letterSpacing: "-0.02em", lineHeight: 1.1 }}>Intelligence that adapts<br />to your latest intent.</h1>
        <p style={{ margin: 0, color: "var(--text-dim)", fontSize: 15 }}>{greet} — AURA is ready for real-time interaction.</p>
        <div style={{ display: "flex", gap: 12, marginTop: 18, flexWrap: "wrap" }}>
          <Link to="/agent?demo=1" className="btn btn-primary" style={{ padding: "12px 26px", fontSize: 14.5 }}>Launch Live Agent →</Link>
          <Link to="/activity" className="btn" style={{ padding: "12px 22px", fontSize: 14 }}>Watch event trace</Link>
        </div>
        <div style={{ display: "flex", gap: 14, marginTop: 22, flexWrap: "wrap" }} className="pillars">
          {[["FAST PATH", "acknowledges in ~140 ms"], ["SLOW PATH", "tools run asynchronously"], ["COORDINATION", "latest intent always wins"]].map(([t, s], i) => (
            <div key={t} style={{ flex: "1 1 160px", padding: "4px 18px 4px 0", borderLeft: i === 0 ? "none" : "1px solid var(--glass-border)", paddingLeft: i === 0 ? 0 : 18 }}>
              <div style={{ fontSize: 12, fontWeight: 800, letterSpacing: "0.12em" }}>{t}</div>
              <div style={{ fontSize: 12.5, color: "var(--text-dim)", marginTop: 3 }}>{s}</div>
            </div>
          ))}
        </div>
      </div>
      <PageHeader title="System at a glance" sub="Live metrics · mock backend" />
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", marginBottom: 16 }}>
        <MetricCard icon="◔" label="Active Sessions" value="12" sub={<>▲ 2 this hour</>} />
        <MetricCard icon="◉" label="Running Operations" value="4" sub={<>across 3 sessions</>} />
        <MetricCard icon="⚡" label="Interruptions Today" value="27" sub={<>↓ 12% handled faster</>} subTone="down" />
        <MetricCard icon="⛨" label="Stale Results Rejected" value="8" sub={<>0 leaked to state</>} />
        <MetricCard icon="✓" label="Recovery Rate" value="98.4%" sub={<>▲ 0.6 pts</>} />
        <MetricCard icon="◷" label="Average TTFR" value="184 ms" sub={<>↓ 12% from previous session</>} subTone="down" />
      </div>
      <div className="cols-2r" style={{ marginBottom: 16 }}>
        <GlassCard>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
            <b style={{ letterSpacing: "0.1em", fontSize: 13 }}>AURA ENGINE</b>
            <Badge tone="green"><StatusDot tone="online" />&nbsp;Online</Badge>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginTop: 12 }}>
            {SERVICES.map((s) => (
              <div key={s} className="glass" style={{ borderRadius: 12, padding: "11px 13px", display: "flex", gap: 9, alignItems: "center" }}>
                <StatusDot tone="online" />
                <span style={{ fontSize: 13, fontWeight: 600 }}>{s}</span>
                <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--success)", fontWeight: 700 }}>
                  {s === "Multimodal" ? "READY" : "ONLINE"}
                </span>
              </div>
            ))}
          </div>
          <div style={{ marginTop: 14, display: "flex", alignItems: "flex-end", justifyContent: "space-between" }}>
            <div>
              <div className="metric-label">Fast-path acknowledgements</div>
              <Sparkline points={[42, 55, 48, 66, 58, 74, 69, 82, 77, 90]} />
            </div>
            <div style={{ textAlign: "right" }}>
              <div className="metric-label">Tool completions</div>
              <Sparkline points={[12, 18, 15, 24, 22, 30, 28, 36, 34, 41]} color="var(--color-chart-2)" />
            </div>
          </div>
        </GlassCard>
        <GlassCard>
          <SectionTitle right={<Link to="/activity" style={{ fontSize: 12, color: "var(--blue-soft)" }}>View all →</Link>}>Recent activity</SectionTitle>
          <div>
            {recent.length === 0 && <div style={{ color: "var(--text-faint)", fontSize: 13 }}>No events yet — launch the Live Agent to generate traffic.</div>}
            {recent.map((e) => (
              <div key={e.id} className="exec-line" onClick={() => nav("/activity")} style={{ cursor: "pointer" }}>
                <span className="mono" style={{ color: "var(--text-faint)", fontSize: 11.5, flex: "none" }}>{fmtTime(e.ts)}</span>
                <span style={{ fontSize: 13 }}>{e.summary}</span>
              </div>
            ))}
          </div>
        </GlassCard>
      </div>
      <GlassCard style={{ background: "linear-gradient(135deg, color-mix(in srgb, var(--color-accent) 12%, transparent), color-mix(in srgb, var(--color-info) 7%, transparent))", display: "flex", alignItems: "center", gap: 20, flexWrap: "wrap" }}>
        <div style={{ flex: 1, minWidth: 240 }}>
          <b style={{ fontSize: 19 }}>Try AURA</b>
          <p style={{ margin: "6px 0 0", color: "var(--text-dim)", fontSize: 13.5 }}>Experience interruption-aware intelligence — start a search, then interrupt it mid-flight.</p>
        </div>
        <Link to="/agent?demo=1" className="btn btn-primary">Launch Live Agent →</Link>
      </GlassCard>
    </div>
  );
}
