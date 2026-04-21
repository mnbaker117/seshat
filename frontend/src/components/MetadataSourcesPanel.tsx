// MetadataSourcesPanel — unified per-source configuration panel.
//
// Replaces the old scattered `*_enabled` toggles + `rate_*` sliders +
// drag-sortable priority list that lived across two separate Settings
// pages. One panel, two tabs (Ebook / Audiobook), each showing:
//
//   * the ordered priority list (rank = position)
//   * per-row: two checkboxes (Enrich / Scan) + rate-limit number
//   * MAM pinned at rank 1, greyed out
//   * disabled sources (not in the priority list) shown below a
//     divider so the user can drag them in
//
// Reorder UX: up/down arrow buttons per row. Simpler than a drag-
// and-drop library; still fast enough for a 9-source list.
//
// Everything is buffered client-side until the user clicks Save —
// PUT /v1/metadata-sources replaces the whole state atomically and
// rebuilds the dispatcher so changes apply live without a restart.
import { useEffect, useState } from "react";
import { Btn } from "./Btn";
import { Spin } from "./Spin";
import { api } from "../api";
import { useTheme } from "../theme";

interface SourceEntry {
  rate_limit: number;
  ebook_enrich: boolean;
  ebook_scan: boolean;
  audiobook_enrich: boolean;
  audiobook_scan: boolean;
}

interface PriorityLists {
  ebook: string[];
  audiobook: string[];
}

interface SourceMetadata {
  name: string;
  display: string;
  available_for: string[];
  mam_only?: boolean;
}

interface PanelState {
  sources: Record<string, SourceEntry>;
  priority: PriorityLists;
}

interface GetResponse {
  state: PanelState;
  known: SourceMetadata[];
  derived: {
    ebook_enrich: string[];
    ebook_scan: string[];
    audiobook_enrich: string[];
    audiobook_scan: string[];
  };
}

type Tab = "ebook" | "audiobook";

