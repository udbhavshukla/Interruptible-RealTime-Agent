import type { AuraEvent, FlightOption, SlotState } from "../types";
import type { AgentClient, EventHandler, Unsub } from "./AgentClient";

const CITIES = ["bangalore", "delhi", "mumbai", "chennai", "hyderabad", "kolkata", "goa", "jaipur", "pune", "kochi"];
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

interface ActiveCall { id: string; tool: string; timer: ReturnType<typeof setTimeout>; staleTimer?: ReturnType<typeof setTimeout>; }
interface SessionSim {
  slots: SlotState;
  active: ActiveCall | null;
  seq: number;
  results: FlightOption[];
}

function flightsFor(dest: string): FlightOption[] {
  let h = 0;
  for (const c of dest) h = (h * 31 + c.charCodeAt(0)) % 997;
  const airlines = ["Indigo", "Air India", "Vistara"];
  return [0, 1, 2].map((i) => ({
    id: `opt-${dest.slice(0, 3).toLowerCase()}-${i + 1}`,
    airline: airlines[(h + i) % 3],
    depart: `0${6 + i}:35`,
    arrive: `0${9 + i}:15`,
    duration: `${2 + (i % 2)}h ${35 - i * 5}m`,
    stops: i === 1 ? "1 stop" : "Non-stop",
    price: `₹${(5200 + ((h + i * 700) % 2600)).toLocaleString("en-IN")}`,
  }));
}

/** Deterministic demo backend. Simulates authoritative versioned events
 *  (versions, lifecycle, staleness) so the UI can be built and demoed
 *  without a live backend. */
export class MockAgentClient implements AgentClient {
  readonly backend = "mock" as const;
  connected = false;
  private handlers = new Set<EventHandler>();
  private n = 0;
  private sessions = new Map<string, SessionSim>();

  connect() { this.connected = true; }
  disconnect() {
    this.connected = false;
    for (const s of this.sessions.values()) {
      if (s.active) { clearTimeout(s.active.timer); if (s.active.staleTimer) clearTimeout(s.active.staleTimer); }
    }
  }
  onEvent(h: EventHandler): Unsub { this.handlers.add(h); return () => { this.handlers.delete(h); }; }

  private sim(id: string): SessionSim {
    let s = this.sessions.get(id);
    if (!s) {
      s = {
        slots: { origin: "Bangalore", destination: "", date: "25 Sep 2026", passengers: 1, intent: "Flight Search", version: 0, status: "READY" },
        active: null, seq: 0, results: [],
      };
      this.sessions.set(id, s);
    }
    return s;
  }

  private emit(e: Omit<AuraEvent, "id" | "ts">) {
    const full: AuraEvent = { ...e, id: `evt_${++this.n}`, ts: Date.now() };
    for (const h of this.handlers) h(full);
  }

  private parseCities(text: string): { origin?: string; destination?: string } {
    const t = text.toLowerCase();
    const found = CITIES.filter((c) => t.includes(c));
    const fromM = t.match(/from\s+([a-z]+)/);
    const toM = t.match(/to\s+([a-z]+)/);
    const pick = (m: RegExpMatchArray | null) => {
      if (!m) return undefined;
      const c = found.find((f) => f.startsWith(m[1]) || m[1].startsWith(f));
      return c ? cap(c) : undefined;
    };
    let origin = pick(fromM);
    let destination = pick(toM);
    if (!origin && !destination && found.length === 1) destination = cap(found[0]);
    if (!origin && found.length >= 2 && !toM && !fromM) { origin = cap(found[0]); destination = cap(found[1]); }
    return { origin, destination };
  }

  sendMessage(sessionId: string, text: string) {
    const s = this.sim(sessionId);
    this.emit({ type: "USER_INPUT", sessionId, summary: text, detail: { text } });
    if (s.active) { this.interrupt(sessionId, text); return; }
    const { origin, destination } = this.parseCities(text);
    if (origin) s.slots.origin = origin;
    if (destination) s.slots.destination = destination;
    if (!s.slots.destination) s.slots.destination = "Delhi";
    s.slots.version += 1;
    if (s.slots.version === 1) s.slots.status = "READY";
    const v = s.slots.version;
    const route = `${s.slots.origin} → ${s.slots.destination}`;
    window.setTimeout(() => {
      this.emit({ type: "FAST_ACK", sessionId, stateVersion: v, summary: "Acknowledgement sent", detail: { auraText: `Sure, I'm checking availability for ${route}.`, latencyMs: 142 } });
    }, 140);
    this.startTool(sessionId, s.slots.destination, 5200);
  }

