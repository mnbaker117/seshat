import { useTheme } from "../theme";
import { pct } from "../lib/format";

export interface PBProps {
  owned: number;
  total: number;
}

export function PB({ owned, total }: PBProps) {
  const t = useTheme();
  const p = pct(owned, total);
  return (
    <div style={{ height: 5, borderRadius: 3, background: t.bg4, overflow: "hidden" }}>
      <div
        style={{
          width: `${p}%`,
          height: "100%",
          borderRadius: 3,
          background: p === 100 ? t.grn : p > 50 ? t.ylw : t.red,
          transition: "width 0.3s",
        }}
      />
    </div>
  );
}
