import { useTheme } from "../theme";

export function Spin({ size = 22 }: { size?: number }) {
  const theme = useTheme();
  return (
    <div
      role="status"
      aria-label="Loading"
      style={{
        width: size,
        height: size,
        border: `2px solid ${theme.border}`,
        borderTopColor: theme.accent,
        borderRadius: "50%",
        animation: "spin 0.8s linear infinite",
      }}
    />
  );
}
