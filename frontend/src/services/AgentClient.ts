import type { AuraEvent, FlightOption, SlotState } from "../types";

/** Unsubscribe function */
export type Unsub = () => void;
export type EventHandler = (e: AuraEvent) => void;

/** Frontend service abstraction. The UI renders authoritative events;
 *  it never invents versions, lifecycle, or staleness. */
export interface AgentClient {
  readonly backend: "mock" | "real";
  connect(): void;
  disconnect(): void;
  readonly connected: boolean;
  onEvent(h: EventHandler): Unsub;
  sendMessage(sessionId: string, text: string): void;
  sendInterrupt(sessionId: string, correction?: string): void;
  transcribeAudio(sessionId: string, seconds: number): Promise<{ text: string; confidence: number }>;
  analyzeImage(sessionId: string, fileName: string): Promise<{
    facts: Record<string, string>; confidence: number; verified: boolean;
  }>;
  getSnapshot(sessionId: string): SlotState;
  getResults(sessionId: string): FlightOption[];
}
