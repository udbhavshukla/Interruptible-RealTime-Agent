import { BarRow, GlassCard, MetricCard, PageHeader, SectionTitle, Sparkline } from "../components/ui";

function LineChart({ series, labels, height = 180 }: { series: { color: string; points: number[] }[]; labels: string[]; height?: number }) {
  const w = 560;
  const h = height;
  const all = series.flatMap((s) => s.points);
  const max = Math.max(...all, 1);
  const path = (pts: number[]) => {
    const step = w / Math.max(pts.length - 1, 1);
    return pts.map((p, i) => `${i === 0 ? "M" : "L"}${(i * step).toFixed(1)},${(h - 14 - (p / max) * (h - 28)).toFixed(1)}`).join(" ");
  };
  return (
    <div>
      <svg viewBox={`0 0 ${w} ${h}`} style={{ width: "100%", display: "block" }}>
        {[0.25, 0.5, 0.75].map((f) => (
          <line key={f} x1="0" y1={h * f} x2={w} y2={h * f} stroke="var(--color-line)" />
        ))}
        {series.map((s, i) => (
          <path key={i} d={path(s.points)} fill="none" stroke={s.color} strokeWidth="2" strokeLinecap="round" />
        ))}
        {series[0].points.map((_, i) => (
          <circle key={i} cx={(i * (w / Math.max(series[0].points.length - 1, 1))).toFixed(1)} cy={(h - 14 - (series[0].points[i] / max) * (h - 28)).toFixed(1)} r="3" fill="var(--color-chart-1)" />
        ))}
      </svg>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "var(--text-faint)" }}>
        {labels.map((l) => <span key={l}>{l}</span>)}
      </div>
    </div>
  );
}

export default function Analytics() {
  return (
    <div className="page">
      <PageHeader title="Analytics" sub="Measure AURA's real-time behavior · last 24 hours" />
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", marginBottom: 16 }}>
        <MetricCard icon="◷" label="TTFR p50" value="184 ms" sub={<>↓ 12% week over week</>} subTone="down" />
        <MetricCard icon="⚡" label="Interruption Latency" value="96 ms" sub={<>cancel signal → ack</>} />
        <MetricCard icon="↻" label="Re-plan Latency" value="310 ms" sub={<>state v+1 → new call</>} />
        <MetricCard icon="✓" label="Recovery Rate" value="98.4%" sub={<>interrupted → completed</>} />
        <MetricCard icon="⛨" label="Stale Rejected" value="8" sub={<>0 leaked · <span className="bad">target 0</span></>} />
        <MetricCard icon="⬢" label="Duplicate Effects" value="0" sub={<>idempotency intact</>} />
      </div>
      <div className="cols-2" style={{ marginBottom: 16 }}>
        <GlassCard>
          <SectionTitle>Response latency (ms, p50 / p95)</SectionTitle>
          <LineChart
            series={[
              { color: "var(--color-chart-1)", points: [210, 198, 220, 190, 184, 192, 178, 184] },
              { color: "var(--color-chart-2)", points: [420, 400, 440, 390, 380, 395, 370, 372] },
            ]}
            labels={["00:00", "04:00", "08:00", "12:00", "16:00", "20:00", "now"]}
          />
        </GlassCard>
        <GlassCard>
          <SectionTitle>Interruptions over time</SectionTitle>
          <LineChart
            series={[{ color: "var(--color-chart-3)", points: [2, 5, 3, 8, 6, 11, 9, 12] }]}
            labels={["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]}
          />
        </GlassCard>
      </div>
      <div className="cols-3">
        <GlassCard>
          <SectionTitle>Tool execution duration</SectionTitle>
          <BarRow label="flight_search" value="4.2s" pct={82} />
          <BarRow label="booking" value="2.8s" pct={55} color="var(--color-chart-2)" />
          <BarRow label="manual_lookup" value="0.9s" pct={22} color="var(--color-chart-3)" />
          <BarRow label="reserve" value="1.4s" pct={34} color="var(--color-chart-4)" />
        </GlassCard>
        <GlassCard>
          <SectionTitle>Recovery & rejections</SectionTitle>
          <BarRow label="Recovery rate" value="98.4%" pct={98} color="var(--color-success)" />
          <BarRow label="Stale rejected" value="8" pct={64} color="var(--color-error)" />
          <BarRow label="Cancelled cleanly" value="27" pct={88} color="var(--color-warning)" />
          <div style={{ marginTop: 14 }}>
            <div className="metric-label">Active sessions</div>
            <Sparkline points={[6, 8, 7, 10, 9, 12, 11, 12]} color="var(--color-chart-2)" w={220} h={44} />
          </div>
        </GlassCard>
        <GlassCard>
          <SectionTitle>Stale-result rejection</SectionTitle>
          <div style={{ textAlign: "center", padding: "10px 0" }}>
            <div style={{ fontSize: 44, fontWeight: 800, color: "var(--cyan)" }}>100%</div>
            <div style={{ color: "var(--text-dim)", fontSize: 13 }}>of superseded results rejected<br />before reaching state or UI</div>
          </div>
          <div className="metric-sub" style={{ textAlign: "center" }}>8 rejected · 0 leaked · 0 duplicate effects</div>
        </GlassCard>
      </div>
    </div>
  );
}
