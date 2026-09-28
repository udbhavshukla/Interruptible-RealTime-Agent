import type { SessionInfo, ToolManifest } from "../types";

export const TOOLS: ToolManifest[] = [
  {
    name: "flight_search", title: "Flight Search", kind: "read_only", version: "1.0",
    status: "Available", description: "Searches live inventory for origin, destination and date. Fully interruptible, no side effects.",
    args: [
      { name: "origin", type: "string", required: true },
      { name: "destination", type: "string", required: true },
      { name: "date", type: "string", required: true },
    ],
    returns: "{ options: FlightOption[], destination: string }",
    reversibility: "fully_interruptible", commitBoundary: false, idempotency: null,
    cancellable: true, timeoutS: 20,
  },
  {
    name: "manual_lookup", title: "Manual Lookup", kind: "read_only", version: "1.0",
    status: "Available", description: "Retrieves trusted device facts from product manuals for verification.",
    args: [{ name: "query", type: "string", required: true }],
    returns: "{ model: string, source: 'manual' }",
    reversibility: "fully_interruptible", commitBoundary: false, idempotency: null,
    cancellable: true, timeoutS: 10,
  },
  {
    name: "barcode_lookup", title: "Barcode Lookup", kind: "read_only", version: "1.0",
    status: "Available", description: "Resolves a scanned barcode to a verified device model.",
    args: [{ name: "barcode", type: "string", required: true }],
    returns: "{ model: string, source: 'barcode' }",
    reversibility: "fully_interruptible", commitBoundary: false, idempotency: null,
    cancellable: true, timeoutS: 10,
  },
  {
    name: "reserve", title: "Reserve", kind: "preparatory", version: "1.0",
    status: "Available", description: "Soft-holds an option before commit. Reversible until the commit boundary.",
    args: [
      { name: "option_id", type: "string", required: true },
      { name: "session_id", type: "string", required: false },
    ],
    returns: "{ reservation_id: string, reversible: true }",
    reversibility: "reversible_before_commit", commitBoundary: false,
    idempotency: "idempotency_key (session scope)", cancellable: true, timeoutS: 20,
  },
  {
    name: "booking", title: "Booking", kind: "state_modifying", version: "1.0",
    status: "Commit Boundary", description: "Commits a reservation. Idempotent per key; cancellation after commit requires compensation.",
    args: [
      { name: "option_id", type: "string", required: true },
      { name: "passenger", type: "string", required: true },
      { name: "idempotency_key", type: "string", required: true },
    ],
    returns: "{ booking_id: string, option_id: string, passenger: string }",
    reversibility: "requires_compensation", commitBoundary: true,
    idempotency: "idempotency_key (session scope)", cancellable: true, timeoutS: 30,
  },
  {
    name: "cancel_booking", title: "Cancel Booking", kind: "compensating", version: "1.0",
    status: "Available", description: "Compensating action that reconciles an already-committed booking.",
    args: [
      { name: "booking_id", type: "string", required: true },
      { name: "idempotency_key", type: "string", required: true },
    ],
    returns: "{ cancelled_booking: string, compensated: true }",
    reversibility: "compensating", commitBoundary: true,
    idempotency: "idempotency_key (session scope)", cancellable: false, timeoutS: 30,
  },
];

export const SESSIONS: SessionInfo[] = [
  { id: "sess_8f31", intent: "Flight Search", status: "Completed", duration: "12.4s", interruptions: 2, version: "v3", updated: "2 min ago" },
  { id: "sess_8f2e", intent: "Device Support", status: "Completed", duration: "31.8s", interruptions: 0, version: "v2", updated: "18 min ago" },
  { id: "sess_8f29", intent: "Flight Search", status: "Interrupted", duration: "8.1s", interruptions: 3, version: "v4", updated: "42 min ago" },
  { id: "sess_8f21", intent: "Booking", status: "Completed", duration: "24.6s", interruptions: 1, version: "v3", updated: "1 hr ago" },
  { id: "sess_8f1c", intent: "Flight Search", status: "Completed", duration: "9.7s", interruptions: 0, version: "v1", updated: "2 hr ago" },
  { id: "sess_8f14", intent: "Device Support", status: "Completed", duration: "44.2s", interruptions: 1, version: "v3", updated: "3 hr ago" },
];

export const KIND_META: Record<string, { label: string; cls: string }> = {
  read_only: { label: "Read Only", cls: "cyan" },
  preparatory: { label: "Preparatory", cls: "blue" },
  state_modifying: { label: "State Modifying", cls: "amber" },
  compensating: { label: "Compensating", cls: "violet" },
};
