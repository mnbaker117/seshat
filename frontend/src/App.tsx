// Seshat app shell — mirrors AthenaScout's UI patterns.
//
// Nav structure: primary workflow pages in the horizontal bar,
// secondary/power-user pages as icon buttons on the right side.
// Dashboard is the logo click target.
import { useEffect, useState } from "react";
import { api } from "./api";
import { ThemeProvider, useTheme, useThemeControls } from "./theme";
import { Spin } from "./components/Spin";
import { ErrorBoundary } from "./components/ErrorBoundary";
import LoginPage from "./pages/LoginPage";
import AuthorsPage from "./pages/AuthorsPage";
import Dashboard from "./pages/Dashboard";
import DelayedPage from "./pages/DelayedPage";
import FiltersPage from "./pages/FiltersPage";
import IgnoredWeeklyPage from "./pages/IgnoredWeeklyPage";
import DatabasePage from "./pages/DatabasePage";
import LogsPage from "./pages/LogsPage";
import MamPage from "./pages/MamPage";
import MigrationPage from "./pages/MigrationPage";
import ReviewPage from "./pages/ReviewPage";
import SettingsPage from "./pages/SettingsPage";
import TentativePage from "./pages/TentativePage";

interface AuthState {
  loading: boolean;
  authenticated: boolean;
  firstRun: boolean;
  username?: string;
}

interface CheckResponse {
  authenticated: boolean;
  first_run: boolean;
  username?: string;
}

// Primary nav: the daily-driver pages. Kept short so the bar doesn't
// overflow on narrow screens. Mirrors AthenaScout's 6-item main nav.
const NAV: { id: string; label: string; icon: string }[] = [
  { id: "review", label: "Book Review", icon: "📚" },
  { id: "tentative", label: "New Authors", icon: "🔎" },
  { id: "ignored-weekly", label: "Weekly Ignored", icon: "📊" },
  { id: "authors", label: "Author Lists", icon: "👤" },
];

// ─── Per-page content widths ────────────────────────────────
// Data-heavy pages (lists, tables, log streams) get a wider
// container so columns and review cards have breathing room.
// Form/config pages (Settings, Filters, MAM status panel) stay
// narrow because line length matters more than horizontal real
// estate. Navbar uses WIDE_WIDTH too so the nav cluster doesn't
// overflow on a mid-sized screen (9 nav+icon items + sign-out
// button is tight at 1120px) AND so wide pages don't look
// off-center when the navbar above them stops short.
const NARROW_WIDTH = 1120;
const WIDE_WIDTH = 1400;
const WIDE_PAGES = new Set([
  "review", "tentative", "ignored-weekly", "authors",
  "delayed", "migration", "logs", "database",
]);
const widthFor = (page: string): number =>
  WIDE_PAGES.has(page) ? WIDE_WIDTH : NARROW_WIDTH;

function loadSavedPage(): string {
  try {
    return localStorage.getItem("seshat_page") || "dashboard";
  } catch {
    return "dashboard";
  }
}

export default function App() {
  return (
    <ThemeProvider>
      <AppInner />
    </ThemeProvider>
  );
}

