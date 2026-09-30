import React from "react";
import { useSearchParams } from "react-router-dom";
import { useAura } from "../store/AuraContext";
import type { AuraEvent, ChatMessage, FlightOption } from "../types";
import { Badge, GlassCard, PageHeader, SectionTitle, StatusDot, fmtTime } from "../components/ui";

const SID = "sess_live";
let demoFired = false;

function auraTextOf(e: AuraEvent): string | null {
  const d = e.detail as Record<string, unknown> | undefined;
  if ((e.type === "FAST_ACK" || e.type === "FINAL_RESPONSE" || e.type === "INTERRUPTION") && d && typeof d.auraText === "string") return d.auraText;
  return null;
}

export default function LiveAgent() {
  const { client, events } = useAura();
  const [params] = useSearchParams();
  const [input, setInput] = React.useState("");
  const [messages, setMessages] = React.useState<ChatMessage[]>([]);
  const [results, setResults] = React.useState<FlightOption[]>([]);
  const [snapTick, setSnapTick] = React.useState(0);
  const seen = React.useRef(new Set<string>());
  const bottomRef = React.useRef<HTMLDivElement>(null);

  const mine = React.useMemo(() => events.filter((e) => e.sessionId === SID), [events]);
  const snap = React.useMemo(() => client.getSnapshot(SID), [client, mine, snapTick]);

  React.useEffect(() => {
    const fresh = mine.filter((e) => !seen.current.has(e.id));
    if (fresh.length === 0) return;
    fresh.forEach((e) => seen.current.add(e.id));
    const msgs: ChatMessage[] = [];
    for (const e of fresh) {
      if (e.type === "USER_INPUT") msgs.push({ id: e.id, role: "user", text: e.summary, ts: e.ts });
      else {
        const t = auraTextOf(e);
        if (t) msgs.push({ id: e.id, role: "aura", text: t, ts: e.ts });
      }
      if (e.type === "TOOL_COMPLETED" || e.type === "FINAL_RESPONSE") setResults(client.getResults(SID));
      if (e.type === "STATE_UPDATED") setSnapTick((t) => t + 1);
    }
    if (msgs.length) setMessages((m) => [...m, ...msgs]);
  }, [mine, client]);

  React.useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Demo auto-play: search, then interrupt mid-flight.
  React.useEffect(() => {
    if (params.get("demo") !== "1" || demoFired) return;
    demoFired = true;
    const t1 = window.setTimeout(() => client.sendMessage(SID, "Find a flight from Bangalore to Delhi."), 600);
    const t2 = window.setTimeout(() => client.sendMessage(SID, "Actually Mumbai, not Delhi."), 3400);
    return () => { window.clearTimeout(t1); window.clearTimeout(t2); };
  }, [params, client]);

  const runningCall = React.useMemo(() => {
    let active: AuraEvent | null = null;
    for (const e of mine) {
      if (e.type === "TOOL_STARTED") active = e;
      if ((e.type === "TOOL_COMPLETED" || e.type === "TOOL_CANCELLED") && active && e.callId === active.callId) active = null;
    }
    return active;
  }, [mine]);

  const running = runningCall !== null;

  const send = () => {
    const text = input.trim();
    if (!text) return;
    setInput("");
    client.sendMessage(SID, text);
  };
  const interrupt = () => {
    const correction = input.trim();
    setInput("");
    client.sendInterrupt(SID, correction || undefined);
  };

  return (
    <div className="page" style={{ maxWidth: 1560 }}>
      <PageHeader title="Live Agent" sub={`Real-time interruption demo · session sess_live · ${client.backend} backend`} />
      <PageHeader title="Live Agent" sub="Real-time interruption demo · session sess_live · mock backend" />
      <div className="agent-grid">
        {/* Conversation */}
        <GlassCard>
          <SectionTitle right={<Badge tone="green"><StatusDot tone="online" />&nbsp;Live</Badge>}>Conversation</SectionTitle>
          <div style={{ display: "flex", flexDirection: "column", gap: 12, minHeight: 380, maxHeight: 520, overflowY: "auto", padding: "4px 2px" }}>
            {messages.length === 0 && (
              <div style={{ color: "var(--text-faint)", fontSize: 13, textAlign: "center", marginTop: 60 }}>
                Ask for a flight — then interrupt mid-search.<br />Try: “Find a flight from Bangalore to Delhi.”
              </div>
            )}
            {messages.map((m) => (
              <div key={m.id} className={`msg ${m.role}`}>
                <div className="msg-role">{m.role === "user" ? "USER" : "AURA"} · {fmtTime(m.ts)}</div>
                {m.text}
              </div>
            ))}
            <div ref={bottomRef} />
          </div>
          <div className="glass composer" style={{ borderRadius: 14, padding: 10, marginTop: 12, display: "flex", gap: 8, alignItems: "center" }}>
            <button className="btn btn-ghost btn-sm" title="Attach">＋</button>
            <input className="input" style={{ border: "none", background: "transparent" }} placeholder="Ask AURA anything…"
              value={input} onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") send(); }} />
            <button className="btn btn-ghost btn-sm" title="Image">🖼</button>
            <button className="btn btn-ghost btn-sm" title="Voice">🎙</button>
            {running ? (
              <button className="btn btn-interrupt" onClick={interrupt} aria-label="Interrupt running operation">■ Interrupt</button>
            ) : (
              <button className="btn btn-primary" onClick={send}>➤</button>
            )}
          </div>
        </GlassCard>

        {/* Live execution */}
        <GlassCard>
          <SectionTitle right={running ? <Badge tone="blue"><span className="spin" style={{ width: 10, height: 10 }} />&nbsp;Running</Badge> : <Badge tone="green">Idle</Badge>}>
            Live execution
          </SectionTitle>
          <ExecFeed events={mine} />
          {results.length > 0 && (
            <div style={{ marginTop: 14 }}>
              <div className="metric-label" style={{ marginBottom: 8 }}>Results · {snap.destination}</div>
              {results.map((f) => (
                <div key={f.id} className="glass" style={{ borderRadius: 12, padding: "10px 13px", marginBottom: 8, display: "flex", gap: 12, alignItems: "center" }}>
                  <div style={{ flex: 1 }}>
                    <b style={{ fontSize: 13 }}>{f.airline}</b>
                    <div style={{ fontSize: 12, color: "var(--text-dim)" }} className="mono">{f.depart} → {f.arrive} · {f.duration} · {f.stops}</div>
                  </div>
                  <b style={{ color: "var(--cyan)" }}>{f.price}</b>
                </div>
              ))}
            </div>
          )}
        </GlassCard>

        {/* Current state */}
        <GlassCard>
          <SectionTitle right={<Badge tone="cyan">v{snap.version}</Badge>}>Current state</SectionTitle>
          <div className="metric-label" style={{ marginBottom: 10 }}>Current intent · {snap.intent}</div>
          <SlotRow label="Origin" value={snap.origin} />
          <SlotRow label="Destination" value={snap.destination} flipKey={snap.destination} highlight />
          <SlotRow label="Date" value={snap.date} />
          <SlotRow label="Passengers" value={String(snap.passengers)} />
          <div className="glass" style={{ borderRadius: 12, padding: "10px 13px", marginTop: 12, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span style={{ fontSize: 12, color: "var(--text-dim)" }}>Status</span>
            <Badge tone="green"><StatusDot tone="online" />&nbsp;{snap.status}</Badge>
          </div>
          <div className="metric-sub" style={{ marginTop: 12, fontSize: 12 }}>
            State is session-scoped and versioned by the backend.<br />This panel visualizes authoritative snapshots only.
          </div>
        </GlassCard>
      </div>
    </div>
  );
}

function SlotRow({ label, value, flipKey, highlight }: { label: string; value: string; flipKey?: string; highlight?: boolean }) {
  return (
    <div key={flipKey} className={`glass${highlight ? " slot-flip" : ""}`} style={{ borderRadius: 12, padding: "10px 13px", marginBottom: 8, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
      <span style={{ fontSize: 12, color: "var(--text-dim)" }}>{label}</span>
      <b style={{ fontSize: 13.5, color: highlight ? "var(--cyan)" : undefined }}>{value || "—"}</b>
    </div>
  );
}

function ExecFeed({ events }: { events: AuraEvent[] }) {
  if (events.length === 0) return <div style={{ color: "var(--text-faint)", fontSize: 13 }}>No execution yet.</div>;
  return (
    <div className="exec-feed">
      {events.filter((e) => e.type !== "USER_INPUT").map((e) => (
        <ExecRow key={e.id} e={e} />
      ))}
    </div>
  );
}

function ExecRow({ e }: { e: AuraEvent }) {
  const d = (e.detail ?? {}) as Record<string, unknown>;
  const row = (tone: string, body: React.ReactNode) => (
    <div className="exec-line"><span className={`rail-dot ${tone}`} />{body}</div>
  );
  const tech = (t: string) => <span className="exec-tech">{t}</span>;
  switch (e.type) {
    case "FAST_ACK":
      return row("ok", <span><span style={{ color: "var(--success)" }}>✓</span> <b>Fast ack</b> · acknowledgement sent <span className="mono" style={{ color: "var(--text-faint)" }}>{String(d.latencyMs ?? "")} ms</span></span>);
    case "TOOL_STARTED":
      return row("run", <span><b>Tool running</b> · <span className="mono">{String(d.tool ?? e.tool)}</span> {tech(String(e.callId ?? ""))}<br /><span style={{ color: "var(--text-dim)" }}>{String(d.route ?? "")} · v{e.stateVersion}</span></span>);
    case "INTERRUPTION":
      return row("warn", <span><span style={{ color: "var(--warning)" }}>⚡</span> <b>Interruption</b><br /><span style={{ color: "var(--text-dim)" }}>“{String(d.text ?? "")}”</span></span>);
    case "CANCEL_REQUESTED":
      return row("warn", <span>Cancel requested · {tech(String(e.callId ?? ""))}</span>);
    case "TOOL_CANCELLED":
      return row("bad", <span><span style={{ color: "var(--error)" }}>✕</span> <b>Cancelled</b> · {tech(String(e.callId ?? ""))} superseded by newer intent<br />{tech(`reason: ${String(d.reason ?? "")}`)}</span>);
    case "STATE_UPDATED":
      return row("info", <span><b>State update</b> · v{d.fromVersion as number} → v{d.toVersion as number}<br /><span style={{ color: "var(--text-dim)" }}>{String(d.slot)}: {String(d.from)} → <b style={{ color: "var(--cyan)" }}>{String(d.to)}</b></span></span>);
    case "REPLAN":
      return row("run", <span><span style={{ color: "var(--blue-soft)" }}>↻</span> <b>Re-planning</b> · <span className="mono">{String(d.tool ?? "")}</span> · {String(d.route ?? "")} {tech(String(e.callId ?? ""))}</span>);
    case "TOOL_COMPLETED":
      return row("ok", <span><span style={{ color: "var(--success)" }}>✓</span> <b>Result</b> · {tech(String(e.callId ?? ""))} completed · v{e.stateVersion}</span>);
    case "STALE_RESULT_REJECTED":
      return row("bad", <span><span style={{ color: "var(--error)" }}>⛨</span> Stale result rejected · {tech(String(e.callId ?? ""))}<br />{tech(`v${String(d.resultVersion)} vs current v${String(d.currentVersion)} — never rendered`)}</span>);
    case "FINAL_RESPONSE":
      return row("ok", <span><span style={{ color: "var(--success)" }}>●</span> <b>Final response</b> · v{e.stateVersion}</span>);
    default:
      return row("", <span>{e.summary}</span>);
  }
}