export function MetadataSourcesPanel() {
  const t = useTheme();
  const [loaded, setLoaded] = useState<GetResponse | null>(null);
  const [draft, setDraft] = useState<PanelState | null>(null);
  const [tab, setTab] = useState<Tab>("ebook");
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    try {
      const r = await api.get<GetResponse>("/v1/metadata-sources");
      setLoaded(r);
      setDraft(JSON.parse(JSON.stringify(r.state)) as PanelState);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }

  useEffect(() => { load(); }, []);

  if (!loaded || !draft) {
    return <div style={{ display: "flex", justifyContent: "center", padding: 40 }}><Spin /></div>;
  }

  // Dirty if any field diverges from the loaded state.
  const dirty = JSON.stringify(draft) !== JSON.stringify(loaded.state);

  async function save() {
    if (!draft) return;
    setSaving(true);
    setError(null);
    setMsg(null);
    try {
      const r = await api.put<{ ok: boolean; dispatcher_rebuilt: boolean }>(
        "/v1/metadata-sources", draft,
      );
      if (r.dispatcher_rebuilt) {
        setMsg("Saved. Enricher rebuilt — changes live immediately.");
      } else {
        setMsg("Saved. Dispatcher rebuild failed; restart the container to apply.");
      }
      await load();
      setTimeout(() => setMsg(null), 5000);
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  function reset() {
    if (!loaded) return;
    setDraft(JSON.parse(JSON.stringify(loaded.state)) as PanelState);
    setMsg(null);
    setError(null);
  }

  const known = loaded.known;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <p style={{ fontSize: 13, color: t.textDim, lineHeight: 1.5, margin: 0 }}>
        Priority rank = list position (drag with arrows). Enrich runs during
        post-download metadata lookup; Scan runs during discovery-side
        library scanning. MAM is always first and free — its row is locked.
      </p>

      {error && <Banner tone="err">{error}</Banner>}
      {msg && <Banner tone="ok">{msg}</Banner>}

      <div style={{ display: "flex", gap: 4, borderBottom: `1px solid ${t.border}` }}>
        <TabBtn label="Ebook" active={tab === "ebook"} onClick={() => setTab("ebook")} />
        <TabBtn label="Audiobook" active={tab === "audiobook"} onClick={() => setTab("audiobook")} />
      </div>

      <SourceList
        tab={tab}
        draft={draft}
        setDraft={setDraft}
        known={known}
      />

      {/* Sticky save bar */}
      <div style={{
        position: "sticky", bottom: 12,
        display: "flex", justifyContent: "flex-end", gap: 10,
        background: t.bg + "ee", backdropFilter: "blur(8px)",
        padding: "12px 0", borderTop: `1px solid ${t.borderL}`, marginTop: 8,
      }}>
        <span style={{ fontSize: 13, color: t.textDim, alignSelf: "center" }}>
          {dirty ? "Unsaved changes" : "No unsaved changes"}
        </span>
        <Btn variant="ghost" disabled={!dirty || saving} onClick={reset}>
          Discard
        </Btn>
        <Btn variant="primary" disabled={!dirty || saving} onClick={save}>
          {saving ? <Spin size={14} /> : "Save"}
        </Btn>
      </div>
    </div>
  );
}

// ─── Tab button ────────────────────────────────────────────────

function TabBtn({ label, active, onClick }: {
  label: string; active: boolean; onClick: () => void;
}) {
  const t = useTheme();
  return (
    <button
      onClick={onClick}
      style={{
        padding: "10px 18px",
        fontSize: 14, fontWeight: 600,
        color: active ? t.accent : t.text2,
        background: "transparent",
        border: "none",
        borderBottom: active ? `2px solid ${t.accent}` : "2px solid transparent",
        cursor: "pointer",
        marginBottom: -1,
      }}
    >
      {label}
    </button>
  );
}

// ─── Source list ───────────────────────────────────────────────

function SourceList({ tab, draft, setDraft, known }: {
  tab: Tab;
  draft: PanelState;
  setDraft: (d: PanelState) => void;
  known: SourceMetadata[];
}) {
  const t = useTheme();
  const priority = draft.priority[tab] ?? [];
  const enrichKey = tab === "ebook" ? "ebook_enrich" : "audiobook_enrich";
  const scanKey = tab === "ebook" ? "ebook_scan" : "audiobook_scan";

  // Filter known sources down to those available for this content type.
  const availableNames = known
    .filter(k => k.available_for.includes(tab))
    .map(k => k.name);

  // Prioritised (in the ordered list) vs unprioritised (available but
  // not yet ranked).
  const prioritised = priority.filter(n => availableNames.includes(n));
  const unprioritised = availableNames.filter(n => !prioritised.includes(n));

  function move(name: string, direction: -1 | 1) {
    const list = [...priority];
    const idx = list.indexOf(name);
    if (idx < 0) return;
    const target = idx + direction;
    if (target < 0 || target >= list.length) return;
    // MAM always at rank 0 — can't displace it.
    if (list[target] === "mam") return;
    [list[idx], list[target]] = [list[target], list[idx]];
    setDraft({ ...draft, priority: { ...draft.priority, [tab]: list } });
  }

  function addToPriority(name: string) {
    const list = [...priority, name];
    setDraft({ ...draft, priority: { ...draft.priority, [tab]: list } });
  }

  function removeFromPriority(name: string) {
    const list = priority.filter(n => n !== name);
    setDraft({ ...draft, priority: { ...draft.priority, [tab]: list } });
  }

  function setToggle(name: string, key: keyof SourceEntry, value: boolean | number) {
    const entry = draft.sources[name];
    if (!entry) return;
    const next: SourceEntry = { ...entry, [key]: value };
    setDraft({
      ...draft,
      sources: { ...draft.sources, [name]: next },
    });
  }

  return (
    <div>
      <div style={{
        display: "grid",
        gridTemplateColumns: "auto 32px 1fr 80px 80px 120px 32px",
        alignItems: "center",
        gap: "8px 12px",
        fontSize: 11, fontWeight: 700,
        color: t.textDim, textTransform: "uppercase", letterSpacing: 0.5,
        padding: "8px 4px",
        borderBottom: `1px solid ${t.borderL}`,
      }}>
        <span>#</span>
        <span></span>
        <span>Source</span>
        <span style={{ textAlign: "center" }}>Enrich</span>
        <span style={{ textAlign: "center" }}>Scan</span>
        <span style={{ textAlign: "center" }}>Rate (q/s)</span>
        <span></span>
      </div>

      {prioritised.map((name, i) => (
        <SourceRow
          key={name}
          rank={i + 1}
          name={name}
          meta={known.find(k => k.name === name)!}
          entry={draft.sources[name]}
          enrichKey={enrichKey}
          scanKey={scanKey}
          onMoveUp={() => move(name, -1)}
          onMoveDown={() => move(name, 1)}
          onRemove={() => removeFromPriority(name)}
          onEnrichChange={v => setToggle(name, enrichKey, v)}
          onScanChange={v => setToggle(name, scanKey, v)}
          onRateChange={v => setToggle(name, "rate_limit", v)}
          isFirst={i === 0}
          isLast={i === prioritised.length - 1}
          locked={name === "mam"}
        />
      ))}

      {unprioritised.length > 0 && (
        <>
          <div style={{
            padding: "16px 4px 8px",
            fontSize: 11, fontWeight: 700,
            color: t.textDim, textTransform: "uppercase", letterSpacing: 0.5,
            borderBottom: `1px dashed ${t.borderL}`,
          }}>
            Disabled
          </div>
          {unprioritised.map(name => (
            <DisabledRow
              key={name}
              name={name}
              meta={known.find(k => k.name === name)!}
              entry={draft.sources[name]}
              onRateChange={v => setToggle(name, "rate_limit", v)}
              onAdd={() => addToPriority(name)}
            />
          ))}
        </>
      )}
    </div>
  );
}

// ─── Priority row ──────────────────────────────────────────────

function SourceRow({
  rank, name, meta, entry, enrichKey, scanKey,
  onMoveUp, onMoveDown, onRemove,
  onEnrichChange, onScanChange, onRateChange,
  isFirst, isLast, locked,
}: {
  rank: number;
  name: string;
  meta: SourceMetadata;
  entry: SourceEntry;
  enrichKey: keyof SourceEntry;
  scanKey: keyof SourceEntry;
  onMoveUp: () => void;
  onMoveDown: () => void;
  onRemove: () => void;
  onEnrichChange: (v: boolean) => void;
  onScanChange: (v: boolean) => void;
  onRateChange: (v: number) => void;
  isFirst: boolean;
  isLast: boolean;
  locked: boolean;
}) {
  const t = useTheme();
  const enrich = Boolean(entry?.[enrichKey]);
  const scan = Boolean(entry?.[scanKey]);
  const rate = Number(entry?.rate_limit ?? 1);
  const rowBg = locked ? t.bg3 : "transparent";
  const textColor = locked ? t.textDim : t.text;

  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "auto 32px 1fr 80px 80px 120px 32px",
      alignItems: "center",
      gap: "8px 12px",
      padding: "8px 4px",
      borderBottom: `1px solid ${t.borderL}`,
      background: rowBg,
    }}>
      <span style={{ fontSize: 13, color: t.textDim, fontWeight: 600, width: 24, textAlign: "right" }}>
        {rank}
      </span>
      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        <ArrowBtn dir="up" onClick={onMoveUp} disabled={isFirst || locked} />
        <ArrowBtn dir="down" onClick={onMoveDown} disabled={isLast || locked} />
      </div>
      <div style={{ fontSize: 14, fontWeight: 600, color: textColor, display: "flex", alignItems: "center", gap: 8 }}>
        {meta.display}
        {locked && (
          <span style={{
            fontSize: 10, fontWeight: 700, textTransform: "uppercase",
            padding: "2px 7px", borderRadius: 99,
            background: t.accent + "22", color: t.accent, letterSpacing: 0.4,
          }}>
            Always first
          </span>
        )}
      </div>
      <div style={{ display: "flex", justifyContent: "center" }}>
        <input
          type="checkbox"
          checked={enrich}
          disabled={locked}
          onChange={e => onEnrichChange(e.target.checked)}
          style={{ width: 18, height: 18, cursor: locked ? "not-allowed" : "pointer" }}
        />
      </div>
      <div style={{ display: "flex", justifyContent: "center" }}>
        <input
          type="checkbox"
          checked={scan}
          disabled={locked}
          onChange={e => onScanChange(e.target.checked)}
          style={{ width: 18, height: 18, cursor: locked ? "not-allowed" : "pointer" }}
        />
      </div>
      <div style={{ display: "flex", justifyContent: "center" }}>
        <input
          type="number"
          min={0.1}
          max={100}
          step={0.5}
          value={rate}
          onChange={e => onRateChange(parseFloat(e.target.value) || 1)}
          style={{
            width: 70, padding: "4px 8px", textAlign: "center",
            borderRadius: 6,
            border: `1px solid ${t.border}`, background: t.inp,
            color: t.text2, fontSize: 12, outline: "none",
          }}
        />
      </div>
      <div style={{ display: "flex", justifyContent: "center" }}>
        {!locked && (
          <button
            onClick={onRemove}
            title="Remove from priority (source stays disabled)"
            style={{
              background: "none", border: "none", cursor: "pointer",
              color: t.textDim, fontSize: 18, padding: 0, lineHeight: 1,
            }}
          >
            ×
          </button>
        )}
      </div>
    </div>
  );
}

