// Visibility-aware interval hook.
//
// Behaves like a `setInterval` that pauses when the browser tab is
// backgrounded (`document.hidden`) and resumes when it returns to
// the foreground. Saves network traffic + battery on tabs the user
// has parked in the background — e.g., the Dashboard polling every
// few seconds while you're using a different tab adds up.
//
// On `visibilitychange` back to visible, fires the callback once
// immediately so the UI catches up to whatever happened while the
// tab was hidden, then resumes the interval cadence.
//
// Usage:
//   useVisibleInterval(() => refresh(), 5000);
//
// Pass `delayMs <= 0` or a falsy callback to disable.
//
// Reference impl: AthenaScout commit `7858852` (Sprint 7.3) — same
// pattern, different filename.
import { useEffect, useRef } from "react";

export function useVisibleInterval(
  callback: (() => void | Promise<void>) | null | undefined,
  delayMs: number,
): void {
  const savedCb = useRef<typeof callback>(callback);
  useEffect(() => { savedCb.current = callback; }, [callback]);

  useEffect(() => {
    if (!savedCb.current || !delayMs || delayMs <= 0) return;

    let iv: ReturnType<typeof setInterval> | null = null;
    let cancelled = false;

    const tick = () => {
      if (document.hidden || cancelled) return;
      try {
        const result = savedCb.current?.();
        if (result && typeof (result as Promise<void>).catch === "function") {
          (result as Promise<void>).catch(() => { /* swallow */ });
        }
      } catch { /* swallow */ }
    };

    const start = () => {
      if (iv !== null) clearInterval(iv);
      iv = setInterval(tick, delayMs);
    };

    const onVis = () => {
      if (cancelled) return;
      if (document.hidden) {
        if (iv !== null) { clearInterval(iv); iv = null; }
      } else {
        // Catch-up tick on return so the UI doesn't stay stale until
        // the next interval boundary, then resume normal cadence.
        tick();
        start();
      }
    };

    start();
    document.addEventListener("visibilitychange", onVis);
    return () => {
      cancelled = true;
      if (iv !== null) clearInterval(iv);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [delayMs]);
}
