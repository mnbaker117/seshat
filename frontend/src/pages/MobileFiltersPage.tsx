// Mobile-native filters page. Same data as desktop (categories,
// formats, languages from /v1/enums + /v1/settings), but tap-only
// chip interactions: tap a chip to cycle Off → Allowed → Excluded
// → Off (replaces the desktop's left/right-click split).
import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { useTheme } from "../theme";
import {
  MobileBtn,
  MobileSection,
  MobileBackButton,
} from "../components/mobile";

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

type ChipState = "off" | "allow" | "exclude";

export default function MobileFiltersPage() {
  const t = useTheme();
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

  const setField = (key: string, value: unknown) => {
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
  };

  const save = async () => {
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
      const fresh = await api.get<SettingsMap>("/v1/settings");
      setSettings(fresh);
      setDraft({});
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  const catGroups = useMemo(() => {
    const cats = enums?.categories ?? [];
    const groups: Record<string, CategoryEntry[]> = {};
    for (const c of cats) (groups[c.main_name] ??= []).push(c);
    return groups;
  }, [enums?.categories]);

  if (!enums || !settings) {
    return (
      <div style={{ padding: 40, textAlign: "center", color: t.tg }}>
        Loading…
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
  // v2.9.0: audiobook acceptance derived from the Media Type filter.
  const acceptAudiobooks =
    allowedFormats.size === 0 || allowedFormats.has("audiobooks");

  type FmtEntry = { fmt: string; enabled: boolean };
  const formatPriority =
    (effective.format_priority as Record<string, FmtEntry[]>) ?? {};

  // 3-state chip: Off → Allow → Exclude → Off. supportExclude=false
  // collapses it to Off ↔ Allow.
  const cycleChip = (
    value: string,
    allowedSet: Set<string>,
    excludedSet: Set<string>,
    allowKey: string,
    excludeKey: string | null,
  ) => {
    const isAllowed = allowedSet.has(value);
    const isExcluded = excludedSet.has(value);
    if (!isAllowed && !isExcluded) {
      // Off → Allow
      const next = new Set(allowedSet);
      next.add(value);
      setField(allowKey, [...next]);
    } else if (isAllowed && excludeKey) {
      // Allow → Exclude
      const nextAllow = new Set(allowedSet);
      nextAllow.delete(value);
      setField(allowKey, [...nextAllow]);
      const nextEx = new Set(excludedSet);
      nextEx.add(value);
      setField(excludeKey, [...nextEx]);
    } else if (isAllowed) {
      // Allow → Off (no exclude support)
      const next = new Set(allowedSet);
      next.delete(value);
      setField(allowKey, [...next]);
    } else if (isExcluded && excludeKey) {
      // Exclude → Off
      const next = new Set(excludedSet);
      next.delete(value);
      setField(excludeKey, [...next]);
    }
  };

  const stateOf = (
    value: string,
    allowed: Set<string>,
    excluded: Set<string>,
  ): ChipState =>
    allowed.has(value) ? "allow" : excluded.has(value) ? "exclude" : "off";

  const renderChip = (
    label: string,
    state: ChipState,
    onClick: () => void,
    dimmed = false,
  ) => {
    const palette = {
      off: { bg: t.bg3, fg: t.td, border: t.border },
      allow: { bg: t.abg, fg: t.accent, border: t.abr },
      exclude: { bg: t.redb, fg: t.red, border: t.redt },
    } as const;
    const c = palette[state];
    return (
      <button
        key={label}
        onClick={onClick}
        disabled={dimmed}
        style={{
          padding: "8px 12px",
          minHeight: 36,
          background: c.bg,
          color: c.fg,
          border: `1px solid ${c.border}`,
          borderRadius: 999,
          fontSize: 13,
          fontWeight: state === "allow" || state === "exclude" ? 700 : 500,
          textDecoration: state === "exclude" ? "line-through" : "none",
          cursor: dimmed ? "not-allowed" : "pointer",
          opacity: dimmed ? 0.5 : 1,
          whiteSpace: "nowrap",
        }}
      >
        {label}
      </button>
    );
  };

  const isAudiobookGroup = (mainName: string) =>
    mainName.toLowerCase().startsWith("audio");

  const dirty = Object.keys(draft).length;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <MobileBackButton to="dashboard" label="Dashboard" />

      <div>
        <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: t.text }}>
          Filters
        </h1>
        <p style={{ fontSize: 13, color: t.td, margin: "4px 0 0" }}>
          Tap a chip to cycle: Off → Allow → Exclude → Off.
        </p>
      </div>

      {error && (
        <div
          style={{
            padding: "10px 14px",
            background: t.redb,
            border: `1px solid ${t.redt}`,
            color: t.red,
            borderRadius: 10,
            fontSize: 13,
          }}
        >
          {error}
        </div>
      )}
      {ok && (
        <div
          style={{
            padding: "10px 14px",
            background: t.grnb,
            border: `1px solid ${t.grnt}`,
            color: t.grn,
            borderRadius: 10,
            fontSize: 13,
          }}
        >
          {ok}
        </div>
      )}

      <MobileSection
        title="Categories"
        subtitle={`${allowedCats.size} ebook + ${allowedAudiobookCats.size} audiobook selected`}
        defaultOpen={true}
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {Object.entries(catGroups).map(([mainName, cats]) => {
            const isAudio = isAudiobookGroup(mainName);
            const allowSet = isAudio ? allowedAudiobookCats : allowedCats;
            const allowKey = isAudio
              ? "allowed_audiobook_categories"
              : "allowed_categories";
            const dimmed = isAudio && !acceptAudiobooks;
            return (
              <div key={mainName}>
                <div
                  style={{
                    fontSize: 12,
                    color: t.tg,
                    textTransform: "uppercase",
                    fontWeight: 700,
                    letterSpacing: "0.04em",
                    marginBottom: 6,
                  }}
                >
                  {mainName}
                </div>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  {cats.map((c) =>
                    renderChip(
                      c.name,
                      stateOf(c.normalized, allowSet, excludedCats),
                      () =>
                        cycleChip(
                          c.normalized,
                          allowSet,
                          excludedCats,
                          allowKey,
                          "excluded_categories",
                        ),
                      dimmed,
                    ),
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </MobileSection>

      <MobileSection
        title="Media Type"
        count={`${allowedFormats.size}/${enums.formats.length}`}
        defaultOpen={false}
      >
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {enums.formats.map((f) =>
            renderChip(
              f,
              stateOf(f, allowedFormats, excludedFormats),
              () =>
                cycleChip(
                  f,
                  allowedFormats,
                  excludedFormats,
                  "allowed_formats",
                  "excluded_formats",
                ),
            ),
          )}
        </div>
        <p style={{ fontSize: 11, color: t.td, marginTop: 8 }}>
          Empty = accept all. Adding "audiobooks" enables audiobook
          subcategories above.
        </p>
      </MobileSection>

      <MobileSection
        title="Format Priority"
        count={
          Object.keys(formatPriority).length === 0
            ? "off"
            : `${Object.keys(formatPriority).length} types`
        }
        defaultOpen={false}
      >
        <MobileFormatPriority
          formatPriority={formatPriority}
          onChange={(next) => setField("format_priority", next)}
        />
        <p style={{ fontSize: 11, color: t.td, marginTop: 8 }}>
          Top of each list = highest priority. Enabled = grab now.
          Disabled = hold briefly for a higher-priority sibling, then
          grab if alone.
        </p>
      </MobileSection>

      <MobileSection
        title="Languages"
        count={`${allowedLangs.size}/${enums.languages.length}`}
        defaultOpen={false}
      >
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {enums.languages.map((l) =>
            renderChip(
              l,
              stateOf(l, allowedLangs, new Set()),
              () =>
                cycleChip(
                  l,
                  allowedLangs,
                  new Set(),
                  "allowed_languages",
                  null,
                ),
            ),
          )}
        </div>
      </MobileSection>

      {/* Sticky save bar */}
      {dirty > 0 && (
        <div
          style={{
            position: "sticky",
            bottom: 0,
            display: "flex",
            gap: 8,
            padding: 12,
            background: t.bg2,
            border: `1px solid ${t.border}`,
            borderRadius: 12,
            marginTop: 8,
            paddingBottom: "max(12px, env(safe-area-inset-bottom))",
          }}
        >
          <span
            style={{
              flex: 1,
              alignSelf: "center",
              fontSize: 13,
              color: t.td,
            }}
          >
            {dirty} change(s)
          </span>
          <MobileBtn variant="ghost" onClick={() => setDraft({})}>
            Discard
          </MobileBtn>
          <MobileBtn
            variant="primary"
            primary
            onClick={save}
            disabled={saving}
          >
            {saving ? "Saving…" : "Save"}
          </MobileBtn>
        </div>
      )}
    </div>
  );
}


// v2.9.0 Format Priority — mobile-native variant of the desktop
// component. Up/down arrow buttons sized for touch + an enabled
// toggle per row. One sub-card per media type.
type FmtEntry = { fmt: string; enabled: boolean };

function MobileFormatPriority({
  formatPriority,
  onChange,
}: {
  formatPriority: Record<string, FmtEntry[]>;
  onChange: (next: Record<string, FmtEntry[]>) => void;
}) {
  const t = useTheme();
  const orderedKeys = useMemo(() => {
    const known = ["ebook", "audiobook"];
    const others = Object.keys(formatPriority).filter(
      (k) => !known.includes(k),
    );
    return [...known.filter((k) => k in formatPriority), ...others];
  }, [formatPriority]);

  function updateMedia(media: string, next: FmtEntry[]) {
    onChange({ ...formatPriority, [media]: next });
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {orderedKeys.map((media) => {
        const entries = formatPriority[media] ?? [];
        return (
          <div key={media}>
            <div
              style={{
                fontSize: 12,
                color: t.tg,
                textTransform: "uppercase",
                fontWeight: 700,
                letterSpacing: "0.04em",
                marginBottom: 6,
              }}
            >
              {media} Formats
            </div>
            <MobileFormatPriorityList
              entries={entries}
              onChange={(next) => updateMedia(media, next)}
            />
          </div>
        );
      })}
    </div>
  );
}

function MobileFormatPriorityList({
  entries,
  onChange,
}: {
  entries: FmtEntry[];
  onChange: (next: FmtEntry[]) => void;
}) {
  const t = useTheme();
  const move = (i: number, dir: -1 | 1) => {
    const j = i + dir;
    if (j < 0 || j >= entries.length) return;
    const next = [...entries];
    [next[i], next[j]] = [next[j], next[i]];
    onChange(next);
  };
  const toggle = (i: number) => {
    onChange(
      entries.map((e, idx) =>
        idx === i ? { ...e, enabled: !e.enabled } : e,
      ),
    );
  };
  if (entries.length === 0) {
    return (
      <p style={{ fontSize: 12, color: t.td }}>
        No formats configured for this media type.
      </p>
    );
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      {entries.map((entry, i) => {
        const isTop = i === 0;
        return (
          <div
            key={entry.fmt}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              padding: "8px 12px",
              minHeight: 44,
              borderRadius: 8,
              background: isTop ? t.abg : t.bg3,
              border: `1px solid ${isTop ? t.abr : t.border}`,
            }}
          >
            <span
              style={{
                fontSize: 12,
                fontWeight: 700,
                color: isTop ? t.accent : t.td,
                width: 18,
              }}
            >
              {i + 1}.
            </span>
            <span
              style={{
                flex: 1,
                fontSize: 13,
                fontWeight: 500,
                color: isTop ? t.accent : t.text,
                textTransform: "uppercase",
                letterSpacing: 0.4,
              }}
            >
              {entry.fmt}
            </span>
            <label
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                fontSize: 12,
                color: t.td,
              }}
            >
              <input
                type="checkbox"
                checked={entry.enabled}
                onChange={() => toggle(i)}
                style={{ width: 18, height: 18 }}
              />
              On
            </label>
            <button
              onClick={() => move(i, -1)}
              disabled={isTop}
              style={{
                background: "none",
                border: "none",
                padding: 6,
                color: isTop ? t.tg : t.td,
                fontSize: 16,
                opacity: isTop ? 0.3 : 1,
              }}
              aria-label="Move up"
            >
              ▲
            </button>
            <button
              onClick={() => move(i, 1)}
              disabled={i === entries.length - 1}
              style={{
                background: "none",
                border: "none",
                padding: 6,
                color: i === entries.length - 1 ? t.tg : t.td,
                fontSize: 16,
                opacity: i === entries.length - 1 ? 0.3 : 1,
              }}
              aria-label="Move down"
            >
              ▼
            </button>
          </div>
        );
      })}
    </div>
  );
}
