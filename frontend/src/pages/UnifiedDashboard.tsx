// @ts-nocheck
// Unified Dashboard — merges Discovery + Pipeline widgets.
//
// Strategy: render both original dashboard components, but reorganized:
//   1. Discovery: Library overview + stat tiles
//   2. Pipeline: Status hero + MAM account (with poll countdown)
//   3. Combined: stat tiles from both in one row
//   4. Pipeline: snatch budget with progress bars
//   5. Combined: quick actions + recent activity + tools
import { useEffect, useState, useCallback } from "react";
import { api } from "../api";
import { useTheme } from "../theme";
import { Spin } from "../components/Spin";
import { Btn } from "../components/Btn";
import { fmtNum, fmtBytes, fmtRatio, fmtDuration, pct, timeAgo } from "../lib/format";
import { useVisibleInterval } from "../hooks/useVisibleInterval";

interface Props {
  onNav: (page: string, arg?: string | number | null) => void;
}

const POLL_INTERVAL = 30;

export default function UnifiedDashboard({ onNav }: Props) {
  const t = useTheme();

  // Discovery state
  const [disc, setDisc] = useState<any>(null);

  // Pipeline state
  const [health, setHealth] = useState<any>(null);
  const [mam, setMam] = useState<any>(null);
  const [budget, setBudget] = useState<any>(null);
  const [reviewCount, setReviewCount] = useState<number | null>(null);
  const [tentativeCount, setTentativeCount] = useState<number | null>(null);
  const [counts, setCounts] = useState<any>(null);
  const [recentGrabs, setRecentGrabs] = useState<any[]>([]);
  const [settings, setSettings] = useState<any>(null);
  const [countdown, setCountdown] = useState(POLL_INTERVAL);

  const refresh = useCallback(async () => {
    try {
      const results = await Promise.all([
        api.get("/discovery/stats").catch(() => null),
        api.get("/health").catch(() => null),
        api.get("/v1/mam/status").catch(() => null),
        api.get("/v1/grabs/budget").catch(() => null),
        api.get("/v1/review").catch(() => ({ pending_count: 0 })),
        api.get("/v1/tentative").catch(() => ({ items: [] })),
        api.get("/v1/data/counts").catch(() => null),
        api.get("/v1/grabs/recent").catch(() => ({ grabs: [] })),
        api.get("/v1/settings").catch(() => null),
      ]);
      setDisc(results[0]);
      setHealth(results[1]);
      setMam(results[2]);
      setBudget(results[3]);
      setReviewCount(results[4]?.pending_count ?? 0);
      setTentativeCount(results[5]?.items?.length ?? 0);
      setCounts(results[6]);
      setRecentGrabs(results[7]?.grabs ?? []);
      setSettings(results[8]);
      setCountdown(POLL_INTERVAL);
    } catch {}
  }, []);

  useEffect(() => { refresh(); }, [refresh]);
  useVisibleInterval(refresh, POLL_INTERVAL * 1000);
  useVisibleInterval(() => setCountdown(c => Math.max(0, c - 1)), 1000);

  const d = disc || {};
  const b = budget || {};
  const owned = d.owned ?? 0;
  const totalBooks = d.total ?? 0;
  const missing = d.missing ?? 0;
  const upcoming = d.upcoming ?? 0;
  const authors = d.authors ?? 0;
  const series = d.series ?? 0;
  const newBooks = d.new_books ?? 0;
  const mamFound = d.mam_found ?? 0;
  const completion = totalBooks > 0 ? pct(owned, totalBooks) : 0;
  const allowed = counts?.authors_allowed ?? 0;
  const ignored = counts?.authors_ignored ?? 0;
  const grabs = counts?.grabs ?? 0;
  const calibreAdds = counts?.calibre_additions ?? 0;
  const cwaUrl = (settings?.cwa_web_url) || "";
  const calibreUrl = (settings?.calibre_web_url) || "";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>

      {/* ── Row 1: Library Overview + Pipeline Status + MAM Account ── */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 2fr", gap: 16 }}>

        {/* Library overview */}
        <div style={{ background: t.bg2, border: `1px solid ${t.border}`, borderRadius: 14, padding: "24px 28px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
            <div>
              <div style={{ fontSize: 22, fontWeight: 700, color: t.text }}>Your Library</div>
              <div style={{ fontSize: 13, color: t.textDim, marginTop: 2 }}>{fmtNum(owned)} of {fmtNum(totalBooks)} books owned</div>
            </div>
            <div style={{ fontSize: 36, fontWeight: 800, color: t.accent }}>{completion}%</div>
          </div>
          <div style={{ height: 8, background: t.bg4, borderRadius: 4, overflow: "hidden" }}>
            <div style={{ height: "100%", width: `${Math.min(completion, 100)}%`, background: `linear-gradient(90deg, ${t.jade}, ${t.accent})`, borderRadius: 4 }} />
          </div>
          {/* Discovery stat tiles */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", gap: 10, marginTop: 16 }}>
            <MiniStat icon="📚" value={fmtNum(owned)} label="Owned" color={t.accent} onClick={() => onNav("disc-library")} />
            <MiniStat icon="🔍" value={fmtNum(missing)} label="Missing" color={t.ylw} onClick={() => onNav("disc-missing")} />
            <MiniStat icon="✨" value={fmtNum(newBooks)} label="New" color={t.jade} />
            <MiniStat icon="📅" value={fmtNum(upcoming)} label="Upcoming" color={t.cyan} onClick={() => onNav("disc-upcoming")} />
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", gap: 10, marginTop: 10 }}>
            <MiniStat icon="👤" value={fmtNum(authors)} label="Authors" onClick={() => onNav("disc-authors")} />
            <MiniStat icon="📖" value={fmtNum(series)} label="Series" />
            <MiniStat icon="🎯" value={fmtNum(mamFound)} label="MAM Found" color={t.jade} onClick={() => onNav("disc-mam")} />
            <MiniStat icon="💡" value={fmtNum(d.suggestions ?? 0)} label="Suggestions" onClick={() => onNav("disc-suggestions")} />
          </div>
        </div>

        {/* Pipeline Status hero */}
        <div style={{ background: t.bg2, border: `1px solid ${t.border}`, borderRadius: 14, padding: "24px 28px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: 16 }}>
            <div>
              <h1 style={{ fontSize: 24, fontWeight: 700, color: t.text, margin: 0 }}>Pipeline Status</h1>
              <p style={{ fontSize: 13, color: t.textDim, marginTop: 4 }}>
                {health?.dispatcher_ready
                  ? `${fmtNum(grabs)} total grabs · ${fmtNum(calibreAdds)} books added to Calibre`
                  : "Starting up…"}
              </p>
              <div style={{ display: "flex", gap: 16, marginTop: 14, flexWrap: "wrap", alignItems: "center" }}>
                <StatusPill label="Dispatcher" ok={health?.dispatcher_ready ?? false} />
                <StatusPill label="IRC Listener" ok={health?.dispatcher_ready ?? false} />
                <StatusPill label="MAM Cookie" ok={mam?.validation_ok ?? false} warn={mam?.cookie_configured && !mam?.validation_ok} />
                <StatusPill label="Budget Watcher" ok={health?.dispatcher_ready ?? false} />
                <div style={{ marginLeft: 8, background: t.bg3, borderRadius: 8, padding: "6px 12px", display: "flex", alignItems: "center", gap: 6 }}>
                  <span style={{ fontSize: 10, color: t.textDim, textTransform: "uppercase", letterSpacing: "0.05em", fontWeight: 600 }}>Next poll</span>
                  <span style={{ fontSize: 16, fontWeight: 700, color: countdown <= 5 ? t.accent : t.text2 }}>{countdown}s</span>
                </div>
              </div>
            </div>

            {/* MAM account */}
            {mam?.username && (
              <div style={{ background: t.bg3, borderRadius: 12, padding: "14px 18px", textAlign: "right" }}>
                <div style={{ fontSize: 11, color: t.textDim, textTransform: "uppercase", letterSpacing: "0.05em", fontWeight: 600 }}>
                  MAM · {mam.username}
                </div>
                {mam.classname && <div style={{ fontSize: 11, color: t.textDim, marginTop: 1 }}>{mam.classname}</div>}
                <div style={{ marginTop: 8, display: "flex", gap: 16, justifyContent: "flex-end" }}>
                  {mam.ratio !== null && (
                    <div>
                      <div style={{ fontSize: 24, fontWeight: 700, color: mam.ratio >= 1 ? t.ok : t.warn }}>{fmtRatio(mam.ratio)}</div>
                      <div style={{ fontSize: 10, color: t.textDim }}>Ratio</div>
                    </div>
                  )}
                  {mam.wedges !== null && (
                    <div>
                      <div style={{ fontSize: 24, fontWeight: 700, color: t.accent }}>{fmtNum(mam.wedges)}</div>
                      <div style={{ fontSize: 10, color: t.textDim }}>Wedges</div>
                    </div>
                  )}
                </div>
                {(mam.uploaded_bytes || mam.downloaded_bytes) && (
                  <div style={{ fontSize: 10, color: t.textDim, marginTop: 6 }}>
                    ↑ {fmtBytes(mam.uploaded_bytes)} · ↓ {fmtBytes(mam.downloaded_bytes)}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ── Row 2: Snatch Budget (full width with progress bars) ── */}
      {b.budget_cap > 0 && (
        <div style={{ background: t.bg2, border: `1px solid ${t.border}`, borderRadius: 12, padding: "18px 24px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16 }}>
            <div>
              <div style={{ fontSize: 12, fontWeight: 600, color: t.textDim, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 8 }}>Snatch Budget</div>
              <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
                <span style={{ fontSize: 28, fontWeight: 700, color: b.budget_used >= b.budget_cap ? t.warn : t.accent }}>{b.budget_used ?? 0}</span>
                <span style={{ fontSize: 14, color: t.textDim }}>/ {b.budget_cap}</span>
              </div>
              <div style={{ fontSize: 11, color: t.textDim, marginTop: 2 }}>
                {b.ledger_active ?? 0} Seshat + {b.qbit_extras ?? 0} manual
                {b.queue_size > 0 && <span style={{ color: t.warn, marginLeft: 6 }}>{b.queue_size} queued</span>}
              </div>
              {b.next_release_seconds != null && b.next_release_seconds > 0 && (
                <div style={{ fontSize: 12, color: t.accent, marginTop: 4 }}>Next release in {fmtDuration(b.next_release_seconds)}</div>
              )}
            </div>
            {/* Active entries with progress bars */}
            {b.entries?.length > 0 && (
              <div style={{ flex: 1, minWidth: 300, maxWidth: 700 }}>
                <div style={{ maxHeight: 140, overflowY: "auto" }}>
                  {b.entries.map((e, i) => {
                    const seedPct = Math.min(100, (e.seeding_seconds / b.seed_seconds_required) * 100);
                    return (
                      <div key={e.grab_id ?? i} style={{ display: "flex", alignItems: "center", gap: 10, padding: "4px 0", borderBottom: `1px solid ${t.borderL}`, fontSize: 12 }}>
                        <div style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: t.text2 }}>
                          {e.source === "external" && <span style={{ fontSize: 9, padding: "1px 4px", borderRadius: 3, background: t.textDim + "22", color: t.textDim, fontWeight: 600, marginRight: 4 }}>EXT</span>}
                          {e.torrent_name}
                        </div>
                        <div style={{ width: 80, height: 4, borderRadius: 2, background: t.bg4, flexShrink: 0 }}>
                          <div style={{ width: `${seedPct}%`, height: "100%", borderRadius: 2, background: seedPct >= 100 ? t.ok : t.accent }} />
                        </div>
                        <span style={{ width: 60, textAlign: "right", color: t.textDim, fontSize: 11, flexShrink: 0 }}>
                          {e.remaining_seconds > 0 ? fmtDuration(e.remaining_seconds) : "done"}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Row 3: Pipeline stat cards ── */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(6, 1fr)", gap: 12 }}>
        <StatCard label="Books to Review" value={reviewCount} icon="📚" color={(reviewCount ?? 0) > 0 ? t.accent : t.textDim} onClick={() => onNav("pipe-review")} highlight={(reviewCount ?? 0) > 0} />
        <StatCard label="New Authors" value={tentativeCount} icon="🔎" color={(tentativeCount ?? 0) > 0 ? t.warn : t.textDim} onClick={() => onNav("pipe-tentative")} highlight={(tentativeCount ?? 0) > 0} />
        <StatCard label="Allowed" value={allowed} icon="✅" color={t.ok} onClick={() => onNav("pipe-authors")} />
        <StatCard label="Ignored" value={ignored} icon="⛔" color={t.textDim} onClick={() => onNav("pipe-authors")} />
        <StatCard label="To Calibre" value={calibreAdds} icon="📖" color={t.ok} />
        <StatCard label="Total Grabs" value={grabs} icon="📥" color={t.text2} />
      </div>

      {/* ── Row 4: Quick Actions + Recent Activity + Tools ── */}
      <div style={{ background: t.bg2, border: `1px solid ${t.border}`, borderRadius: 12, padding: 24, display: "grid", gridTemplateColumns: "1fr 1.5fr auto", gap: 0, alignItems: "start" }}>

        {/* Quick Actions (merged from both dashboards) */}
        <div style={{ paddingRight: 20 }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: t.textDim, textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 12 }}>Quick Actions</div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <Btn variant="primary" onClick={() => onNav("pipe-review")}>📚 Review Books {reviewCount ? `(${reviewCount})` : ""}</Btn>
            <Btn onClick={() => onNav("pipe-tentative")}>🔎 New Authors {tentativeCount ? `(${tentativeCount})` : ""}</Btn>
            <Btn onClick={() => onNav("pipe-ignored")}>📊 Weekly Ignored</Btn>
            <Btn onClick={() => onNav("pipe-authors")}>👤 Author Lists</Btn>
            {cwaUrl && <Btn onClick={() => window.open(cwaUrl, "_blank")}>📕 CWA</Btn>}
            {calibreUrl && <Btn onClick={() => window.open(calibreUrl, "_blank")}>📗 Calibre</Btn>}
          </div>
        </div>

        {/* Recent Activity */}
        <div style={{ borderLeft: `1px solid ${t.borderL}`, borderRight: `1px solid ${t.borderL}`, paddingLeft: 20, paddingRight: 20 }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: t.textDim, textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 12 }}>Recent Activity</div>
          {recentGrabs.length > 0 ? (
            recentGrabs.slice(0, 5).map((g, i) => (
              <div key={i} style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", fontSize: 12, padding: "5px 0", borderBottom: i < 4 ? `1px solid ${t.borderL}` : "none" }}>
                <span style={{ color: t.text2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}>{g.torrent_name}</span>
                {g.author_blob && <span style={{ color: t.textDim, marginLeft: 8, flexShrink: 0 }}>— {g.author_blob}</span>}
                <span style={{ fontSize: 10, color: t.textDim, marginLeft: 10, flexShrink: 0 }}>{g.grabbed_at ? new Date(g.grabbed_at + "Z").toLocaleDateString() : ""}</span>
              </div>
            ))
          ) : (
            <div style={{ fontSize: 12, color: t.textDim, fontStyle: "italic" }}>No recent grabs yet.</div>
          )}
        </div>

        {/* Tools */}
        <div style={{ paddingLeft: 20, display: "flex", flexDirection: "column", gap: 6 }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: t.textDim, textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 4 }}>Tools</div>
          <ToolBtn label="Migration" icon="📦" onClick={() => onNav("pipe-migration")} />
          <ToolBtn label="MAM Account" icon="📡" onClick={() => onNav("pipe-mam")} />
          <ToolBtn label="Logs" icon="📝" onClick={() => onNav("logs")} />
          <ToolBtn label="Settings" icon="⚙️" onClick={() => onNav("settings")} />
        </div>
      </div>
    </div>
  );
}

// ── Shared sub-components ────────────────────────────────────

function MiniStat({ icon, value, label, color, onClick }: {
  icon: string; value: string; label: string; color?: string; onClick?: () => void;
}) {
  const t = useTheme();
  return (
    <div onClick={onClick} style={{
      background: t.bg3, borderRadius: 8, padding: "8px 10px",
      cursor: onClick ? "pointer" : "default", textAlign: "center",
    }}>
      <div style={{ fontSize: 14 }}>{icon}</div>
      <div style={{ fontSize: 16, fontWeight: 700, color: color || t.text, marginTop: 2 }}>{value}</div>
      <div style={{ fontSize: 10, color: t.textDim }}>{label}</div>
    </div>
  );
}

function StatusPill({ label, ok, warn }: { label: string; ok: boolean; warn?: boolean }) {
  const t = useTheme();
  const color = ok ? t.ok : warn ? t.warn : t.textDim;
  const text = ok ? "Online" : warn ? "Check" : "Offline";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <div style={{ width: 8, height: 8, borderRadius: "50%", background: color, boxShadow: ok ? `0 0 6px ${color}66` : "none" }} />
      <div>
        <div style={{ fontSize: 12, fontWeight: 600, color: t.text2 }}>{label}</div>
        <div style={{ fontSize: 10, color }}>{text}</div>
      </div>
    </div>
  );
}

function StatCard({ label, value, icon, color, onClick, highlight }: {
  label: string; value: number | string | null; icon: string; color: string;
  onClick?: () => void; highlight?: boolean;
}) {
  const t = useTheme();
  return (
    <div onClick={onClick} style={{
      background: t.bg2, border: `1px solid ${highlight ? color + "55" : t.border}`,
      borderRadius: 12, padding: "16px 18px",
      cursor: onClick ? "pointer" : "default",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ fontSize: 22 }}>{icon}</span>
        <span style={{ fontSize: 26, fontWeight: 700, color }}>
          {value === null ? <Spin size={18} /> : typeof value === "number" ? fmtNum(value) : value}
        </span>
      </div>
      <div style={{ fontSize: 12, color: t.textDim, marginTop: 8, fontWeight: 500 }}>{label}</div>
    </div>
  );
}

function ToolBtn({ label, icon, onClick }: { label: string; icon: string; onClick: () => void }) {
  const t = useTheme();
  return (
    <button onClick={onClick} style={{
      display: "flex", alignItems: "center", gap: 8,
      padding: "6px 12px", background: t.bg4, border: `1px solid ${t.border}`,
      borderRadius: 6, cursor: "pointer", fontSize: 12, fontWeight: 500,
      color: t.text2, whiteSpace: "nowrap",
    }}>
      <span style={{ fontSize: 14 }}>{icon}</span> {label}
    </button>
  );
}
