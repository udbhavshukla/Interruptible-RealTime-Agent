import React from "react";
import { SESSIONS } from "../data/catalog";
import { useAura } from "../store/AuraContext";
import type { SessionInfo } from "../types";
import { Badge, Drawer, GlassCard, JsonView, PageHeader } from "../components/ui";

const STATUS_TONE: Record<string, string> = { Active: "green", Completed: "blue", Interrupted: "amber" };

export default function Sessions() {
  const { events } = useAura();
  const [q, setQ] = React.useState("");
  const [status, setStatus] = React.useState("All");
  const [sel, setSel] = React.useState<SessionInfo | null>(null);
  const rows = SESSIONS.filter((s) =>
    (status === "All" || s.status === status) &&
    (s.id.includes(q.toLowerCase()) || s.intent.toLowerCase().includes(q.toLowerCase()))
  );
  const sessEvents = sel ? events.filter((e) => e.sessionId === "sess_live").slice(-30) : [];
  return (
    <div className="page">
      <PageHeader title="Sessions" sub="Manage and inspect AURA interactions." />
      <div style={{ display: "flex", gap: 10, marginBottom: 16, flexWrap: "wrap" }}>
        <input className="input" style={{ maxWidth: 300 }} placeholder="Search sessions…" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="select" style={{ maxWidth: 170 }} value={status} onChange={(e) => setStatus(e.target.value)}>
          {["All", "Active", "Completed", "Interrupted"].map((s) => <option key={s}>{s}</option>)}
        </select>
        <select className="select" style={{ maxWidth: 170 }} defaultValue="Last 24h">
          <option>Last 24h</option><option>Last 7 days</option><option>All time</option>
        </select>
      </div>
      <GlassCard className="table-wrap" hover={false} style={{ padding: 6 } as React.CSSProperties}>
        <table className="data">
          <thead><tr><th>Session</th><th>Intent</th><th>Status</th><th>Duration</th><th>Interruptions</th><th>State Version</th><th>Updated</th></tr></thead>
          <tbody>
            {rows.map((s) => (
              <tr key={s.id} onClick={() => setSel(s)}>
                <td className="mono" style={{ color: "var(--cyan)" }}>{s.id}</td>
                <td>{s.intent}</td>
                <td><Badge tone={STATUS_TONE[s.status]}>{s.status}</Badge></td>
                <td className="mono">{s.duration}</td>
                <td>{s.interruptions > 0 ? <Badge tone="amber">{s.interruptions} interruptions</Badge> : <span style={{ color: "var(--text-faint)" }}>0</span>}</td>
                <td className="mono">{s.version}</td>
                <td style={{ color: "var(--text-dim)" }}>{s.updated}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </GlassCard>
      {sel && (
        <Drawer title={`Session ${sel.id}`} onClose={() => setSel(null)}>
          <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
            <Badge tone={STATUS_TONE[sel.status]}>{sel.status}</Badge>
            <Badge tone="cyan">{sel.version}</Badge>
            <Badge>{sel.intent}</Badge>
          </div>
          <Section h="Conversation" />
          <p style={{ fontSize: 13, color: "var(--text-dim)" }}>Flight search with one mid-flight correction (Delhi → Mumbai). Final answer rendered from v3 only.</p>
          <Section h="State history" />
          <JsonView data={{ v1: { destination: "Delhi" }, v2: { destination: "Mumbai" }, v3: { destination: "Mumbai", confirmed: true } }} />
          <Section h="Tool calls" />
          <JsonView data={[{ call_id: "call_204", version: 1, status: "cancelled" }, { call_id: "call_205", version: 2, status: "completed" }]} />
          <Section h={`Live trace (${sessEvents.length} demo events)`} />
          {sessEvents.length === 0
            ? <p style={{ fontSize: 12.5, color: "var(--text-faint)" }}>Run the Live Agent demo to stream events here.</p>
            : sessEvents.slice(-8).map((e) => <div key={e.id} style={{ fontSize: 12.5, padding: "6px 0", borderBottom: "1px dashed var(--color-line)" }}><span className="mono" style={{ color: "var(--cyan)" }}>{e.type}</span> · {e.summary}</div>)}
        </Drawer>
      )}
    </div>
  );
}

function Section({ h }: { h: string }) {
  return <div className="metric-label" style={{ margin: "16px 0 8px" }}>{h}</div>;
}
