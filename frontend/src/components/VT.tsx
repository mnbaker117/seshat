import { useTheme } from "../theme";

export type ViewMode = "grid" | "list";

export interface VTProps {
  mode: ViewMode;
  setMode: (m: ViewMode) => void;
}

export function VT({ mode, setMode }: VTProps) {
  const t = useTheme();
  const modes: ViewMode[] = ["grid", "list"];
  return (
    <div style={{ display: "flex", borderRadius: 6, border: `1px solid ${t.border}`, overflow: "hidden", height: 34 }}>
      {modes.map(m => (
        <button
          key={m}
          onClick={() => setMode(m)}
          style={{
            padding: "0 12px", fontSize: 12, fontWeight: 500, border: "none", cursor: "pointer",
            background: mode === m ? t.bg4 : "transparent",
            color: mode === m ? t.accent : t.tg,
            textTransform: "capitalize", height: "100%",
          }}
        >{m === "grid" ? "Grid" : "List"}</button>
      ))}
    </div>
  );
}
