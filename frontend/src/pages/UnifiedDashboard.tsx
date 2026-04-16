// Unified Dashboard — combines Discovery + Pipeline dashboards.
//
// Two-column layout on wide screens, stacked on narrow. Shows all
// stats from both domains in one view — "all the goodies."
import { useTheme } from "../theme";
import PipelineDashboard from "./Dashboard";
import DiscDashboard from "./DiscDashboard";

interface Props {
  onNav: (page: string, arg?: string | number | null) => void;
}

export default function UnifiedDashboard({ onNav }: Props) {
  const t = useTheme();
  return (
    <div>
      <div style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        marginBottom: 24,
      }}>
        <h1 style={{
          fontSize: 26,
          fontWeight: 800,
          color: t.accent,
          margin: 0,
          letterSpacing: "0.01em",
        }}>
          𓋹 Dashboard
        </h1>
        <span style={{ fontSize: 13, color: t.td }}>
          Discovery + Pipeline
        </span>
      </div>

      <div style={{
        display: "grid",
        gridTemplateColumns: "1fr 1fr",
        gap: 20,
        alignItems: "start",
      }}>
        {/* Discovery column */}
        <div>
          <div style={{
            fontSize: 13,
            fontWeight: 700,
            color: t.accent,
            textTransform: "uppercase",
            letterSpacing: "0.05em",
            marginBottom: 12,
            paddingBottom: 6,
            borderBottom: `1px solid ${t.border}`,
          }}>
            Discovery
          </div>
          <DiscDashboard onNav={onNav} />
        </div>

        {/* Pipeline column */}
        <div>
          <div style={{
            fontSize: 13,
            fontWeight: 700,
            color: t.jade || t.accent,
            textTransform: "uppercase",
            letterSpacing: "0.05em",
            marginBottom: 12,
            paddingBottom: 6,
            borderBottom: `1px solid ${t.border}`,
          }}>
            Pipeline
          </div>
          <PipelineDashboard onNav={onNav} />
        </div>
      </div>
    </div>
  );
}
