// FiltersPage — edit the torrent filter rules that gate every announce.
//
// Reads MAM enums from /api/v1/enums (categories, languages, formats)
// and the current filter settings from /api/v1/settings. Renders each
// filter as a multi-select checklist. Changes are collected in a local
// draft and sent as a single sparse PATCH on Save.
//
// All values are stored in their **normalized** form (lowercase,
// punctuation collapsed) so they compare correctly against live IRC
// announces. The display labels come from the enums endpoint.
import { useEffect, useState, useMemo } from "react";
import { Btn } from "../components/Btn";
import { Section } from "../components/Section";
import { Spin } from "../components/Spin";
import { api } from "../api";
import { useTheme } from "../theme";
import { useViewport } from "../hooks/useViewport";
import { useMobileCodepath } from "../components/mobile";
import MobileFiltersPage from "./MobileFiltersPage";

interface CategoryEntry {
  id: string;
  name: string;
  main_id: string;
  main_name: string;
  normalized: string;
}

interface EnumsResponse {
  categories: CategoryEntry[];
  languages: string[];
  formats: string[];
}

type SettingsMap = Record<string, unknown>;

interface PatchResponse {
  ok: boolean;
  updated: string[];
  rejected: string[];
}

export default function FiltersPage() {
  const vp = useViewport();
  if (useMobileCodepath(vp)) return <MobileFiltersPage />;
  return <DesktopFiltersPage />;
}

