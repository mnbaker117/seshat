// Mobile-native logs viewer. Tab chips for category, search input,
// auto-refresh every 5s while visible, monospace scrolling list.
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useTheme } from "../theme";
import { useVisibleInterval } from "../hooks/useVisibleInterval";
import { Ic } from "../icons";
import {
  MobileChip,
  MobileInput,
  MobileBtn,
  MobileBackButton,
} from "../components/mobile";

interface LogEntry {
  ts: string;
  level: string;
  logger: string;
  message: string;
  is_announce: boolean;
}

interface LogsResponse {
  entries: LogEntry[];
  total_buffered: number;
}

type Tab = "all" | "announces" | "application" | "irc" | "scans";

const TABS: { v: Tab; label: string }[] = [
  { v: "all", label: "All" },
  { v: "application", label: "App" },
  { v: "irc", label: "IRC" },
  { v: "announces", label: "Announces" },
  { v: "scans", label: "Scans" },
];

export default function MobileLogsPage() {
  const t = useTheme();
  const [tab, setTab] = useState<Tab>("all");
  const [entries, setEntries] = useState<LogEntry[] | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [autoScroll, setAutoScroll] = useState(true);
  const [filter, setFilter] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  const load = async () => {
    try {
      const params = new URLSearchParams({ lines: "2000" });
      if (tab === "announces") params.set("filter", "announces");
      else if (tab !== "all") params.set("category", tab);
      const r = await api.get<LogsResponse>(`/v1/logs?${params}`);
      setEntries(r.entries);
      setTotal(r.total_buffered);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);
  useVisibleInterval(() => {
    if (autoScroll) load();
  }, 5000);

  useEffect(() => {
    if (autoScroll && bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: "auto" });
    }
  }, [entries, autoScroll]);

  const levelColor = (level: string) => {
    switch (level) {
      case "ERROR":
        return t.err;
      case "WARNING":
        return t.warn;
      case "INFO":
        return t.cyan;
      case "DEBUG":
        return t.tg;
      default:
        return t.td;
    }
  };

  const filtered = (entries || []).filter((e) => {
    if (!filter) return true;
    const f = filter.toLowerCase();
    return (
      e.message.toLowerCase().includes(f) || e.logger.toLowerCase().includes(f)
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
          Logs
        </h1>
        <span style={{ fontSize: 12, color: t.tg }}>{total} buffered</span>
      </div>

      {/* Tab chips */}
      <div
        style={{
          display: "flex",
          gap: 6,
          overflowX: "auto",
          scrollbarWidth: "none",
        }}
      >
        {TABS.map((opt) => (
          <MobileChip
            key={opt.v}
            active={tab === opt.v}
            onClick={() => setTab(opt.v)}
          >
            {opt.label}
          </MobileChip>
        ))}
      </div>

      {/* Filter + auto-scroll */}
      <MobileInput
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        placeholder="Filter visible lines"
        leadingIcon={Ic.search}
        trailing={
          filter ? (
            <button
              onClick={() => setFilter("")}
              style={{
                background: "none",
                border: "none",
                cursor: "pointer",
                color: t.tg,
                padding: 4,
                display: "flex",
                width: 32,
                height: 32,
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              {Ic.x}
            </button>
          ) : undefined
        }
      />

      <div style={{ display: "flex", gap: 6 }}>
        <MobileChip
          active={autoScroll}
          onClick={() => setAutoScroll((s) => !s)}
        >
          {autoScroll ? "Auto-scrolling" : "Paused"}
        </MobileChip>
        <MobileBtn
          variant="ghost"
          onClick={load}
          style={{ minHeight: 36, fontSize: 13 }}
        >
          Refresh
        </MobileBtn>
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

      {/* Log list */}
      <div
        style={{
          background: t.bg2,
          border: `1px solid ${t.border}`,
          borderRadius: 12,
          maxHeight: "60vh",
          overflowY: "auto",
          fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
          fontSize: 11,
        }}
        onScroll={(e) => {
          // Pause auto-scroll if user scrolls up.
          const el = e.currentTarget;
          const atBottom =
            el.scrollHeight - el.scrollTop - el.clientHeight < 40;
          if (!atBottom && autoScroll) setAutoScroll(false);
        }}
      >
        {entries === null ? (
          <div style={{ padding: 16, color: t.tg }}>Loading…</div>
        ) : filtered.length === 0 ? (
          <div style={{ padding: 16, color: t.tg }}>No log entries.</div>
        ) : (
          filtered.map((e, i) => (
            <div
              key={i}
              style={{
                padding: "6px 10px",
                borderBottom: `1px solid ${t.borderL}`,
                display: "flex",
                gap: 6,
                flexWrap: "wrap",
              }}
            >
              <span style={{ color: t.tg, flexShrink: 0 }}>
                {e.ts.split("T")[1]?.split(".")[0] || e.ts}
              </span>
              <span
                style={{
                  color: levelColor(e.level),
                  fontWeight: 700,
                  flexShrink: 0,
                  textTransform: "uppercase",
                }}
              >
                {e.level}
              </span>
              <span style={{ color: t.text2, wordBreak: "break-word" }}>
                {e.message}
              </span>
            </div>
          ))
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