// ─── Disabled row (source not in priority list) ────────────────

function DisabledRow({ name, meta, entry, onRateChange, onAdd }: {
  name: string;
  meta: SourceMetadata;
  entry: SourceEntry;
  onRateChange: (v: number) => void;
  onAdd: () => void;
}) {
  const t = useTheme();
  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "auto 32px 1fr 80px 80px 120px 32px",
      alignItems: "center",
      gap: "8px 12px",
      padding: "8px 4px",
      borderBottom: `1px solid ${t.borderL}`,
      opacity: 0.55,
    }}>
      <span style={{ fontSize: 13, color: t.textDim, fontWeight: 600, width: 24, textAlign: "right" }}>
        —
      </span>
      <span />
      <div style={{ fontSize: 14, fontWeight: 600, color: t.text2 }}>
        {meta.display}
      </div>
      <div />
      <div />
      <div style={{ display: "flex", justifyContent: "center" }}>
        <input
          type="number"
          min={0.1}
          max={100}
          step={0.5}
          value={Number(entry?.rate_limit ?? 1)}
          onChange={e => onRateChange(parseFloat(e.target.value) || 1)}
          style={{
            width: 70, padding: "4px 8px", textAlign: "center",
            borderRadius: 6,
            border: `1px solid ${t.border}`, background: t.inp,
            color: t.text2, fontSize: 12, outline: "none",
          }}
        />
      </div>
      <div style={{ display: "flex", justifyContent: "center" }}>
        <button
          onClick={onAdd}
          title={`Add ${meta.display} to this content type's priority list`}
          style={{
            background: "none", border: "none", cursor: "pointer",
            color: t.accent, fontSize: 16, padding: 0, lineHeight: 1,
            fontWeight: 700,
          }}
        >
          +
        </button>
      </div>
    </div>
  );
}

// ─── Arrow button ─────────────────────────────────────────────

function ArrowBtn({ dir, onClick, disabled }: {
  dir: "up" | "down"; onClick: () => void; disabled: boolean;
}) {
  const t = useTheme();
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{
        background: "none", border: "none",
        cursor: disabled ? "not-allowed" : "pointer",
        color: disabled ? t.borderL : t.textDim,
        padding: 0, fontSize: 10, lineHeight: 1,
      }}
    >
      {dir === "up" ? "▲" : "▼"}
    </button>
  );
}

// ─── Banner ───────────────────────────────────────────────────

function Banner({ tone, children }: { tone: "ok" | "err"; children: React.ReactNode }) {
  const t = useTheme();
  const color = tone === "ok" ? t.ok : t.err;
  return (
    <div style={{
      background: color + "22",
      border: `1px solid ${color}55`,
      color, padding: "10px 14px", borderRadius: 8, fontSize: 13,
    }}>
      {children}
    </div>
  );
}
