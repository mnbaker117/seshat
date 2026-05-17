// Floating "Jump to Top" button anchored bottom-right on every page.
//
// Always visible (Mark's preference: consistency over conditional
// reveal — easier to find when you DO need it on a long page).
// z-index 50 keeps it below the BookSidebar (100), mobile nav drawer
// (201), and Toaster (9999), so overlays always cover it cleanly.

import { useTheme } from "../theme";

export function JumpToTop() {
  const t = useTheme();
  return (
    <button
      type="button"
      aria-label="Jump to top of page"
      title="Jump to top"
      onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
      style={{
        position: "fixed",
        bottom: 16,
        right: 16,
        zIndex: 50,
        width: 44,
        height: 44,
        borderRadius: "50%",
        background: t.bg2,
        color: t.accent,
        border: `1px solid ${t.accent}66`,
        boxShadow: "0 4px 14px rgba(0,0,0,0.35)",
        cursor: "pointer",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <svg
        width={22}
        height={22}
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={2.5}
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <line x1="12" y1="19" x2="12" y2="5" />
        <polyline points="5 12 12 5 19 12" />
      </svg>
    </button>
  );
}
