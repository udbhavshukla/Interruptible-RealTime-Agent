import React from "react";
import { KIND_META, TOOLS } from "../data/catalog";
import { useAura } from "../store/AuraContext";
import type { ToolManifest } from "../types";
import { Badge, GlassCard, JsonView, Modal, PageHeader, SectionTitle } from "../components/ui";

export default function Tools() {
  const { client, pushToast } = useAura();
  const [sel, setSel] = React.useState<ToolManifest | null>(null);
  const [testing, setTesting] = React.useState<ToolManifest | null>(null);
  const [testOut, setTestOut] = React.useState<string | null>(null);

  const testTool = (t: ToolManifest) => {
    setTesting(t);
    setTestOut(null);
    window.setTimeout(() => {
      if (t.name === "flight_search") {
        const opts = client.getResults("sess_live");
        setTestOut(opts.length ? `${opts.length} options returned (deterministic mock).` : "No cached results — run the Live Agent demo first, then re-test.");
      } else if (t.name === "booking") {
        setTestOut("Dry-run OK · commit boundary armed · idempotency key required (mock backend, no real charge).");
      } else {
        setTestOut(`Dry-run OK · ${t.timeoutS}s timeout · ${t.cancellable ? "cancellable" : "non-cancellable"} (mock backend).`);
      }
      pushToast({ title: `${t.title} tested`, body: "Mock backend responded deterministically.", tone: "green" });
    }, 900);
  };

  return (
    <div className="page">
      <PageHeader title="Tool Registry" sub="Schema-driven tools available to AURA · mock backend" />
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))" }}>
        {TOOLS.map((t) => (
          <GlassCard key={t.name} hover>
            <div style={{ display: "flex", gap: 12, alignItems: "flex-start" }}>
              <span style={{ width: 40, height: 40, borderRadius: 12, background: "color-mix(in srgb, var(--color-accent) 14%, transparent)", border: "1px solid var(--color-border-strong)", color: "var(--color-accent-ink)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 18, flex: "none" }}>
                {t.kind === "read_only" ? "◎" : t.kind === "preparatory" ? "◍" : t.kind === "state_modifying" ? "⬢" : "↩"}
              </span>
              <div style={{ flex: 1 }}>
                <b style={{ fontSize: 14.5 }}>{t.title}</b>
                <div className="mono" style={{ fontSize: 11.5, color: "var(--text-faint)" }}>{t.name} · v{t.version}</div>
              </div>
            </div>
            <p style={{ fontSize: 13, color: "var(--text-dim)", lineHeight: 1.55, minHeight: 60 }}>{t.description}</p>
            <div style={{ display: "flex", gap: 7, flexWrap: "wrap", marginBottom: 14 }}>
              <Badge tone={KIND_META[t.kind].cls}>{KIND_META[t.kind].label}</Badge>
              <Badge tone={t.status === "Commit Boundary" ? "amber" : "green"}>{t.status}</Badge>
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <button className="btn btn-sm" style={{ flex: 1, justifyContent: "center" }} onClick={() => setSel(t)}>View Schema</button>
              <button className="btn btn-sm btn-primary" style={{ flex: 1, justifyContent: "center" }} onClick={() => testTool(t)}>Test Tool</button>
            </div>
          </GlassCard>
        ))}
      </div>
      {sel && (
        <Modal title={`${sel.title} · schema`} onClose={() => setSel(null)}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 12, fontSize: 12.5 }}>
            <SchemaKV k="version" v={sel.version} />
            <SchemaKV k="kind" v={sel.kind} />
            <SchemaKV k="reversibility" v={sel.reversibility} />
            <SchemaKV k="commit boundary" v={sel.commitBoundary ? "YES" : "no"} />
            <SchemaKV k="idempotency" v={sel.idempotency ?? "—"} />
            <SchemaKV k="cancellable" v={sel.cancellable ? "yes" : "NO"} />
            <SchemaKV k="timeout" v={`${sel.timeoutS}s`} />
            <SchemaKV k="status" v={sel.status} />
          </div>
          <SectionTitle>Arguments</SectionTitle>
          <JsonView data={Object.fromEntries(sel.args.map((a) => [a.name, `${a.type}${a.required ? " (required)" : ""}`]))} />
          <div style={{ height: 12 }} />
          <SectionTitle>Returns</SectionTitle>
          <JsonView data={sel.returns} />
        </Modal>
      )}
      {testing && (
        <Modal title={`Test ${testing.title}`} onClose={() => setTesting(null)}>
          <p style={{ fontSize: 13, color: "var(--text-dim)" }}>Executing dry-run against the mock backend…</p>
          {testOut === null ? <div className="spin" /> : <div className="glass" style={{ borderRadius: 12, padding: 14, fontSize: 13 }}><span style={{ color: "var(--success)" }}>✓ </span>{testOut}</div>}
        </Modal>
      )}
    </div>
  );
}

function SchemaKV({ k, v }: { k: string; v: string }) {
  return <div className="glass" style={{ borderRadius: 10, padding: "8px 11px" }}><div className="metric-label" style={{ fontSize: 10 }}>{k}</div><div className="mono" style={{ fontSize: 12, marginTop: 3 }}>{v}</div></div>;
}
