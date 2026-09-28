import React from "react";
import { useAura } from "../store/AuraContext";
import { Badge, GlassCard, PageHeader, SectionTitle, StatusDot } from "../components/ui";

const SID = "sess_multi";

export default function Multimodal() {
  const { client } = useAura();
  const [recording, setRecording] = React.useState(false);
  const [recSecs, setRecSecs] = React.useState(0);
  const [transcript, setTranscript] = React.useState<{ text: string; confidence: number } | null>(null);
  const [img, setImg] = React.useState<string | null>(null);
  const [analyzing, setAnalyzing] = React.useState(false);
  const [grounding, setGrounding] = React.useState<{ facts: Record<string, string>; confidence: number; verified: boolean } | null>(null);
  const timer = React.useRef<number | null>(null);

  const toggleRec = () => {
    if (recording) {
      if (timer.current) window.clearInterval(timer.current);
      setRecording(false);
      client.transcribeAudio(SID, recSecs).then(setTranscript);
    } else {
      setTranscript(null);
      setRecSecs(0);
      setRecording(true);
      timer.current = window.setInterval(() => setRecSecs((s) => s + 1), 1000);
    }
  };
  React.useEffect(() => () => { if (timer.current) window.clearInterval(timer.current); }, []);

  const onFile = (f: File | undefined) => {
    if (!f) return;
    setGrounding(null);
    const reader = new FileReader();
    reader.onload = () => {
      setImg(String(reader.result));
      setAnalyzing(true);
      client.analyzeImage(SID, f.name).then((g) => { setGrounding(g); setAnalyzing(false); });
    };
    reader.readAsDataURL(f);
  };

  return (
    <div className="page">
      <PageHeader title="Multimodal Grounding" sub="Process voice and visual input · perception proposes, verification commits" />
      <div className="cols-2">
        <GlassCard>
          <SectionTitle right={recording ? <Badge tone="red"><StatusDot tone="bad" />&nbsp;Listening</Badge> : <Badge>Idle</Badge>}>Voice</SectionTitle>
          <div className="glass" style={{ borderRadius: 14, padding: 22, textAlign: "center" }}>
            <button onClick={toggleRec} aria-label={recording ? "Stop recording" : "Start recording"} style={{
              width: 76, height: 76, borderRadius: "50%", border: "1.5px solid var(--color-accent)",
              background: recording ? "var(--color-accent)" : "color-mix(in srgb, var(--color-accent) 12%, transparent)",
              fontSize: 28, cursor: "pointer", color: "var(--text)",
              boxShadow: recording ? "0 0 34px color-mix(in srgb, var(--color-accent) 55%, transparent)" : "0 0 22px color-mix(in srgb, var(--color-accent) 22%, transparent)",
            }}>🎙</button>
            <div style={{ marginTop: 12 }}>
              {recording ? (
                <><div className="wave" style={{ justifyContent: "center" }}>{Array.from({ length: 24 }).map((_, i) => <i key={i} style={{ animationDelay: `${i * 0.07}s` }} />)}</div>
                <div className="mono" style={{ marginTop: 8, fontSize: 13 }}>00:{String(recSecs).padStart(2, "0")} · LISTENING</div></>
              ) : (
                <div style={{ color: "var(--text-faint)", fontSize: 13 }}>Tap the microphone to dictate</div>
              )}
            </div>
          </div>
          <div style={{ marginTop: 14 }}>
            <div className="metric-label" style={{ marginBottom: 8 }}>Transcription</div>
            {!transcript && <div style={{ color: "var(--text-faint)", fontSize: 13 }}>No transcription yet.</div>}
            {transcript && (
              <div className="glass" style={{ borderRadius: 12, padding: 14 }}>
                <div style={{ fontSize: 14, fontStyle: "italic" }}>"{transcript.text}"</div>
                <div style={{ display: "flex", gap: 8, marginTop: 12, alignItems: "center" }}>
                  <Badge tone="green">Confidence {Math.round(transcript.confidence * 100)}%</Badge>
                  <Badge tone="cyan">Verified</Badge>
                </div>
              </div>
            )}
          </div>
        </GlassCard>

        <GlassCard>
          <SectionTitle right={analyzing ? <Badge tone="blue">Processing…</Badge> : grounding ? <Badge tone="green">Grounded</Badge> : <Badge>Idle</Badge>}>Vision</SectionTitle>
          <label
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => { e.preventDefault(); onFile(e.dataTransfer.files?.[0]); }}
            style={{ display: "block", border: "1.5px dashed var(--color-border-strong)", borderRadius: 14, padding: img ? 12 : 34, textAlign: "center", cursor: "pointer", transition: "border-color 0.2s" }}>
            {img ? (
              <img src={img} alt="upload" style={{ maxWidth: "100%", maxHeight: 220, borderRadius: 10 }} />
            ) : (
              <div><div style={{ fontSize: 30 }}>🖼</div><b style={{ fontSize: 14 }}>Drop an image here</b><div style={{ color: "var(--text-faint)", fontSize: 12.5, marginTop: 4 }}>or click to upload (PNG)</div></div>
            )}
            <input type="file" accept="image/*" style={{ display: "none" }} onChange={(e) => onFile(e.target.files?.[0])} />
          </label>
          <div style={{ marginTop: 14 }}>
            <div className="metric-label" style={{ marginBottom: 8 }}>Detected information</div>
            {analyzing && <div style={{ display: "flex", gap: 10, alignItems: "center", fontSize: 13, color: "var(--text-dim)" }}><span className="spin" /> Analyzing pixels…</div>}
            {!analyzing && !grounding && <div style={{ color: "var(--text-faint)", fontSize: 13 }}>Upload an image to ground visual facts.</div>}
            {grounding && (
              <div className="glass" style={{ borderRadius: 12, padding: 14 }}>
                {Object.entries(grounding.facts).map(([k, v]) => (
                  <div key={k} style={{ display: "flex", justifyContent: "space-between", fontSize: 13, padding: "6px 0", borderBottom: "1px dashed var(--color-line)" }}>
                    <span className="mono" style={{ color: "var(--text-dim)" }}>{k}</span><b className="mono">{v}</b>
                  </div>
                ))}
                <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
                  <Badge tone="green">Confidence {Math.round(grounding.confidence * 100)}%</Badge>
                  <Badge tone={grounding.verified ? "cyan" : "amber"}>{grounding.verified ? "Verified — safe to commit" : "Needs review"}</Badge>
                </div>
              </div>
            )}
          </div>
        </GlassCard>
      </div>
    </div>
  );
}
