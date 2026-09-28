import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { AuraEvent, Toast } from "../types";
import { MockAgentClient } from "../services/MockAgentClient";
import type { AgentClient } from "../services/AgentClient";

export interface Settings {
  glassIntensity: number;
  animations: boolean;
  compact: boolean;
  demoMode: boolean;
  mockBackend: boolean;
  debugTrace: boolean;
  verboseEvents: boolean;
}

interface AuraCtx {
  client: AgentClient;
  connected: boolean;
  events: AuraEvent[];
  toasts: Toast[];
  pushToast: (t: Omit<Toast, "id">) => void;
  dismissToast: (id: string) => void;
  settings: Settings;
  setSettings: (s: Partial<Settings>) => void;
  clearEvents: () => void;
}

const Ctx = createContext<AuraCtx | null>(null);
let toastN = 0;

const TOASTABLE: Partial<Record<AuraEvent["type"], { title: string; tone: Toast["tone"] }>> = {
  TOOL_STARTED: { title: "Tool started", tone: "blue" },
  INTERRUPTION: { title: "User interruption detected", tone: "amber" },
  TOOL_CANCELLED: { title: "Previous operation cancelled", tone: "amber" },
  STATE_UPDATED: { title: "State updated", tone: "cyan" },
  TOOL_COMPLETED: { title: "Tool completed", tone: "green" },
  STALE_RESULT_REJECTED: { title: "Stale result rejected", tone: "red" },
  FINAL_RESPONSE: { title: "New result available", tone: "green" },
};

export function AuraProvider({ children }: { children: React.ReactNode }) {
  const [client] = useState<AgentClient>(() => new MockAgentClient());
  const [connected, setConnected] = useState(false);
  const [events, setEvents] = useState<AuraEvent[]>([]);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [settings, setSettingsState] = useState<Settings>({
    glassIntensity: 60, animations: true, compact: false,
    demoMode: true, mockBackend: true, debugTrace: false, verboseEvents: true,
  });

  const pushToast = useCallback((t: Omit<Toast, "id">) => {
    const id = `toast_${++toastN}`;
    setToasts((prev) => [...prev.slice(-3), { ...t, id }]);
    window.setTimeout(() => setToasts((prev) => prev.filter((x) => x.id !== id)), 4200);
  }, []);
  const dismissToast = useCallback((id: string) => setToasts((p) => p.filter((x) => x.id !== id)), []);
  const setSettings = useCallback((s: Partial<Settings>) => setSettingsState((p) => ({ ...p, ...s })), []);
  const clearEvents = useCallback(() => setEvents([]), []);

  useEffect(() => {
    client.connect();
    setConnected(true);
    const unsub = client.onEvent((e) => {
      setEvents((prev) => [...prev.slice(-399), e]);
      const meta = TOASTABLE[e.type];
      if (meta) pushToast({ title: meta.title, body: e.summary, tone: meta.tone });
    });
    return () => { unsub(); client.disconnect(); };
  }, [client, pushToast]);

  const value = useMemo(
    () => ({ client, connected, events, toasts, pushToast, dismissToast, settings, setSettings, clearEvents }),
    [client, connected, events, toasts, pushToast, dismissToast, settings, setSettings, clearEvents]
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAura(): AuraCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useAura outside provider");
  return c;
}
