import { useState, useEffect, useRef } from "react";
import { useTheme } from "../theme";
import { EVT } from "../types";
import type { ToastDetail, ToastKind } from "../lib/toast";

// Top-banner toast notification system. Listens for the
// `seshat:toast` window event (dispatched via `lib/toast.ts`)
// and renders a vertical stack of fading banners at the top of the
// viewport. Mounted once at the App level so every page shares the
// same stack and no two pages can render competing toasters.
//
// Lifecycle per toast:
//   1. Event arrives → push to state with `entering: true`.
//   2. Next animation frame → flip `entering: false` so the
//      slide-down transition runs.
//   3. After 5s → flip `exiting: true` to run the slide-up + fade.
//   4. After 300ms exit → remove the toast from state.
//   5. A click anywhere on the toast jumps straight to step 3.
//
// The two-step entering/false flip is required because if we mounted
// in the final position immediately, the CSS transition would have
// nothing to interpolate from and the animation wouldn't run.

let nextId = 1;

interface ToastState {
  id: number;
  kind: ToastKind;
  msg: string;
  entering: boolean;
  exiting: boolean;
}

const KIND_STYLES: Record<ToastKind, { bg: string; border: string }> = {
  // bg uses RGBA with alpha so the banner is semi-transparent
  // over whatever's behind it (a la macOS notification center).
  info:    { bg: "rgba(64, 116, 196, 0.92)",  border: "rgba(110, 158, 230, 0.5)" },
  success: { bg: "rgba(60, 145, 90, 0.92)",   border: "rgba(110, 200, 140, 0.5)" },
  warn:    { bg: "rgba(190, 145, 50, 0.92)",  border: "rgba(230, 190, 100, 0.5)" },
  error:   { bg: "rgba(180, 60, 70, 0.92)",   border: "rgba(220, 110, 120, 0.5)" },
};

const ICONS: Record<ToastKind, string> = { info: "ℹ", success: "✓", warn: "⚠", error: "✕" };

export default function Toaster() {
  const t = useTheme();
  void t; // theme not currently used; reserved for future per-theme styling
  const [toasts, setToasts] = useState<ToastState[]>([]);
  // Refs to per-toast timeout handles so we can cancel them on click-dismiss.
  const timers = useRef<Map<number, ReturnType<typeof setTimeout>>>(new Map());

  useEffect(() => {
    const onToast = (e: Event) => {
      const detail = (e as CustomEvent<ToastDetail>).detail || { kind: "info" as ToastKind, msg: "" };
      const kind: ToastKind = detail.kind || "info";
      const msg = detail.msg || "";
      const id = nextId++;
      // Insert with `entering: true`. A second setState on next frame
      // flips it false to trigger the slide-down animation. Without the
      // two-step, the new node mounts already in its final position and
      // the transition has nothing to animate from.
      setToasts(prev => [...prev, { id, kind, msg, entering: true, exiting: false }]);
      requestAnimationFrame(() => {
        setToasts(prev => prev.map(x => x.id === id ? { ...x, entering: false } : x));
      });
      // Auto-dismiss after 5s
      const dismissTimer = setTimeout(() => dismiss(id), 5000);
      timers.current.set(id, dismissTimer);
    };
    window.addEventListener(EVT.Toast, onToast);
    return () => {
      window.removeEventListener(EVT.Toast, onToast);
      // Clear any pending dismiss timers on unmount
      for (const [, h] of timers.current) clearTimeout(h);
      timers.current.clear();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const dismiss = (id: number): void => {
    // Cancel any pending auto-dismiss timer (click-to-dismiss path).
    const h = timers.current.get(id);
    if (h) { clearTimeout(h); timers.current.delete(id); }
    setToasts(prev => prev.map(x => x.id === id ? { ...x, exiting: true } : x));
    // Remove after exit animation completes
    setTimeout(() => {
      setToasts(prev => prev.filter(x => x.id !== id));
    }, 300);
  };

  if (toasts.length === 0) return null;

  return (
    <div style={{
      position: "fixed",
      top: 12,
      left: "50%",
      transform: "translateX(-50%)",
      zIndex: 9999,
      display: "flex",
      flexDirection: "column",
      gap: 8,
      pointerEvents: "none",
      maxWidth: "calc(100vw - 24px)",
    }}>
      {toasts.map(toast => {
        const styles = KIND_STYLES[toast.kind] || KIND_STYLES.info;
        const offsetY = toast.entering ? -40 : toast.exiting ? -20 : 0;
        const opacity = toast.entering || toast.exiting ? 0 : 1;
        return (
          <div
            key={toast.id}
            onClick={() => dismiss(toast.id)}
            style={{
              padding: "10px 18px",
              minWidth: 240,
              maxWidth: 520,
              borderRadius: 12,
              background: styles.bg,
              border: `1px solid ${styles.border}`,
              color: "#fff",
              fontSize: 14,
              fontWeight: 500,
              boxShadow: "0 8px 24px rgba(0, 0, 0, 0.35)",
              backdropFilter: "blur(10px)",
              WebkitBackdropFilter: "blur(10px)",
              opacity,
              transform: `translateY(${offsetY}px)`,
              transition: "opacity 280ms ease, transform 280ms ease",
              pointerEvents: "auto",
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              gap: 12,
            }}
          >
            <span style={{ fontSize: 16, opacity: 0.9 }}>{ICONS[toast.kind] || "•"}</span>
            <span style={{ flex: 1, lineHeight: 1.4 }}>{toast.msg}</span>
          </div>
        );
      })}
    </div>
  );
}
