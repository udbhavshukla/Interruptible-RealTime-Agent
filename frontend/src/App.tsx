import React from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { AuraProvider, useAura } from "./store/AuraContext";
import { CommandPalette, MobileNav, Sidebar, TopBar } from "./components/Shell";
import Overview from "./pages/Overview";
import LiveAgent from "./pages/LiveAgent";
import Sessions from "./pages/Sessions";
import Activity from "./pages/Activity";
import Tools from "./pages/Tools";
import Multimodal from "./pages/Multimodal";
import Analytics from "./pages/Analytics";
import System from "./pages/System";
import Settings from "./pages/Settings";

const TONE_DOT: Record<string, string> = { blue: "var(--color-accent)", green: "var(--color-success)", amber: "var(--color-warning)", red: "var(--color-error)", cyan: "var(--color-info)" };

function Shell() {
  const [collapsed, setCollapsed] = React.useState(false);
  const [palette, setPalette] = React.useState(false);
  const { toasts, dismissToast } = useAura();
  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalette((p) => !p);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return (
    <div className="shell">
      <Sidebar collapsed={collapsed} onToggle={() => setCollapsed((c) => !c)} />
      <div className="main">
        <TopBar onPalette={() => setPalette(true)} onToggleSidebar={() => setCollapsed((c) => !c)} />
        <div className="content">
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/agent" element={<LiveAgent />} />
            <Route path="/sessions" element={<Sessions />} />
            <Route path="/activity" element={<Activity />} />
            <Route path="/tools" element={<Tools />} />
            <Route path="/multimodal" element={<Multimodal />} />
            <Route path="/analytics" element={<Analytics />} />
            <Route path="/system" element={<System />} />
            <Route path="/settings" element={<Settings />} />
          </Routes>
        </div>
      </div>
      <MobileNav />
      <CommandPalette open={palette} onClose={() => setPalette(false)} />
      <div className="toasts">
        {toasts.map((t) => (
          <div key={t.id} className="glass toast">
            <span className="dot" style={{ background: TONE_DOT[t.tone], boxShadow: `0 0 8px ${TONE_DOT[t.tone]}`, marginTop: 4 }} />
            <div style={{ flex: 1 }}><b>{t.title}</b><span>{t.body}</span></div>
            <button className="btn btn-ghost btn-sm" onClick={() => dismissToast(t.id)}>✕</button>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AuraProvider>
        <Shell />
      </AuraProvider>
    </BrowserRouter>
  );
}
