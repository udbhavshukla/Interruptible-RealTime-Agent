import React from "react";
import { useAura } from "../store/AuraContext";
import type { AuraEvent } from "../types";
import { Badge, Drawer, GlassCard, JsonView, PageHeader, fmtTime } from "../components/ui";

const FILTERS = ["All", "User", "Agent", "Tool", "State", "Interruptions", "Errors"] as const;

const TONE: Record<string, string> = {
  USER_INPUT: "blue", FAST_ACK: "green", TOOL_STARTED: "blue", INTERRUPTION: "amber",
  CANCEL_REQUESTED: "amber", TOOL_CANCELLED: "red", STATE_UPDATED: "cyan", REPLAN: "cyan",
  TOOL_COMPLETED: "green", STALE_RESULT_REJECTED: "red", FINAL_RESPONSE: "green",
  CLARIFICATION: "amber", FILLER: "", TRANSCRIPTION: "cyan", GROUNDING: "cyan",
};

function groupOf(e: AuraEvent): string {
  switch (e.type) {
    case "USER_INPUT": case "INTERRUPTION": return "User";
    case "FAST_ACK": case "FINAL_RESPONSE": case "CLARIFICATION": case "FILLER": case "TRANSCRIPTION": case "GROUNDING": return "Agent";
    case "TOOL_STARTED": case "TOOL_COMPLETED": case "REPLAN": return "Tool";
    case "STATE_UPDATED": return "State";
    case "CANCEL_REQUESTED": case "TOOL_CANCELLED": return "Interruptions";
    case "STALE_RESULT_REJECTED": return "Errors";
    default: return "Agent";
  }
}

export default function Activity() {
  const { events, clearEvents } = useAura();
  const [filter, setFilter] = React.useState<string>("All");
  const [sel, setSel] = React.useState<AuraEvent | null>(null);
  const rows = [...events].reverse().filter((e) => filter === "All" || groupOf(e) === filter);
  return (
    <div className="page">
      <PageHeader title="Activity" sub="Real-time event stream · click any event for full JSON." right={<button className="btn btn-sm" onClick={clearEvents}>Clear</button>} />
      <div style={{ display: "flex", gap: 8, marginBottom: 16, flexWrap: "wrap" }}>
        {FILTERS.map((f) => (
          <button key={f} className={`btn btn-sm${filter === f ? " btn-primary" : ""}`} onClick={() => setFilter(f)}>{f}</button>
        ))}
      </div>
      <GlassCard style={{ padding: 8 }}>
        {rows.length === 0 && <div style={{ padding: 20, color: "var(--text-faint)", fontSize: 13 }}>No events yet — run the Live Agent demo to populate the stream.</div>}
        {rows.map((e) => (
          <div key={e.id} onClick={() => setSel(e)} className="glass-hover"
            style={{ display: "flex", gap: 12, alignItems: "center", padding: "11px 14px", borderRadius: 12, cursor: "pointer", border: "1px solid transparent" }}>
            <span className="mono" style={{ color: "var(--text-faint)", fontSize: 11.5, flex: "none" }}>{fmtTime(e.ts)}</span>
            <Badge tone={TONE[e.type]}>{e.type}</Badge>
            <span style={{ fontSize: 13, flex: 1 }}>{e.summary}</span>
            {e.callId && <span className="mono" style={{ fontSize: 11.5, color: "var(--cyan)" }}>{e.callId}</span>}
            {e.stateVersion != null && <span className="mono" style={{ fontSize: 11.5, color: "var(--text-faint)" }}>v{e.stateVersion}</span>}
          </div>
        ))}
      </GlassCard>
      {sel && (
        <Drawer title={sel.type} onClose={() => setSel(null)}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 14, fontSize: 12.5 }}>
            <KV k="timestamp" v={new Date(sel.ts).toISOString()} />
            <KV k="session" v={sel.sessionId} />
            <KV k="call_id" v={sel.callId ?? "—"} />
            <KV k="state_version" v={sel.stateVersion != null ? `v${sel.stateVersion}` : "—"} />
            <KV k="tool" v={sel.tool ?? "—"} />
            <KV k="event_id" v={sel.id} />
          </div>
          <div className="metric-label" style={{ marginBottom: 8 }}>Payload JSON</div>
          <div className="glass" style={{ borderRadius: 12, padding: 14 }}>
            <JsonView data={{ type: sel.type, session_id: sel.sessionId, call_id: sel.callId ?? null, state_version: sel.stateVersion ?? null, ...(sel.detail ?? {}) }} />
          </div>
        </Drawer>
      )}
    </div>
  );
}

function KV({ k, v }: { k: string; v: string }) {
  return <div className="glass" style={{ borderRadius: 10, padding: "8px 11px" }}><div className="metric-label" style={{ fontSize: 10 }}>{k}</div><div className="mono" style={{ fontSize: 12, marginTop: 3 }}>{v}</div></div>;
}
