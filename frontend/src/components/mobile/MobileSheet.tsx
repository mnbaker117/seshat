// Bottom sheet wrapper. Slides up from the bottom edge, dims the
// background with a scrim, traps Escape to close.
//
// Used for action sheets (replaces dropdowns/menus on mobile —
// "Sort by" picker, "Filter" panel, etc.) and for full-screen modal
// content (AddBookModal, ExportModal, etc. when ported in Phase 5).
//
// Tap outside / Escape closes. The sheet itself has a grab handle
// at the top and rounded top corners. On iPad the sheet is centered
// and capped at 600px wide instead of edge-to-edge.
//
// Swipe-to-dismiss: dragging down on the grab handle or title header
// translates the sheet downward and closes when released past 100px.
// Content area still scrolls normally — drag only fires on the
// drag-zone (handle + header), not the body.
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useTheme } from "../../theme";
import { useViewport } from "../../hooks/useViewport";
import { RADIUS, scaleFor } from "./tokens";
import { MobileIconBtn } from "./MobileIconBtn";

export interface MobileSheetProps {
  open: boolean;
  onClose: () => void;
  // Title is rendered in the sticky header bar. Pass null to render
  // a sheet without a header (rare; useful for image lightboxes).
  title?: ReactNode;
  // Content height: "auto" hugs content (good for short menus),
  // "tall" caps at 90vh and lets content scroll (good for forms),
  // "full" takes the whole viewport (good for nested-page views).
  height?: "auto" | "tall" | "full";
  // Sticky bottom action bar (Cancel/Save row, etc.).
  footer?: ReactNode;
  children: ReactNode;
}

const DISMISS_THRESHOLD = 100; // px dragged before release closes the sheet

