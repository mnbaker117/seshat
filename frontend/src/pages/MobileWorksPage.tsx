// Mobile-native works page — cross-library ebook↔audiobook link
// browser. Search, source filter, rebuild action, list of work
// cards with per-member rows and unlink buttons. Per-author
// preferences live in a collapsed section at the bottom.
import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { useTheme } from "../theme";
import { toast } from "../lib/toast";
import { Ic } from "../icons";
import {
  MobileBtn,
  MobileChip,
  MobileSection,
  MobileInput,
  MobileBackButton,
  MobileBadge,
} from "../components/mobile";

interface WorkLinkOut {
  id: number;
  work_id: string;
  library_slug: string;
  book_id: number;
  content_type: string;
  link_source: string;
  title: string | null;
  author_name: string | null;
  cover_url: string | null;
  series_name: string | null;
  series_index: number | null;
}

interface WorkSummary {
  work_id: string;
  links: WorkLinkOut[];
}

interface WorksListResponse {
  total: number;
  items: WorkSummary[];
}

interface AuthorPref {
  normalized_name: string;
  display_name: string;
  tracking_mode: string;
}

interface RebuildResult {
  works_created: number;
  links_added: number;
  links_skipped_manual: number;
  stale_auto_removed: number;
  orphans_pruned: number;
  total_bucketed: number;
}

type SourceFilter = "all" | "auto" | "manual";

const TRACKING_MODES = ["combined", "ebook", "audiobook"];

