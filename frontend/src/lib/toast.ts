// Lightweight toast notification dispatcher.
//
// Replaces alert() / confirm() popups for transient status messages
// (scan started, scan rejected, MAM finished, etc). The actual
// rendering lives in components/Toaster.tsx — this module just fires
// a window event so any non-React caller (api.ts error handlers,
// global window listeners, etc) can pop a toast without importing
// React.
//
// Usage:
//   import { toast } from "./lib/toast";
//   toast.info("Scan started for Brandon Sanderson");
//   toast.success("MAM scan complete");
//   toast.error("An author scan is already running");
//
// kinds: info | success | warn | error
//
// The Toaster component listens for "athenascout:toast" events and
// renders an iOS-style banner stack at the top of the viewport that
// auto-dismisses after ~5s and is click-to-dismiss.

import { EVT } from "../types";

export type ToastKind = "info" | "success" | "warn" | "error";

export interface ToastDetail {
  kind: ToastKind;
  msg: string;
}

function fire(kind: ToastKind, msg: unknown): void {
  try {
    window.dispatchEvent(
      new CustomEvent<ToastDetail>(EVT.Toast, {
        detail: { kind, msg: String(msg ?? "") },
      }),
    );
  } catch {
    /* ignore */
  }
}

export const toast = {
  info:    (msg: unknown): void => fire("info", msg),
  success: (msg: unknown): void => fire("success", msg),
  warn:    (msg: unknown): void => fire("warn", msg),
  error:   (msg: unknown): void => fire("error", msg),
};
