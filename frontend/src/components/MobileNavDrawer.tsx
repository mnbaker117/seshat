// Slide-out nav drawer for mobile viewports.
//
// The desktop nav is one horizontal row of ~12 buttons that exceeds
// 900px of intrinsic width. On phones we collapse all of that into
// a hamburger button that opens this drawer. The drawer renders a
// vertical list of section switchers (Discovery / Pipeline), the
// active section's nav items, and the right-side icons (settings,
// logs, etc.) in a single tappable column.
//
// Tapping any item closes the drawer + navigates. Tapping outside
// also closes. Escape closes.
import { useEffect } from "react";
import { useTheme } from "../theme";

export interface NavItem {
  id: string;
  label: string;
  icon: string;
}

export type Section = "discovery" | "pipeline";

interface Props {
  open: boolean;
  onClose: () => void;
  section: Section;
  onSectionChange: (s: Section) => void;
  activePage: string;
  navItems: NavItem[];
  rightIcons: NavItem[];
  onNavigate: (page: string) => void;
  themeIcon: string;
  themeName: string;
  onCycleTheme: () => void;
  onLogout: () => void;
}

export function MobileNavDrawer({
  open,
  onClose,
  section,
  onSectionChange,
  activePage,
  navItems,
  rightIcons,
  onNavigate,
  themeIcon,
  themeName,
  onCycleTheme,
  onLogout,
}: Props) {
  const t = useTheme();

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [open, onClose]);

  if (!open) return null;

  const itemBtn = (
    label: string,
    icon: string,
    active: boolean,
    onClick: () => void,
  ) => (
    <button
      key={label}
      onClick={() => {
        onClick();
        onClose();
      }}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 14,
        width: "100%",
        padding: "14px 16px",
        background: active ? t.abg : "transparent",
        color: active ? t.accent : t.text2,
        border: "none",
        borderRadius: 8,
        fontSize: 16,
        fontWeight: active ? 700 : 500,
        textAlign: "left",
        cursor: "pointer",
      }}
    >
      <span style={{ fontSize: 18, width: 22, textAlign: "center" }}>
        {icon}
      </span>
      <span>{label}</span>
    </button>
  );

  return (
    <>
      {/* Scrim — closes the drawer when tapped */}
      <div
        onClick={onClose}
        style={{
          position: "fixed",
          inset: 0,
          background: "rgba(0,0,0,0.5)",
          zIndex: 200,
          animation: "fade-in 0.15s ease-out",
        }}
      />
      {/* Drawer panel */}
      <aside
        style={{
          position: "fixed",
          top: 0,
          right: 0,
          width: "min(320px, 88vw)",
          height: "100vh",
          background: t.bg2,
          borderLeft: `1px solid ${t.border}`,
          zIndex: 201,
          overflowY: "auto",
          padding: 12,
          display: "flex",
          flexDirection: "column",
          gap: 6,
          animation: "slide-up 0.2s ease-out",
        }}
      >
        {/* Section switcher */}
        <div
          style={{
            display: "flex",
            gap: 4,
            padding: 4,
            background: t.bg3,
            borderRadius: 8,
            marginBottom: 8,
          }}
        >
          {(["discovery", "pipeline"] as Section[]).map((s) => (
            <button
              key={s}
              onClick={() => onSectionChange(s)}
              style={{
                flex: 1,
                padding: "10px 8px",
                background: section === s ? t.abg : "transparent",
                color: section === s ? t.accent : t.td,
                border: `1px solid ${section === s ? t.abr : "transparent"}`,
                borderRadius: 6,
                fontSize: 14,
                fontWeight: 600,
                textTransform: "capitalize",
                cursor: "pointer",
              }}
            >
              {s}
            </button>
          ))}
        </div>

        {/* Section nav items */}
        <div
          style={{
            fontSize: 11,
            fontWeight: 700,
            color: t.tg,
            textTransform: "uppercase",
            letterSpacing: "0.04em",
            padding: "6px 16px",
          }}
        >
          {section === "discovery" ? "Discovery" : "Pipeline"}
        </div>
        {navItems.map((item) =>
          itemBtn(item.label, item.icon, activePage === item.id, () =>
            onNavigate(item.id),
          ),
        )}

        <div
          style={{
            height: 1,
            background: t.border,
            margin: "10px 8px",
          }}
        />

        <div
          style={{
            fontSize: 11,
            fontWeight: 700,
            color: t.tg,
            textTransform: "uppercase",
            letterSpacing: "0.04em",
            padding: "6px 16px",
          }}
        >
          Tools
        </div>
        {rightIcons.map((item) =>
          itemBtn(item.label, item.icon, activePage === item.id, () =>
            onNavigate(item.id),
          ),
        )}

        <div
          style={{
            height: 1,
            background: t.border,
            margin: "10px 8px",
          }}
        />

        {itemBtn(
          `Theme: ${themeName}`,
          themeIcon,
          false,
          onCycleTheme,
        )}
        {itemBtn("Sign out", "⏻", false, onLogout)}
      </aside>
    </>
  );
}