  private startTool(sessionId: string, dest: string, delayMs: number) {
    const s = this.sim(sessionId);
    s.seq += 1;
    const callId = `call_${String(s.seq).padStart(3, "0")}`;
    const v = s.slots.version;
    const route = `${s.slots.origin} → ${dest}`;
    this.emit({ type: "TOOL_STARTED", sessionId, callId, stateVersion: v, tool: "flight_search", summary: `Flight search started · ${callId}`, detail: { tool: "flight_search", route, args: { ...s.slots } } });
    const timer = setTimeout(() => {
      const cur = this.sim(sessionId);
      if (!cur.active || cur.active.id !== callId) return; // superseded
      cur.active = null;
      cur.results = flightsFor(dest);
      this.emit({ type: "TOOL_COMPLETED", sessionId, callId, stateVersion: v, tool: "flight_search", summary: `${callId} completed`, detail: { tool: "flight_search", options: cur.results } });
      this.emit({ type: "FINAL_RESPONSE", sessionId, callId, stateVersion: v, summary: "Results ready", detail: { auraText: `Found ${cur.results.length} options to ${dest}. ${cur.results[0].airline} at ${cur.results[0].price} looks best.` } });
    }, delayMs);
    s.active = { id: callId, tool: "flight_search", timer };
  }

  sendInterrupt(sessionId: string, correction?: string) {
    const s = this.sim(sessionId);
    if (!s.active) {
      if (correction) this.sendMessage(sessionId, correction);
      return;
    }
    this.interrupt(sessionId, correction ?? "Actually Mumbai, not Delhi.");
  }

  private interrupt(sessionId: string, text: string) {
    const s = this.sim(sessionId);
    const old = s.active;
    if (!old) return;
    clearTimeout(old.timer);
    s.active = null;
    const oldV = s.slots.version;
    const prevDest = s.slots.destination;
    this.emit({ type: "INTERRUPTION", sessionId, summary: "User interruption detected", detail: { text, auraText: "Got it — switching right away." } });
    this.emit({ type: "CANCEL_REQUESTED", sessionId, callId: old.id, stateVersion: oldV, tool: old.tool, summary: `Cancel requested · ${old.id}`, detail: { reason: "superseded_by_new_intent" } });
    this.emit({ type: "TOOL_CANCELLED", sessionId, callId: old.id, stateVersion: oldV, tool: old.tool, summary: `${old.id} cancelled`, detail: { tool: old.tool, reason: "superseded_by_new_intent" } });
    const { destination } = this.parseCities(text);
    const nextDest = destination ?? (prevDest === "Delhi" ? "Mumbai" : "Delhi");
    s.slots.destination = nextDest;
    s.slots.version += 1;
    const v = s.slots.version;
    this.emit({ type: "STATE_UPDATED", sessionId, stateVersion: v, summary: `Destination updated · ${prevDest} → ${nextDest}`, detail: { fromVersion: oldV, toVersion: v, slot: "destination", from: prevDest, to: nextDest } });
    const route = `${s.slots.origin} → ${nextDest}`;
    this.emit({ type: "REPLAN", sessionId, stateVersion: v, tool: "flight_search", summary: `Re-planning · ${route}`, detail: { tool: "flight_search", route } });
    this.startTool(sessionId, nextDest, 4200);
    // Late arrival of the superseded result — must be rejected, never rendered.
    window.setTimeout(() => {
      this.emit({ type: "STALE_RESULT_REJECTED", sessionId, callId: old.id, stateVersion: oldV, tool: old.tool, summary: `Stale result rejected · ${old.id}`, detail: { tool: old.tool, resultVersion: oldV, currentVersion: v, reason: "version_superseded" } });
    }, 1800);
  }

  async transcribeAudio(sessionId: string, seconds: number): Promise<{ text: string; confidence: number }> {
    await new Promise((r) => setTimeout(r, 600 + seconds * 120));
    const text = "Find the device manual for this image.";
    this.emit({ type: "TRANSCRIPTION", sessionId, summary: "Voice transcribed", detail: { text, confidence: 0.94 } });
    return { text, confidence: 0.94 };
  }

  async analyzeImage(sessionId: string, fileName: string): Promise<{ facts: Record<string, string>; confidence: number; verified: boolean }> {
    await new Promise((r) => setTimeout(r, 1400));
    void fileName;
    this.emit({ type: "GROUNDING", sessionId, summary: "Visual facts grounded", detail: { facts: { device_model: "ABC-123", error_code: "E42" }, confidence: 0.88, verified: true } });
    return { facts: { device_model: "ABC-123", error_code: "E42" }, confidence: 0.88, verified: true };
  }

  getSnapshot(sessionId: string): SlotState { return { ...this.sim(sessionId).slots }; }
  getResults(sessionId: string): FlightOption[] { return [...this.sim(sessionId).results]; }
}
