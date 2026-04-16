import type { ButtonHTMLAttributes, ReactNode } from "react";
import { useTheme } from "../theme";

type Variant = "primary" | "secondary" | "danger" | "ghost";

interface BtnProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  children: ReactNode;
  fullWidth?: boolean;
}

export function Btn({
  variant = "secondary",
  children,
  fullWidth,
  style,
  ...rest
}: BtnProps) {
  const theme = useTheme();
  const palette: Record<Variant, { bg: string; fg: string; border: string }> = {
    primary: { bg: theme.accent, fg: theme.bg, border: theme.accent },
    secondary: { bg: theme.bg3, fg: theme.text2, border: theme.border },
    danger: { bg: theme.err, fg: theme.bg, border: theme.err },
    ghost: { bg: "transparent", fg: theme.text2, border: theme.border },
  };
  const c = palette[variant];
  return (
    <button
      {...rest}
      style={{
        background: c.bg,
        color: c.fg,
        border: `1px solid ${c.border}`,
        padding: "8px 14px",
        borderRadius: 8,
        fontSize: 14,
        fontWeight: 600,
        cursor: "pointer",
        width: fullWidth ? "100%" : undefined,
        ...style,
      }}
    >
      {children}
    </button>
  );
}
