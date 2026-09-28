import { useAura } from "../store/AuraContext";
import { useTheme } from "../theme/ThemeContext";
import type { ThemeMode } from "../theme/ThemeContext";
import { GlassCard, PageHeader, Toggle } from "../components/ui";

export default function Settings() {
  const { settings, setSettings } = useAura();
  const { mode, setMode } = useTheme();
  const Row = ({ label, sub, control }: { label: string; sub: string; control: React.ReactNode }) => (
    <div style={{ display: "flex", gap: 14, alignItems: "center", padding: "13px 2px", borderBottom: "1px dashed var(--color-line)" }}>
      <div style={{ flex: 1 }}>
        <b style={{ fontSize: 13.5 }}>{label}</b>
        <div style={{ fontSize: 12.5, color: "var(--text-dim)" }}>{sub}</div>
      </div>
      {control}
    </div>
  );
  return (
    <div className="page" style={{ maxWidth: 860 }}>
      <PageHeader title="Settings" sub="Local UI preferences — no backend calls." />
      <div className="cols-2">
        <GlassCard>
          <div className="metric-label" style={{ marginBottom: 6 }}>Appearance</div>
          <div style={{ padding: "13px 2px", borderBottom: "1px dashed var(--color-line)" }}>
            <b style={{ fontSize: 13.5 }}>Theme</b>
            <div style={{ fontSize: 12.5, color: "var(--text-dim)", marginBottom: 10 }}>Dark · Light · follow system. Saved automatically.</div>
            <div role="radiogroup" aria-label="Theme" style={{ display: "flex", gap: 8 }}>
              {(["light", "dark", "system"] as ThemeMode[]).map((m) => (
                <button
                  key={m}
                  role="radio"
                  aria-checked={mode === m}
                  onClick={() => setMode(m)}
                  className="btn btn-sm"
                  style={mode === m
                    ? { background: "var(--color-accent)", borderColor: "var(--color-accent)", color: "var(--color-accent-fg)" }
                    : undefined}
                >
                  {m === "light" ? "☀ Light" : m === "dark" ? "☾ Dark" : "◐ System"}
                </button>
              ))}
            </div>
          </div>
          <Row label="Theme" sub="Midnight glass (fixed for demo)" control={<span className="badge blue">Midnight</span>} />
          <div style={{ padding: "13px 2px", borderBottom: "1px dashed var(--color-line)" }}>
            <b style={{ fontSize: 13.5 }}>Glass intensity</b>
            <div style={{ fontSize: 12.5, color: "var(--text-dim)", marginBottom: 8 }}>Translucency of glass surfaces</div>
            <input type="range" min={20} max={100} value={settings.glassIntensity} onChange={(e) => setSettings({ glassIntensity: Number(e.target.value) })} style={{ width: "100%", accentColor: "var(--color-accent)" }} />
          </div>
          <Row label="Animations" sub="Page, hover and state transitions" control={<Toggle on={settings.animations} onChange={(v) => setSettings({ animations: v })} />} />
          <Row label="Compact mode" sub="Denser cards and tables" control={<Toggle on={settings.compact} onChange={(v) => setSettings({ compact: v })} />} />
        </GlassCard>
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <GlassCard>
            <div className="metric-label" style={{ marginBottom: 6 }}>Agent behavior</div>
            <Row label="Demo mode" sub="Auto-play scripted interruption scenario" control={<Toggle on={settings.demoMode} onChange={(v) => setSettings({ demoMode: v })} />} />
            <Row label="Verbose events" sub="Show filler and ack events in Activity" control={<Toggle on={settings.verboseEvents} onChange={(v) => setSettings({ verboseEvents: v })} />} />
          </GlassCard>
          <GlassCard>
            <div className="metric-label" style={{ marginBottom: 6 }}>Connection</div>
            <Row label="Mock backend" sub="Deterministic local simulation" control={<Toggle on={settings.mockBackend} onChange={(v) => setSettings({ mockBackend: v })} />} />
            <Row label="WebSocket URL" sub="Used when mock backend is off" control={<span className="mono" style={{ fontSize: 11.5, color: "var(--text-dim)" }}>ws://localhost:8000/ws</span>} />
          </GlassCard>
          <GlassCard>
            <div className="metric-label" style={{ marginBottom: 6 }}>Developer</div>
            <Row label="Debug trace" sub="Attach raw payloads to Activity rows" control={<Toggle on={settings.debugTrace} onChange={(v) => setSettings({ debugTrace: v })} />} />
          </GlassCard>
        </div>
      </div>
    </div>
  );
}