function AppInner() {
  const theme = useTheme();
  const [auth, setAuth] = useState<AuthState>({
    loading: true,
    authenticated: false,
    firstRun: false,
  });
  const [page, setPage] = useState<string>(loadSavedPage);

  async function checkAuth() {
    try {
      const r = await api.get<CheckResponse>("/auth/check");
      setAuth({
        loading: false,
        authenticated: !!r.authenticated,
        firstRun: !!r.first_run,
        username: r.username,
      });
    } catch {
      setAuth({ loading: false, authenticated: false, firstRun: false });
    }
  }

  useEffect(() => {
    checkAuth();
    const onAuthRequired = () => {
      setAuth((s) =>
        s.authenticated
          ? { loading: false, authenticated: false, firstRun: false }
          : s,
      );
    };
    window.addEventListener("seshat:auth-required", onAuthRequired);
    return () =>
      window.removeEventListener("seshat:auth-required", onAuthRequired);
  }, []);

  function nav(p: string) {
    setPage(p);
    try { localStorage.setItem("seshat_page", p); } catch { /* */ }
    window.scrollTo(0, 0);
  }

  async function logout() {
    if (!confirm("Sign out of Seshat?")) return;
    try { await api.post("/auth/logout"); } catch { /* */ }
    setAuth({ loading: false, authenticated: false, firstRun: false });
  }

  if (auth.loading) {
    return (
      <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", background: theme.bg }}>
        <Spin />
      </div>
    );
  }

  if (!auth.authenticated) {
    return <LoginPage firstRun={auth.firstRun} onLoginSuccess={() => checkAuth()} />;
  }

  return (
    <div style={{ minHeight: "100vh", background: theme.bg, color: theme.text2 }}>
      {/* ── Sticky nav ── */}
      <nav style={{
        position: "sticky", top: 0, zIndex: 50,
        background: theme.bg + "ee", backdropFilter: "blur(12px)",
        borderBottom: `1px solid ${theme.borderL}`,
      }}>
        <div style={{
          maxWidth: WIDE_WIDTH, margin: "0 auto", padding: "0 24px",
          display: "flex", alignItems: "center", justifyContent: "space-between",
          height: 64, gap: 10,
        }}>
          {/* Logo / Dashboard link */}
          <button onClick={() => nav("dashboard")} style={{
            background: "none", border: "none", cursor: "pointer",
            fontSize: 20, fontWeight: 700, color: theme.accent, padding: 0,
            flexShrink: 0, position: "relative", paddingBottom: 4,
          }}>
            Seshat
            {page === "dashboard" && (
              <div style={{ position: "absolute", bottom: 0, left: 0, right: 0, height: 2, background: theme.accent, borderRadius: 1 }} />
            )}
          </button>

          {/* Primary nav items */}
          <div style={{ display: "flex", gap: 4, flex: 1, marginLeft: 20, overflowX: "auto" }}>
            {NAV.map((n) => (
              <button
                key={n.id}
                onClick={() => nav(n.id)}
                style={{
                  padding: "9px 16px", borderRadius: 8, fontSize: 15,
                  fontWeight: 500, border: "none", cursor: "pointer",
                  display: "inline-flex", alignItems: "center", gap: 7,
                  height: 40, whiteSpace: "nowrap", flexShrink: 0,
                  background: page === n.id ? theme.bg4 : "transparent",
                  color: page === n.id ? theme.accent : theme.text2,
                }}
              >
                <span style={{ fontSize: 17, lineHeight: 1 }}>{n.icon}</span>
                {n.label}
              </button>
            ))}
          </div>

          {/* Separator between nav pages and icon cluster */}
          <div style={{ width: 1, height: 28, background: theme.border, flexShrink: 0, marginLeft: 8, marginRight: 4 }} />

          {/* Right-side icon cluster: secondary pages + user actions */}
          <div style={{ display: "flex", alignItems: "center", gap: 6, flexShrink: 0 }}>
            <NavIcon page={page} target="filters" icon="🎯" title="Torrent Filters" onClick={() => nav("filters")} />
            <NavIcon page={page} target="mam" icon="📡" title="MAM Status" onClick={() => nav("mam")} />
            <NavIcon page={page} target="logs" icon="📝" title="Logs" onClick={() => nav("logs")} />
            <NavIcon page={page} target="database" icon="🗄️" title="Database browser" onClick={() => nav("database")} />
            <NavIcon page={page} target="settings" icon="⚙️" title="Settings" onClick={() => nav("settings")} />
            <ThemeToggleButton />
            <button onClick={logout} style={{
              background: "transparent", border: `1px solid ${theme.border}`,
              color: theme.text2, padding: "7px 12px", borderRadius: 8,
              fontSize: 12, cursor: "pointer", whiteSpace: "nowrap",
            }}>
              Sign out
            </button>
          </div>
        </div>
      </nav>

      {/* ── Main content ── */}
      <main style={{ maxWidth: widthFor(page), margin: "0 auto", padding: "28px 24px" }}>
        <ErrorBoundary onReset={() => nav("dashboard")} key={page}>
          <div style={{ animation: "fade-in 0.2s ease-out" }}>
            {page === "dashboard" && <Dashboard onNav={nav} />}
            {page === "review" && <ReviewPage />}
            {page === "tentative" && <TentativePage />}
            {page === "ignored-weekly" && <IgnoredWeeklyPage />}
            {page === "authors" && <AuthorsPage />}
            {page === "filters" && <FiltersPage />}
            {page === "delayed" && <DelayedPage />}
            {page === "migration" && <MigrationPage />}
            {page === "mam" && <MamPage />}
            {page === "logs" && <LogsPage />}
            {page === "database" && <DatabasePage />}
            {page === "settings" && <SettingsPage />}
          </div>
        </ErrorBoundary>
      </main>
    </div>
  );
}

function NavIcon({ page, target, icon, title, onClick }: {
  page: string; target: string; icon: string; title: string; onClick: () => void;
}) {
  const theme = useTheme();
  return (
    <button
      onClick={onClick}
      title={title}
      style={{
        width: 40, height: 40, borderRadius: 8,
        fontSize: 20, border: "none", cursor: "pointer",
        background: page === target ? theme.bg4 : "transparent",
        color: page === target ? theme.accent : theme.text2,
        display: "inline-flex", alignItems: "center", justifyContent: "center",
        transition: "background 0.15s",
      }}
    >
      {icon}
    </button>
  );
}

function ThemeToggleButton() {
  const theme = useTheme();
  const { themeName, cycle } = useThemeControls();
  const icon = themeName === "dark" ? "🌙" : themeName === "dim" ? "⛅" : "☀️";
  const next = themeName === "dark" ? "Dim" : themeName === "dim" ? "Light" : "Dark";
  return (
    <button
      onClick={cycle}
      title={`Theme: ${theme.name} — click for ${next}`}
      aria-label="Cycle theme"
      style={{
        width: 40, height: 40, borderRadius: 8,
        fontSize: 20, border: "none", cursor: "pointer",
        background: "transparent", color: theme.text2,
        display: "inline-flex", alignItems: "center", justifyContent: "center",
        transition: "background 0.15s",
      }}
    >
      {icon}
    </button>
  );
}