function DesktopFiltersPage() {
  const theme = useTheme();
  const [enums, setEnums] = useState<EnumsResponse | null>(null);
  const [settings, setSettings] = useState<SettingsMap | null>(null);
  const [draft, setDraft] = useState<SettingsMap>({});
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    Promise.all([
      api.get<EnumsResponse>("/v1/enums"),
      api.get<SettingsMap>("/v1/settings"),
    ])
      .then(([e, s]) => {
        setEnums(e);
        setSettings(s);
      })
      .catch((e) => setError(String(e)));
  }, []);

  const effective: SettingsMap = { ...(settings ?? {}), ...draft };

  function setField(key: string, value: unknown) {
    setDraft((d) => {
      const next = { ...d, [key]: value };
      if (
        settings &&
        JSON.stringify(settings[key]) === JSON.stringify(value)
      ) {
        delete next[key];
      }
      return next;
    });
    setOk(null);
  }

  async function save() {
    if (Object.keys(draft).length === 0) return;
    setSaving(true);
    setError(null);
    setOk(null);
    try {
      const r = await api.patch<PatchResponse>("/v1/settings", draft);
      if (r.rejected.length > 0) {
        setError(`Rejected: ${r.rejected.join(", ")}`);
      } else {
        setOk(`Updated ${r.updated.length} filter(s).`);
      }
      // Reload settings so the draft resets cleanly.
      const fresh = await api.get<SettingsMap>("/v1/settings");
      setSettings(fresh);
      setDraft({});
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  // Group categories by their main_name (AudioBooks, E-Books, etc.)
  // Hook must be called unconditionally (Rules of Hooks) — before
  // any early return. Safe because enums?.categories is just [].
  const catGroups = useMemo(() => {
    const cats = enums?.categories ?? [];
    const groups: Record<string, CategoryEntry[]> = {};
    for (const c of cats) {
      (groups[c.main_name] ??= []).push(c);
    }
    return groups;
  }, [enums?.categories]);

  if (!enums || !settings) {
    return (
      <div style={{ display: "flex", justifyContent: "center", padding: 40 }}>
        <Spin />
      </div>
    );
  }

  const allowedCats = new Set(
    (effective.allowed_categories as string[]) ?? [],
  );
  const allowedAudiobookCats = new Set(
    (effective.allowed_audiobook_categories as string[]) ?? [],
  );
  const excludedCats = new Set(
    (effective.excluded_categories as string[]) ?? [],
  );
  const allowedLangs = new Set(
    (effective.allowed_languages as string[]) ?? [],
  );
  const allowedFormats = new Set(
    (effective.allowed_formats as string[]) ?? [],
  );
  const excludedFormats = new Set(
    (effective.excluded_formats as string[]) ?? [],
  );
  const acceptAudiobooks = !!effective.accept_audiobook_announces;

  function toggleInSet(
    settingKey: string,
    current: Set<string>,
    value: string,
  ) {
    const next = new Set(current);
    if (next.has(value)) next.delete(value);
    else next.add(value);
    setField(settingKey, [...next]);
  }

  // Route audiobook category chips to the separate
  // `allowed_audiobook_categories` list so the user can toggle
  // audiobook acceptance at the filter level without mutating their
  // ebook category selections.
  function isAudiobookGroup(mainName: string): boolean {
    return mainName.toLowerCase().startsWith("audio");
  }

  const dirty = Object.keys(draft).length;

  return (
    <div style={{ maxWidth: 1100, margin: "0 auto" }}>
      <h1
        style={{
          fontSize: 24,
          fontWeight: 700,
          color: theme.text,
          marginBottom: 4,
        }}
      >
        Filters
      </h1>
      <p style={{ fontSize: 14, color: theme.textDim, marginBottom: 20 }}>
        Control which MAM announces pass the filter gate. Unchecked items
        are silently skipped. Changes take effect immediately after Save.
      </p>

      {error && <Banner tone="err">{error}</Banner>}
      {ok && <Banner tone="ok">{ok}</Banner>}

      {/* ── Audiobook acceptance ──────────────────────────── */}
      <Section
        title="Audiobook Announces"
        subtitle="Pipeline-side toggle. When off, audiobook IRC announces are skipped regardless of the category list below."
      >
        <label
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            fontSize: 14,
            color: theme.text,
            cursor: "pointer",
          }}
        >
          <input
            type="checkbox"
            checked={acceptAudiobooks}
            onChange={(e) =>
              setField("accept_audiobook_announces", e.target.checked)
            }
          />
          Accept audiobook announces (routes m4b/mp3 downloads to
          Audiobookshelf when an ABS library path is configured)
        </label>
      </Section>

      {/* ── Categories ─────────────────────────────────────── */}
      <Section
        title="Allowed Categories"
        subtitle={`${allowedCats.size} ebook + ${allowedAudiobookCats.size} audiobook selected. Empty = accept all that pass the format gate.`}
      >
        {Object.entries(catGroups).map(([mainName, cats]) => {
          const isAudio = isAudiobookGroup(mainName);
          const activeSet = isAudio ? allowedAudiobookCats : allowedCats;
          const settingKey = isAudio
            ? "allowed_audiobook_categories"
            : "allowed_categories";
          const dimmed = isAudio && !acceptAudiobooks;
          return (
            <div key={mainName} style={{ marginBottom: 16, opacity: dimmed ? 0.4 : 1 }}>
              <h4
                style={{
                  fontSize: 13,
                  fontWeight: 700,
                  color: theme.text2,
                  marginBottom: 8,
                  textTransform: "uppercase",
                  letterSpacing: 0.4,
                }}
              >
                {mainName}
                {isAudio && !acceptAudiobooks && (
                  <span style={{
                    marginLeft: 8, fontSize: 11, fontWeight: 500,
                    color: theme.textDim, textTransform: "none",
                    letterSpacing: 0,
                  }}>
                    (toggle audiobook announces above to enable)
                  </span>
                )}
              </h4>
              <div
                style={{
                  display: "flex",
                  flexWrap: "wrap",
                  gap: 6,
                }}
              >
                {cats.map((c) => (
                  <Chip
                    key={c.normalized}
                    label={c.name}
                    active={activeSet.has(c.normalized)}
                    excluded={excludedCats.has(c.normalized)}
                    onClick={() =>
                      toggleInSet(settingKey, activeSet, c.normalized)
                    }
                    onRightClick={() =>
                      toggleInSet(
                        "excluded_categories",
                        excludedCats,
                        c.normalized,
                      )
                    }
                  />
                ))}
              </div>
            </div>
          );
        })}
        <p style={{ fontSize: 11, color: theme.textDim, marginTop: 8 }}>
          Click to toggle allowed. Right-click to toggle excluded (red
          strike-through = excluded even if parent format is allowed).
        </p>
      </Section>

      {/* ── Formats ────────────────────────────────────────── */}
      <Section
        title="Formats"
        subtitle="Top-level format gate. Empty allowed = accept all."
      >
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {enums.formats.map((f) => (
            <Chip
              key={f}
              label={f}
              active={allowedFormats.has(f)}
              excluded={excludedFormats.has(f)}
              onClick={() =>
                toggleInSet("allowed_formats", allowedFormats, f)
              }
              onRightClick={() =>
                toggleInSet("excluded_formats", excludedFormats, f)
              }
            />
          ))}
        </div>
      </Section>

      {/* ── Languages ──────────────────────────────────────── */}
      <Section
        title="Allowed Languages"
        subtitle={`${allowedLangs.size} selected. Empty = accept all languages.`}
      >
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {enums.languages.map((l) => (
            <Chip
              key={l}
              label={l}
              active={allowedLangs.has(l)}
              onClick={() =>
                toggleInSet("allowed_languages", allowedLangs, l)
              }
            />
          ))}
        </div>
      </Section>

      {/* ── Sticky save bar ────────────────────────────────── */}
      <div
        style={{
          position: "sticky",
          bottom: 20,
          display: "flex",
          justifyContent: "flex-end",
          gap: 10,
          background: theme.bg + "ee",
          backdropFilter: "blur(8px)",
          padding: "12px 0",
          borderTop: `1px solid ${theme.borderL}`,
          marginTop: 20,
        }}
      >
        <span
          style={{
            fontSize: 13,
            color: theme.textDim,
            alignSelf: "center",
          }}
        >
          {dirty > 0
            ? `${dirty} unsaved change(s)`
            : "No unsaved changes"}
        </span>
        <Btn
          variant="ghost"
          disabled={dirty === 0 || saving}
          onClick={() => setDraft({})}
        >
          Discard
        </Btn>
        <Btn
          variant="primary"
          disabled={dirty === 0 || saving}
          onClick={save}
        >
          {saving ? <Spin size={14} /> : "Save"}
        </Btn>
      </div>
    </div>
  );
}

