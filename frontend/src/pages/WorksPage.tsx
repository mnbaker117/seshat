// Works page — cross-library ebook ↔ audiobook link browser.
//
// Lists every `work` (a group of links across libraries) with its
// members side-by-side. The user can:
//   * search across titles/authors
//   * filter to show only manual vs auto vs all
//   * manually unlink a single member
//   * rebuild the matcher from the page
//
// Per-author format preferences live on this page too — scroll to
// the bottom section where the user can set "this author: audiobook
// only" overrides that flow through to missing-book detection.
import { useCallback, useEffect, useState } from "react";
import { useTheme } from "../theme";
import { api } from "../api";
import { Btn } from "../components/Btn";
import { Load } from "../components/Load";
import { Spin } from "../components/Spin";
import { toast } from "../lib/toast";
import { useViewport } from "../hooks/useViewport";
import { useMobileCodepath } from "../components/mobile";
import MobileWorksPage from "./MobileWorksPage";

interface WorkLinkOut {
  id: number;
  work_id: string;
  library_slug: string;
  book_id: number;
  content_type: string;
  link_source: string;
  created_at: number;
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
  updated_at: number;
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

export default function WorksPage() {
  const vp = useViewport();
  if (useMobileCodepath(vp)) return <MobileWorksPage />;
  return <DesktopWorksPage />;
}

function DesktopWorksPage() {
  const t = useTheme();
  const [works, setWorks] = useState<WorkSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<SourceFilter>("all");
  const [rebuilding, setRebuilding] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.get<WorksListResponse>("/v1/works?limit=500");
      setWorks(r.items);
      setTotal(r.total);
    } catch (e) {
      toast.error(`Failed to load works: ${e}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const rebuild = async () => {
    setRebuilding(true);
    try {
      const r = await api.post<RebuildResult>("/v1/works/rebuild");
      toast.success(
        `Matcher: +${r.links_added} links, ${r.stale_auto_removed} stale cleared`,
      );
      await load();
    } catch (e: any) {
      toast.error(`Rebuild failed: ${e?.message || e}`);
    } finally {
      setRebuilding(false);
    }
  };

  const unlink = async (link: WorkLinkOut) => {
    const label = link.title || `book ${link.book_id}`;
    if (!confirm(`Unlink "${label}" from this work? The other side(s) will remain linked.`)) {
      return;
    }
    try {
      await api.del(`/v1/works/link/${encodeURIComponent(link.library_slug)}/${link.book_id}`);
      toast.success(`Unlinked ${label}`);
      await load();
    } catch (e: any) {
      toast.error(`Unlink failed: ${e?.message || e}`);
    }
  };

  // Apply client-side search + source filters.
  const filteredWorks = works.filter(w => {
    if (w.links.length < 2) return false;  // singletons aren't interesting
    if (filter !== "all") {
      const hasMatch = w.links.some(l => l.link_source === filter);
      if (!hasMatch) return false;
    }
    if (search) {
      const q = search.toLowerCase();
      const hit = w.links.some(l =>
        (l.title || "").toLowerCase().includes(q) ||
        (l.author_name || "").toLowerCase().includes(q),
      );
      if (!hit) return false;
    }
    return true;
  });

  // Quick stats.
  const linkCount = works.reduce((acc, w) => acc + w.links.length, 0);
  const manualCount = works.reduce(
    (acc, w) => acc + w.links.filter(l => l.link_source === "manual").length, 0,
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* ─── Header ─── */}
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        gap: 12, flexWrap: "wrap",
      }}>
        <div>
          <h1 style={{ fontSize: 24, fontWeight: 800, color: t.accent, margin: 0 }}>
            Works
            <span style={{ fontSize: 15, fontWeight: 600, color: t.td, marginLeft: 10 }}>
              {filteredWorks.length.toLocaleString()} / {total.toLocaleString()} linked
            </span>
          </h1>
          <div style={{ fontSize: 13, color: t.td, marginTop: 4 }}>
            {linkCount.toLocaleString()} total memberships · {manualCount.toLocaleString()} manual
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search title or author"
            style={{
              padding: "6px 10px", borderRadius: 6,
              border: `1px solid ${t.border}`, background: t.inp,
              color: t.text2, fontSize: 13, width: 220, outline: "none",
            }}
          />
          <select
            value={filter}
            onChange={e => setFilter(e.target.value as SourceFilter)}
            style={{
              padding: "6px 10px", borderRadius: 6,
              border: `1px solid ${t.border}`, background: t.inp,
              color: t.text2, fontSize: 13,
            }}
          >
            <option value="all">All links</option>
            <option value="auto">Auto-matched only</option>
            <option value="manual">Manual only</option>
          </select>
          <Btn variant="ghost" onClick={rebuild} disabled={rebuilding}>
            {rebuilding ? <Spin size={14} /> : "Rebuild"}
          </Btn>
        </div>
      </div>

      {/* ─── Works list ─── */}
      {loading ? <Load /> : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {filteredWorks.length === 0 ? (
            <div style={{
              padding: 40, background: t.bg2, borderRadius: 8,
              border: `1px dashed ${t.border}`, textAlign: "center",
              color: t.td, fontSize: 14,
            }}>
              No linked works match the current filter.
              <div style={{ fontSize: 12, color: t.tm, marginTop: 8 }}>
                Cross-library links are created automatically after each
                Calibre + ABS sync. Run "Rebuild" to force a refresh.
              </div>
            </div>
          ) : (
            filteredWorks.map(w => <WorkRow key={w.work_id} work={w} onUnlink={unlink} />)
          )}
        </div>
      )}

      {/* ─── Per-author preferences ─── */}
      <AuthorPreferencesSection />
    </div>
  );
}

// ─── Single work row ─────────────────────────────────────────

function WorkRow({ work, onUnlink }: {
  work: WorkSummary;
  onUnlink: (link: WorkLinkOut) => void;
}) {
  const t = useTheme();
  return (
    <div style={{
      background: t.bg2, border: `1px solid ${t.border}`, borderRadius: 8,
      padding: "10px 14px", display: "flex", flexDirection: "column", gap: 8,
    }}>
      <div style={{
        display: "flex", alignItems: "center", gap: 8, fontSize: 11,
        color: t.td, fontFamily: "monospace",
      }}>
        <span>work {work.work_id.slice(0, 8)}</span>
        <span style={{ color: t.textDim }}>· {work.links.length} members</span>
      </div>
      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(340px, 1fr))",
        gap: 8,
      }}>
        {work.links.map(link => (
          <WorkMember key={link.id} link={link} onUnlink={onUnlink} />
        ))}
      </div>
    </div>
  );
}

// ─── Single member row within a work ─────────────────────────

function WorkMember({ link, onUnlink }: {
  link: WorkLinkOut;
  onUnlink: (link: WorkLinkOut) => void;
}) {
  const t = useTheme();
  const isAudio = link.content_type === "audiobook";
  const accentColor = isAudio ? t.pur : t.cyan;
  const accentBg = isAudio ? t.purb : t.cyanb;
  const accentFg = isAudio ? t.purt : t.cyant;

  return (
    <div style={{
      display: "flex", gap: 10, padding: "8px 10px", borderRadius: 6,
      background: t.bg3, border: `1px solid ${t.borderL}`,
      alignItems: "flex-start",
    }}>
      {link.cover_url ? (
        <img
          src={link.cover_url}
          alt=""
          style={{
            width: 40, height: 58, objectFit: "cover", borderRadius: 3,
            flexShrink: 0, background: t.bg4,
          }}
          onError={e => ((e.target as HTMLImageElement).style.display = "none")}
        />
      ) : (
        <div style={{
          width: 40, height: 58, borderRadius: 3, background: t.bg4,
          flexShrink: 0, display: "flex", alignItems: "center",
          justifyContent: "center", fontSize: 18,
        }}>{isAudio ? "🎧" : "📖"}</div>
      )}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 2 }}>
          <span style={{
            fontSize: 10, fontWeight: 700, textTransform: "uppercase",
            padding: "1px 6px", borderRadius: 3,
            background: accentBg, color: accentFg,
            border: `1px solid ${accentColor}44`,
          }}>{link.content_type}</span>
          {link.link_source === "manual" && (
            <span style={{
              fontSize: 10, fontWeight: 700, textTransform: "uppercase",
              padding: "1px 6px", borderRadius: 3,
              background: t.ylwb, color: t.ylwt,
              border: `1px solid ${t.ylw}44`,
            }}>manual</span>
          )}
        </div>
        <div style={{
          fontSize: 13, fontWeight: 600, color: t.text,
          whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
        }}>{link.title || "(title missing)"}</div>
        <div style={{
          fontSize: 12, color: t.text2,
          whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
        }}>{link.author_name || "—"}</div>
        <div style={{ fontSize: 11, color: t.td, marginTop: 2 }}>
          {link.series_name ? `${link.series_name}${link.series_index ? ` #${link.series_index}` : ""} · ` : ""}
          {link.library_slug}
        </div>
      </div>
      <Btn size="sm" variant="ghost" onClick={() => onUnlink(link)}>Unlink</Btn>
    </div>
  );
}

// ─── Per-author format preferences ───────────────────────────

function AuthorPreferencesSection() {
  const t = useTheme();
  const [prefs, setPrefs] = useState<AuthorPref[]>([]);
  const [loading, setLoading] = useState(true);
  const [newAuthor, setNewAuthor] = useState("");
  const [newMode, setNewMode] = useState("both");

  const load = async () => {
    setLoading(true);
    try {
      const r = await api.get<AuthorPref[]>("/v1/works/author-preferences");
      setPrefs(r);
    } catch {
      setPrefs([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const setPref = async (author: string, mode: string) => {
    try {
      await api.put(`/v1/works/author-preferences/${encodeURIComponent(author)}`, {
        tracking_mode: mode,
      });
      await load();
    } catch (e: any) {
      toast.error(`Failed to set preference: ${e?.message || e}`);
    }
  };

  const clear = async (author: string) => {
    if (!confirm(`Clear format preference for "${author}"? They'll inherit the global default.`)) {
      return;
    }
    try {
      await api.del(`/v1/works/author-preferences/${encodeURIComponent(author)}`);
      await load();
    } catch (e: any) {
      toast.error(`Failed to clear: ${e?.message || e}`);
    }
  };