export default function MobileWorksPage() {
  const t = useTheme();
  const [works, setWorks] = useState<WorkSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<SourceFilter>("all");
  const [rebuilding, setRebuilding] = useState(false);
  const [prefs, setPrefs] = useState<AuthorPref[]>([]);
  const [newPrefName, setNewPrefName] = useState("");
  const [newPrefMode, setNewPrefMode] = useState("combined");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.get<WorksListResponse>("/v1/works?limit=500");
      setWorks(r.items);
      setTotal(r.total);
    } catch (e) {
      toast.error(`Failed: ${e}`);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadPrefs = async () => {
    try {
      const r = await api.get<AuthorPref[]>("/v1/works/author-preferences");
      setPrefs(r);
    } catch { /* ignore */ }
  };

  useEffect(() => {
    load();
    loadPrefs();
  }, [load]);

  const rebuild = async () => {
    if (!confirm("Rebuild work links across all libraries?")) return;
    setRebuilding(true);
    try {
      const r = await api.post<RebuildResult>("/v1/works/rebuild");
      toast.success(
        `Rebuilt: ${r.works_created} works, ${r.links_added} links`,
      );
      await load();
    } catch (e) {
      toast.error(`Rebuild failed: ${e}`);
    } finally {
      setRebuilding(false);
    }
  };

  const unlink = async (link: WorkLinkOut) => {
    if (!confirm(`Unlink "${link.title || link.book_id}" from this work?`))
      return;
    try {
      await api.del(`/v1/works/link/${link.library_slug}/${link.book_id}`);
      await load();
    } catch (e) {
      toast.error(`Unlink failed: ${e}`);
    }
  };

  const setPrefMode = async (name: string, mode: string) => {
    try {
      await api.put(`/v1/works/author-preferences/${encodeURIComponent(name)}`, {
        tracking_mode: mode,
      });
      await loadPrefs();
    } catch (e) {
      toast.error(`Failed: ${e}`);
    }
  };

  const removePref = async (name: string) => {
    if (!confirm(`Clear preference for ${name}?`)) return;
    try {
      await api.del(
        `/v1/works/author-preferences/${encodeURIComponent(name)}`,
      );
      await loadPrefs();
    } catch (e) {
      toast.error(`Failed: ${e}`);
    }
  };

  const addPref = async () => {
    if (!newPrefName.trim()) return;
    await setPrefMode(newPrefName.trim(), newPrefMode);
    setNewPrefName("");
    setNewPrefMode("combined");
  };

  const filtered = works.filter((w) => {
    if (filter !== "all") {
      const hasMatch = w.links.some((l) => l.link_source === filter);
      if (!hasMatch) return false;
    }
    if (!search) return true;
    const s = search.toLowerCase();
    return w.links.some(
      (l) =>
        (l.title || "").toLowerCase().includes(s) ||
        (l.author_name || "").toLowerCase().includes(s),
    );
  });

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <MobileBackButton to="dashboard" label="Dashboard" />

      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          gap: 8,
        }}
      >
        <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: t.text }}>
          Works
        </h1>
        <span style={{ fontSize: 13, color: t.td }}>
          {loading ? "…" : `${total} total`}
        </span>
      </div>

      <MobileInput
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder="Search title or author"
        leadingIcon={Ic.search}
      />

      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {(["all", "auto", "manual"] as SourceFilter[]).map((f) => (
          <MobileChip
            key={f}
            active={filter === f}
            onClick={() => setFilter(f)}
          >
            {f.charAt(0).toUpperCase() + f.slice(1)}
          </MobileChip>
        ))}
        <MobileBtn
          variant="secondary"
          onClick={rebuild}
          disabled={rebuilding}
          style={{ minHeight: 36, fontSize: 13 }}
        >
          {rebuilding ? "Rebuilding…" : "Rebuild"}
        </MobileBtn>
      </div>

      {filtered.map((w) => (
        <div
          key={w.work_id}
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 6,
            padding: 10,
            background: t.bg2,
            border: `1px solid ${t.border}`,
            borderRadius: 12,
          }}
        >
          <div style={{ fontSize: 11, color: t.tg, fontFamily: "monospace" }}>
            {w.work_id}
          </div>
          {w.links.map((link) => (
            <div
              key={link.id}
              style={{
                display: "flex",
                gap: 8,
                padding: 8,
                background: t.bg3,
                borderRadius: 8,
                alignItems: "flex-start",
              }}
            >
              <div
                style={{
                  width: 40,
                  height: 60,
                  flexShrink: 0,
                  background: t.bg4,
                  borderRadius: 4,
                  overflow: "hidden",
                }}
              >
                {link.cover_url && (
                  <img
                    src={link.cover_url}
                    alt=""
                    style={{ width: "100%", height: "100%", objectFit: "cover" }}
                    onError={(e) => {
                      (e.currentTarget as HTMLImageElement).style.display = "none";
                    }}
                  />
                )}
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div
                  style={{
                    fontSize: 13,
                    fontWeight: 600,
                    color: t.text,
                    lineHeight: 1.3,
                  }}
                >
                  {link.title || `#${link.book_id}`}
                </div>
                <div style={{ fontSize: 12, color: t.td, marginTop: 2 }}>
                  {link.author_name}
                </div>
                <div
                  style={{
                    display: "flex",
                    gap: 4,
                    marginTop: 4,
                    flexWrap: "wrap",
                  }}
                >
                  <MobileBadge
                    tone={link.content_type === "audiobook" ? "info" : "ok"}
                  >
                    {link.content_type === "audiobook" ? "🎧 audio" : "📖 ebook"}
                  </MobileBadge>
                  <MobileBadge tone={link.link_source === "manual" ? "accent" : "neutral"}>
                    {link.link_source}
                  </MobileBadge>
                </div>
              </div>
              <button
                onClick={() => unlink(link)}
                style={{
                  background: t.redb,
                  color: t.red,
                  border: `1px solid ${t.redt}`,
                  borderRadius: 6,
                  padding: "4px 8px",
                  fontSize: 11,
                  cursor: "pointer",
                  flexShrink: 0,
                }}
              >
                Unlink
              </button>
            </div>
          ))}
        </div>
      ))}

      {!loading && filtered.length === 0 && (
        <div
          style={{
            padding: 24,
            textAlign: "center",
            color: t.tg,
            fontSize: 13,
            background: t.bg2,
            border: `1px solid ${t.borderL}`,
            borderRadius: 12,
          }}
        >
          {search ? "No works match." : "No works yet — try a rebuild."}
        </div>
      )}

      <MobileSection
        title="Per-author preferences"
        count={prefs.length}
        defaultOpen={false}
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {prefs.map((p) => (
            <div
              key={p.normalized_name}
              style={{
                display: "flex",
                gap: 8,
                alignItems: "center",
                padding: 8,
                background: t.bg3,
                borderRadius: 8,
              }}
            >
              <div
                style={{
                  flex: 1,
                  fontSize: 13,
                  color: t.text,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {p.display_name}
              </div>
              <select
                value={p.tracking_mode}
                onChange={(e) =>
                  setPrefMode(p.display_name, e.target.value)
                }
                style={{
                  minHeight: 36,
                  padding: "0 8px",
                  background: t.inp,
                  color: t.text,
                  border: `1px solid ${t.border}`,
                  borderRadius: 6,
                  fontSize: 13,
                }}
              >
                {TRACKING_MODES.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
              <button
                onClick={() => removePref(p.display_name)}
                style={{
                  background: "transparent",
                  color: t.red,
                  border: "none",
                  cursor: "pointer",
                  fontSize: 18,
                  padding: 4,
                }}
              >
                ×
              </button>
            </div>
          ))}
          <div
            style={{
              display: "flex",
              gap: 8,
              padding: 8,
              background: t.bg3,
              borderRadius: 8,
            }}
          >
            <input
              value={newPrefName}
              onChange={(e) => setNewPrefName(e.target.value)}
              placeholder="Author name"
              style={{
                flex: 1,
                minHeight: 36,
                padding: "0 8px",
                background: t.inp,
                color: t.text,
                border: `1px solid ${t.border}`,
                borderRadius: 6,
                fontSize: 16,
              }}
            />
            <select
              value={newPrefMode}
              onChange={(e) => setNewPrefMode(e.target.value)}
              style={{
                minHeight: 36,
                padding: "0 8px",
                background: t.inp,
                color: t.text,
                border: `1px solid ${t.border}`,
                borderRadius: 6,
                fontSize: 13,
              }}
            >
              {TRACKING_MODES.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
            <MobileBtn
              variant="primary"
              onClick={addPref}
              disabled={!newPrefName.trim()}
              style={{ minHeight: 36, fontSize: 13 }}
            >
              Add
            </MobileBtn>
          </div>
        </div>
      </MobileSection>
    </div>
  );
}