function Chip({
  label,
  active,
  excluded,
  onClick,
  onRightClick,
}: {
  label: string;
  active: boolean;
  excluded?: boolean;
  onClick: () => void;
  onRightClick?: () => void;
}) {
  const theme = useTheme();
  return (
    <button
      onClick={onClick}
      onContextMenu={(e) => {
        if (onRightClick) {
          e.preventDefault();
          onRightClick();
        }
      }}
      style={{
        padding: "6px 12px",
        borderRadius: 99,
        fontSize: 12,
        fontWeight: 600,
        border: `1px solid ${
          excluded
            ? theme.err + "88"
            : active
              ? theme.accent + "88"
              : theme.border
        }`,
        background: excluded
          ? theme.err + "18"
          : active
            ? theme.accent + "18"
            : "transparent",
        color: excluded
          ? theme.err
          : active
            ? theme.accent
            : theme.textDim,
        cursor: "pointer",
        textDecoration: excluded ? "line-through" : "none",
        textTransform: "capitalize",
      }}
    >
      {label}
    </button>
  );
}

function Banner({
  tone,
  children,
}: {
  tone: "ok" | "err";
  children: React.ReactNode;
}) {
  const theme = useTheme();
  const color = tone === "ok" ? theme.ok : theme.err;
  return (
    <div
      style={{
        background: color + "22",
        border: `1px solid ${color}55`,
        color,
        padding: "10px 14px",
        borderRadius: 8,
        fontSize: 13,
        marginBottom: 16,
      }}
    >
      {children}
    </div>
  );
}
