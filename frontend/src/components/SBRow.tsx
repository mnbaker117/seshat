import type { ReactNode } from "react";
import { useTheme } from "../theme";

export interface SBRowProps {
  label: string;
  value: ReactNode;
  color?: string;
}

export function SBRow({ label, value, color }: SBRowProps) {
  const t = useTheme();
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
      <span style={{ fontSize: 11, fontWeight: 600, color: t.tg, textTransform: "uppercase" }}>{label}</span>
      <span style={{ fontSize: 13, color: color || t.text2, textAlign: "right" }}>{value}</span>
    </div>
  );
}
