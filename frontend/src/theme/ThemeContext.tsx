import React from "react";

export type ThemeMode = "dark" | "light" | "system";
export type ResolvedTheme = "dark" | "light";

const KEY = "aura-theme";

function resolve(mode: ThemeMode): ResolvedTheme {
  if (mode !== "system" || typeof window === "undefined") return mode === "light" ? "light" : "dark";
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

interface ThemeCtx {
  mode: ThemeMode;
  resolved: ResolvedTheme;
  setMode: (m: ThemeMode) => void;
}

const Ctx = React.createContext<ThemeCtx>({ mode: "dark", resolved: "dark", setMode: () => undefined });

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [mode, setModeState] = React.useState<ThemeMode>(() => {
    try {
      const v = window.localStorage.getItem(KEY);
      return v === "light" || v === "system" ? v : "dark";
    } catch {
      return "dark";
    }
  });
  const [resolved, setResolved] = React.useState<ResolvedTheme>(() => resolve(
    (() => {
      try {
        const v = window.localStorage.getItem(KEY);
        return (v === "light" || v === "system" ? v : "dark") as ThemeMode;
      } catch {
        return "dark" as ThemeMode;
      }
    })()
  ));

  React.useEffect(() => {
    setResolved(resolve(mode));
    try {
      window.localStorage.setItem(KEY, mode);
    } catch { /* private mode: theme simply won't persist */ }
  }, [mode]);

  React.useEffect(() => {
    document.documentElement.dataset.theme = resolved;
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", resolved === "light" ? "#F5F7F2" : "#06110D");
  }, [resolved]);

  React.useEffect(() => {
    if (mode !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: light)");
    const onChange = () => setResolved(mq.matches ? "light" : "dark");
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [mode]);

  const setMode = React.useCallback((m: ThemeMode) => setModeState(m), []);
  const value = React.useMemo(() => ({ mode, resolved, setMode }), [mode, resolved, setMode]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useTheme(): ThemeCtx {
  return React.useContext(Ctx);
}
