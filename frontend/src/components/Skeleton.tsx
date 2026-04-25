// Skeleton-loader primitives + a book-grid variant.
//
// Replaces the centered `<Load />` spinner on book-list pages so the
// page shape locks in BEFORE data arrives — covers, sticky header,
// pagination row stay where they're going to be, and the user gets
// a "the page is loading" signal that matches the eventual content
// instead of a blank rectangle with a spinner in the middle.
//
// The pulse animation is keyframed in index.css (`@keyframes pulse`),
// so this stays a thin React layer over CSS. We don't precisely match
// every BCard pixel — the goal is shape recognition, not deception.
import { useTheme } from "../theme";

interface SkeletonProps {
  width?: number | string;
  height?: number | string;
  radius?: number;
  style?: React.CSSProperties;
}

// Single shimmer block — used as a building primitive.
export function Skeleton({
  width = "100%",
  height = 14,
  radius = 4,
  style,
}: SkeletonProps) {
  const t = useTheme();
  return (
    <div
      aria-hidden="true"
      style={{
        width,
        height,
        borderRadius: radius,
        background: t.bg3,
        animation: "pulse 1.4s ease-in-out infinite",
        ...style,
      }}
    />
  );
}

// Mimics a single BCard's shape — cover block + 2 title lines + a
// short author line. `flex: 1 1 160px; minWidth 160; maxWidth 200`
// matches BCard so the grid layout doesn't reshuffle when the real
// cards land.
export function BookCardSkeleton() {
  const t = useTheme();
  return (
    <div
      style={{
        flex: "1 1 160px",
        minWidth: 160,
        maxWidth: 200,
        background: t.bg2,
        border: `1px solid ${t.border}`,
        borderRadius: 10,
        overflow: "hidden",
      }}
    >
      <Skeleton width="100%" height={200} radius={0} />
      <div style={{ padding: 10, display: "flex", flexDirection: "column", gap: 6 }}>
        <Skeleton height={12} />
        <Skeleton height={12} width="70%" />
        <Skeleton height={10} width="50%" style={{ marginTop: 4 }} />
      </div>
    </div>
  );
}

// Grid of card skeletons matching `BGrid`'s flex-wrap layout. Default
// count is 12 — fills the typical first viewport on both desktop and
// phone without overshooting paged batch size (60).
export function BookGridSkeleton({ count = 12 }: { count?: number }) {
  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        gap: 12,
        alignItems: "start",
      }}
    >
      {Array.from({ length: count }).map((_, i) => (
        <BookCardSkeleton key={i} />
      ))}
    </div>
  );
}