  const addNew = async () => {
    const name = newAuthor.trim();
    if (!name) return;
    await setPref(name, newMode);
    setNewAuthor("");
    setNewMode("both");
  };

  return (
    <div style={{
      marginTop: 20, padding: 16, background: t.bg2, borderRadius: 8,
      border: `1px solid ${t.border}`,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
        <h2 style={{ fontSize: 16, fontWeight: 700, color: t.accent, margin: 0 }}>
          Per-Author Format Preferences
        </h2>
        <span style={{ fontSize: 12, color: t.td }}>
          Overrides the global Audiobook Tracking Mode for specific authors.
        </span>
      </div>
      <p style={{ fontSize: 12, color: t.td, marginBottom: 12, lineHeight: 1.5 }}>
        Preferences are keyed by normalized author name — a preference on
        "Brandon Sanderson" applies whether the author appears in your
        Calibre library or your ABS library. "Both" means owning either
        format marks a book as owned; "Ebook" / "Audiobook" mean only
        that format counts.
      </p>

      {/* Add-new row */}
      <div style={{
        display: "flex", gap: 8, alignItems: "center", marginBottom: 16,
        padding: "10px 12px", background: t.bg3, borderRadius: 6,
        border: `1px solid ${t.borderL}`,
      }}>
        <input
          value={newAuthor}
          onChange={e => setNewAuthor(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter") addNew(); }}
          placeholder="Author name"
          style={{
            flex: 1, padding: "6px 10px", borderRadius: 6,
            border: `1px solid ${t.border}`, background: t.inp,
            color: t.text2, fontSize: 13, outline: "none",
          }}
        />
        <select
          value={newMode}
          onChange={e => setNewMode(e.target.value)}
          style={{
            padding: "6px 10px", borderRadius: 6,
            border: `1px solid ${t.border}`, background: t.inp,
            color: t.text2, fontSize: 13,
          }}
        >
          <option value="both">Both</option>
          <option value="ebook">Ebook only</option>
          <option value="audiobook">Audiobook only</option>
        </select>
        <Btn size="sm" onClick={addNew} disabled={!newAuthor.trim()}>Add</Btn>
      </div>

      {loading ? <Load /> : prefs.length === 0 ? (
        <div style={{ fontSize: 13, color: t.td, fontStyle: "italic" }}>
          No per-author overrides yet. All authors use the global default.
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {prefs.map(p => (
            <div key={p.normalized_name} style={{
              display: "flex", alignItems: "center", gap: 10,
              padding: "6px 10px", borderRadius: 6, background: t.bg3,
              border: `1px solid ${t.borderL}`,
            }}>
              <div style={{ flex: 1, fontSize: 13, fontWeight: 600, color: t.text }}>
                {p.display_name}
              </div>
              <select
                value={p.tracking_mode}
                onChange={e => setPref(p.display_name, e.target.value)}
                style={{
                  padding: "4px 8px", borderRadius: 5,
                  border: `1px solid ${t.border}`, background: t.inp,
                  color: t.text2, fontSize: 12,
                }}
              >
                <option value="both">Both</option>
                <option value="ebook">Ebook only</option>
                <option value="audiobook">Audiobook only</option>
              </select>
              <Btn size="sm" variant="ghost" onClick={() => clear(p.display_name)}>
                Clear
              </Btn>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
