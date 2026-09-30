import type { AuraEvent, FlightOption, SlotState } from "../types";
import type { AgentClient, EventHandler, Unsub } from "./AgentClient";

/** Real backend integration point (M2). Speaks the AURA event protocol
 *  over WebSocket; frame shapes mirror backend Event/Action dicts.
 *  Drop-in replacement for MockAgentClient — no UI changes required.
 *
 *  Outgoing frames: {kind:"text"|"interrupt"|"audio"|"image", session_id, ...}
 *  Incoming frames: {type, session_id, payload, call_id?, state_version?, ts?}
 */
export class RealAgentClient implements AgentClient {
  readonly backend = "real" as const;
  connected = false;
  private ws: WebSocket | null = null;
  private handlers = new Set<EventHandler>();
  private n = 0;
  private snapshots = new Map<string, SlotState>();
  private results = new Map<string, FlightOption[]>();
  constructor(private url = "ws://localhost:8000/ws") {}

  connect() {
    // StrictMode (or any reconnect) runs connect() again before the previous
    // socket's close event lands: every handler must therefore act only on
    // ITS OWN socket, or a stale onclose would null the live this.ws.
    const ws = new WebSocket(this.url);
    this.ws = ws;
    ws.onopen = () => { if (this.ws === ws) this.connected = true; };
    ws.onclose = () => { if (this.ws === ws) { this.connected = false; this.ws = null; } };
    ws.onmessage = (msg) => {
      if (this.ws !== ws) return; // stale socket: ignore its messages
    this.ws = new WebSocket(this.url);
    this.ws.onopen = () => { this.connected = true; };
    this.ws.onclose = () => { this.connected = false; this.ws = null; };
    this.ws.onmessage = (msg) => {
      try {
        const f = JSON.parse(msg.data as string) as Record<string, unknown>;
        const p = (f.payload ?? {}) as Record<string, unknown>;
        const e: AuraEvent = {
          id: `evt_${++this.n}`,
          ts: typeof f.ts === "number" ? f.ts : Date.now(),
          type: String(f.type ?? "USER_INPUT") as AuraEvent["type"],
          sessionId: String(f.session_id ?? ""),
          callId: f.call_id != null ? String(f.call_id) : undefined,
          stateVersion: f.state_version != null ? Number(f.state_version) : undefined,
          tool: typeof p.tool_name === "string" ? p.tool_name : undefined,
          summary: typeof p.summary === "string" ? p.summary : String(f.type ?? "event"),
          detail: p,
        };
        if (e.type === "STALE_RESULT_REJECTED") {
          // Version metadata for the execution panel: the backend transmits
          // the superseded call's version as payload.spawn_version and
          // stamps the current version on the frame as state_version, while
          // the panel renders detail.resultVersion / detail.currentVersion —
          // the exact shape MockAgentClient.ts:150 emits (drop-in parity).
          // Map the transmitted values; never invent versions.
          e.detail = {
            ...p,
            resultVersion: p.spawn_version,
            currentVersion: e.stateVersion,
          };
        }
        if (p.snapshot && typeof p.snapshot === "object") {
          this.snapshots.set(e.sessionId, p.snapshot as SlotState);
        }
        for (const h of this.handlers) h(e);
      } catch { /* malformed frame: ignore, never crash UI */ }
    };
  }
  disconnect() { this.ws?.close(); this.ws = null; this.connected = false; }
  onEvent(h: EventHandler): Unsub { this.handlers.add(h); return () => { this.handlers.delete(h); }; }

  private send(frame: Record<string, unknown>) {
    if (this.ws && this.connected) this.ws.send(JSON.stringify(frame));
  }
  sendMessage(sessionId: string, text: string) {
    this.send({ kind: "text", session_id: sessionId, text });
  }
  sendInterrupt(sessionId: string, correction?: string) {
    this.send({ kind: "interrupt", session_id: sessionId, text: correction ?? "" });
  }
  async transcribeAudio(sessionId: string, _seconds: number) {
    this.send({ kind: "audio_request", session_id: sessionId });
    return { text: "", confidence: 0 };
  }
  async analyzeImage(sessionId: string, fileName: string) {
    this.send({ kind: "image_request", session_id: sessionId, file: fileName });
    return { facts: {}, confidence: 0, verified: false };
  }
  getSnapshot(sessionId: string): SlotState {
    return this.snapshots.get(sessionId) ?? {
      origin: "—", destination: "—", date: "—", passengers: 1,
      intent: "—", version: 0, status: "CONNECTING",
    };
  }
  getResults(sessionId: string): FlightOption[] {
    return this.results.get(sessionId) ?? [];
  }
}
