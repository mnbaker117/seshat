import type { ReactNode } from "react";
import { useTheme } from "../theme";

export function Section({
  title,
  subtitle,
  children,
  right,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  right?: ReactNode;
}) {
  const theme = useTheme();
  return (
    <section
      style={{
        background: theme.bg2,
        border: `1px solid ${theme.borderL}`,
        borderRadius: 12,
        padding: 20,
        marginBottom: 16,
      }}
    >
      <header
        style={{
          display: "flex",
          alignItems: "flex-start",
          justifyContent: "space-between",
          gap: 12,
          marginBottom: 16,
        }}
      >
        <div>
          <h2 style={{ fontSize: 16, fontWeight: 700, color: theme.text }}>
            {title}
          </h2>
          {subtitle && (
            <p
              style={{
                fontSize: 13,
                color: theme.textDim,
                marginTop: 4,
              }}
            >
              {subtitle}
            </p>
          )}
        </div>
        {right && <div>{right}</div>}
      </header>
      {children}
    </section>
  );
}
