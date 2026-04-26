// Back button for mobile pages. Reads the navigation history from
// useNavigation() and renders nothing when there's no history — so
// dashboards (where the user typically lands first) don't show a
// back arrow that would lead nowhere.
//
// Drop this at the top of every mobile page; it self-hides when
// not applicable.
import { useTheme } from "../../theme";
import { useNavigation } from "../../providers/NavigationProvider";
import { TAP, RADIUS } from "./tokens";

export function MobileBackButton() {
  const t = useTheme();
  const { navBack, canGoBack } = useNavigation();

  if (!canGoBack) return null;

  return (
    <button
      onClick={navBack}
      aria-label="Back"
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        height: TAP.min,
        padding: "0 14px 0 10px",
        background: t.bg3,
        color: t.text2,
        border: `1px solid ${t.border}`,
        borderRadius: RADIUS.full,
        fontSize: 14,
        fontWeight: 600,
        cursor: "pointer",
        alignSelf: "flex-start",
      }}
    >
      <span style={{ fontSize: 18, lineHeight: 1 }}>‹</span>
      <span>Back</span>
    </button>
  );
}