export function MobileSheet({
  open,
  onClose,
  title,
  height = "tall",
  footer,
  children,
}: MobileSheetProps) {
  const t = useTheme();
  const vp = useViewport();
  const s = scaleFor(vp);

  // Drag-to-dismiss state. dragStart = touch Y at touchstart, null
  // when not dragging. dragOffset = how far down the user has dragged
  // (only ever positive; upward drags are clamped to 0).
  const [dragStart, setDragStart] = useState<number | null>(null);
  const [dragOffset, setDragOffset] = useState(0);
  const sheetRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [open, onClose]);

  // Reset drag state whenever the sheet opens — defensive against a
  // sheet being re-opened mid-animation.
  useEffect(() => {
    if (open) {
      setDragStart(null);
      setDragOffset(0);
    }
  }, [open]);

  if (!open) return null;

  const sheetMaxHeight =
    height === "auto" ? "85vh" : height === "tall" ? "90vh" : "100vh";
  const sheetHeight = height === "full" ? "100vh" : undefined;

  // On iPad, center the sheet and cap width so it doesn't go
  // edge-to-edge on a wide tablet. On phones, full width.
  const isTablet = vp.isTablet;
  const sheetWidth = isTablet ? "min(600px, 92vw)" : "100%";
  const radius =
    height === "full" ? 0 : `${RADIUS.xl}px ${RADIUS.xl}px 0 0`;

  // Drag-to-dismiss: only enabled for non-full sheets. Full-height
  // sheets are page-like and shouldn't disappear with a stray swipe.
  const dragEnabled = height !== "full";

  const onDragStart = (clientY: number) => {
    if (!dragEnabled) return;
    setDragStart(clientY);
    setDragOffset(0);
  };
  const onDragMove = (clientY: number) => {
    if (!dragEnabled || dragStart === null) return;
    const delta = clientY - dragStart;
    // Only allow downward drag — upward drags would let the user
    // peel the sheet up past its natural height, which looks weird.
    setDragOffset(Math.max(0, delta));
  };
  const onDragEnd = () => {
    if (!dragEnabled || dragStart === null) return;
    if (dragOffset > DISMISS_THRESHOLD) {
      onClose();
    }
    // Either way, reset the drag state. If we closed, the sheet
    // unmounts on the next render and the snap-back animation is
    // moot. If we didn't, the transition kicks in to snap back.
    setDragStart(null);
    setDragOffset(0);
  };

  // Drag handlers attach to the grab-zone (handle + header). Touch
  // events use clientY directly; pointer events also work but are
  // less universally supported on iOS Safari. Sticking with touch.
  const dragHandlers = dragEnabled
    ? {
        onTouchStart: (e: React.TouchEvent) =>
          onDragStart(e.touches[0].clientY),
        onTouchMove: (e: React.TouchEvent) =>
          onDragMove(e.touches[0].clientY),
        onTouchEnd: onDragEnd,
        onTouchCancel: onDragEnd,
      }
    : {};

  // While dragging, kill the entrance animation + opacity-fade the
  // scrim proportional to drag distance for a tactile feel.
  const isDragging = dragStart !== null && dragOffset > 0;
  const scrimOpacity = isDragging
    ? Math.max(0, 0.5 - (dragOffset / 400) * 0.5)
    : 0.5;
  const sheetTransform = isDragging
    ? `translateY(${dragOffset}px)${isTablet ? " translateX(-50%)" : ""}`
    : isTablet
      ? "translateX(-50%)"
      : undefined;

  return (
    <>
      <div
        onClick={onClose}
        style={{
          position: "fixed",
          inset: 0,
          background: `rgba(0,0,0,${scrimOpacity})`,
          zIndex: 200,
          animation: isDragging ? undefined : "fade-in 0.18s ease-out",
          transition: isDragging ? undefined : "background 0.2s",
        }}
      />
      <div
        ref={sheetRef}
        role="dialog"
        aria-modal="true"
        style={{
          position: "fixed",
          left: isTablet ? "50%" : 0,
          right: isTablet ? "auto" : 0,
          bottom: 0,
          transform: sheetTransform,
          width: sheetWidth,
          maxHeight: sheetMaxHeight,
          height: sheetHeight,
          background: t.bg2,
          borderTop: `1px solid ${t.border}`,
          borderRadius: radius,
          zIndex: 201,
          display: "flex",
          flexDirection: "column",
          animation: isDragging ? undefined : "slide-up 0.22s ease-out",
          transition: isDragging ? undefined : "transform 0.2s ease-out",
          paddingBottom: "env(safe-area-inset-bottom, 0px)",
          touchAction: "pan-y",
        }}
      >
        {/* grab handle — drag-zone */}
        {height !== "full" && (
          <div
            {...dragHandlers}
            style={{
              padding: "8px 0 4px",
              display: "flex",
              justifyContent: "center",
              flexShrink: 0,
              cursor: "grab",
              touchAction: "none",
            }}
          >
            <span
              style={{
                width: 40,
                height: 4,
                borderRadius: 2,
                background: t.borderH,
              }}
            />
          </div>
        )}
        {title !== undefined && title !== null && (
          <header
            {...dragHandlers}
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              padding: `${s.space.md}px ${s.pad.normal}px`,
              borderBottom: `1px solid ${t.borderL}`,
              flexShrink: 0,
              touchAction: "none",
            }}
          >
            <h2
              style={{
                fontSize: s.type.heading,
                fontWeight: 700,
                color: t.text,
                margin: 0,
              }}
            >
              {title}
            </h2>
            <MobileIconBtn
              onClick={onClose}
              label="Close"
              // Stop the drag from firing when the user taps × — the
              // sheet shouldn't drag away from a button press.
              onTouchStart={(e) => e.stopPropagation()}
            >
              <span style={{ fontSize: 22 }}>×</span>
            </MobileIconBtn>
          </header>
        )}
        <div
          style={{
            flex: 1,
            overflowY: "auto",
            padding: s.pad.normal,
          }}
        >
          {children}
        </div>
        {footer && (
          <div
            style={{
              borderTop: `1px solid ${t.borderL}`,
              padding: s.pad.normal,
              display: "flex",
              gap: s.space.sm,
              flexShrink: 0,
              background: t.bg2,
            }}
          >
            {footer}
          </div>
        )}
      </div>
    </>
  );
}
