import { useTheme } from "../theme";
import { Spin } from "./Spin";

export function Load() {
  const t = useTheme();
  return (
    <div style={{ display: "flex", justifyContent: "center", padding: 60 }}>
      <Spin />
      <span style={{ marginLeft: 10, color: t.td }}>Loading...</span>
    </div>
  );
}
