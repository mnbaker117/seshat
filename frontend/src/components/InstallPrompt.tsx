// Custom "Install Seshat" prompt.
//
// Chrome / Edge / Android Chrome fire a `beforeinstallprompt` event
// when the app meets the PWA install criteria AND the user hasn't
// already installed it. We capture the event, stash it, and render
// our own install button — that way we pick WHEN to show it (after
// 30s of active session, rather than on first paint) and it fits
// the Seshat aesthetic.
//
// iOS Safari never fires beforeinstallprompt — users install via
// Share → Add to Home Screen. The button silently stays hidden on
// iOS, which is the right default (showing a button that does
// nothing would be worse than showing no button).
//
// Dismissal is sticky via localStorage — one "not now" and the
// prompt stays hidden for 30 days.
import { useEffect, useRef, useState } from "react";
import { useTheme } from "../theme";

type BeforeInstallPromptEvent = Event & {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
};

const LS_DISMISSED_AT = "seshat_pwa_install_dismissed_at";
const DISMISS_DURATION_MS = 30 * 24 * 60 * 60 * 1000; // 30 days
const SHOW_AFTER_MS = 30 * 1000; // 30 seconds of session time

function dismissalActive(): boolean {
  try {
    const raw = localStorage.getItem(LS_DISMISSED_AT);
    if (!raw) return false;
    const at = Number(raw);
    if (!Number.isFinite(at)) return false;
    return Date.now() - at < DISMISS_DURATION_MS;
  } catch {
    return false;
  }
}

export function InstallPrompt() {
  const t = useTheme();
  const [event, setEvent] = useState<BeforeInstallPromptEvent | null>(null);
  const [show, setShow] = useState(false);
  const timerRef = useRef<number | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (dismissalActive()) return;

    const onBeforeInstall = (e: Event) => {
      e.preventDefault();
      setEvent(e as BeforeInstallPromptEvent);
      // Defer showing the UI so it doesn't land mid-first-paint.
      // Gives the user 30s to get oriented before we interrupt.
      timerRef.current = window.setTimeout(() => {
        setShow(true);
      }, SHOW_AFTER_MS);
    };
    const onInstalled = () => {
      setShow(false);
      setEvent(null);
      // No need to record dismissal — installed apps don't re-fire
      // beforeinstallprompt, so this is a natural terminal state.
    };

    window.addEventListener("beforeinstallprompt", onBeforeInstall);
    window.addEventListener("appinstalled", onInstalled);

    return () => {
      window.removeEventListener("beforeinstallprompt", onBeforeInstall);
      window.removeEventListener("appinstalled", onInstalled);
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
      }
    };
  }, []);

  const handleInstall = async () => {
    if (!event) return;
    try {
      await event.prompt();
      const { outcome } = await event.userChoice;
      if (outcome === "dismissed") {
        try {
          localStorage.setItem(LS_DISMISSED_AT, String(Date.now()));
        } catch {
          /* localStorage blocked — silent */
        }
      }
    } catch {
      /* prompt() can reject if user-gesture budget expired */
    }
    setShow(false);
    setEvent(null);
  };

  const handleDismiss = () => {
    try {
      localStorage.setItem(LS_DISMISSED_AT, String(Date.now()));
    } catch {
      /* localStorage blocked — silent */
    }
    setShow(false);
  };

  if (!show || !event) return null;

  return (
    <div
      role="dialog"
      aria-labelledby="seshat-install-title"
      style={{
        position: "fixed",
        bottom: 16,
        right: 16,
        zIndex: 60,
        maxWidth: 340,
        padding: "12px 14px",
        background: t.bg2,
        border: `1px solid ${t.accent}66`,
        borderRadius: 10,
        boxShadow: "0 8px 22px rgba(0,0,0,0.4)",
        display: "flex",
        flexDirection: "column",
        gap: 8,
      }}
    >
      <div
        id="seshat-install-title"
        style={{
          fontSize: 14,
          fontWeight: 700,
          color: t.accent,
        }}
      >
        Install Seshat
      </div>
      <div style={{ fontSize: 12, color: t.text2, lineHeight: 1.45 }}>
        Add Seshat as an app on this device. Opens in its own window,
        skips the browser bar, and keeps browsing working even when
        the connection drops.
      </div>
      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
        <button
          onClick={handleDismiss}
          style={{
            padding: "6px 12px",
            fontSize: 12,
            fontWeight: 500,
            background: "transparent",
            color: t.tg,
            border: `1px solid ${t.border}`,
            borderRadius: 6,
            cursor: "pointer",
          }}
        >
          Not now
        </button>
        <button
          onClick={handleInstall}
          style={{
            padding: "6px 12px",
            fontSize: 12,
            fontWeight: 700,
            background: t.accent,
            color: t.bg,
            border: `1px solid ${t.accent}`,
            borderRadius: 6,
            cursor: "pointer",
          }}
        >
          Install
        </button>
      </div>
    </div>
  );
}
