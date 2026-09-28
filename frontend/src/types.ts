/* Frontend mirror of the AURA protocol (backend remains source of truth).
   The UI visualizes authoritative backend events; it never invents
   versions, lifecycle, or staleness. */

export type AuraEventType =
  | "USER_INPUT" | "FAST_ACK" | "TOOL_STARTED" | "INTERRUPTION"
  | "CANCEL_REQUESTED" | "TOOL_CANCELLED" | "STATE_UPDATED" | "REPLAN"
  | "TOOL_COMPLETED" | "STALE_RESULT_REJECTED" | "FINAL_RESPONSE"
  | "CLARIFICATION" | "FILLER" | "TRANSCRIPTION" | "GROUNDING";

export interface AuraEvent {
  id: string;
  ts: number;
  type: AuraEventType;
  sessionId: string;
  callId?: string;
  stateVersion?: number;
  tool?: string;
  summary: string;
  detail?: Record<string, unknown>;
}

export interface ChatMessage {
  id: string;
  role: "user" | "aura";
  text: string;
  ts: number;
}

export interface SlotState {
  origin: string;
  destination: string;
  date: string;
  passengers: number;
  intent: string;
  version: number;
  status: string;
}

export type ToolKind = "read_only" | "preparatory" | "state_modifying" | "compensating";

export interface ToolManifest {
  name: string;
  title: string;
  kind: ToolKind;
  version: string;
  status: string;
  description: string;
  args: { name: string; type: string; required: boolean }[];
  returns: string;
  reversibility: string;
  commitBoundary: boolean;
  idempotency: string | null;
  cancellable: boolean;
  timeoutS: number;
}

export interface SessionInfo {
  id: string;
  intent: string;
  status: "Active" | "Completed" | "Interrupted";
  duration: string;
  interruptions: number;
  version: string;
  updated: string;
}

export interface Toast {
  id: string;
  title: string;
  body: string;
  tone: "blue" | "green" | "amber" | "red" | "cyan";
}

export interface FlightOption {
  id: string;
  airline: string;
  depart: string;
  arrive: string;
  duration: string;
  stops: string;
  price: string;
}
