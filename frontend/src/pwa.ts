// PWA service-worker registration.
//
// vite-plugin-pwa injects `virtual:pwa-register` at build time with
// the right service-worker URL and scope. The register call is a
// no-op in browsers that don't support service workers AND in
// non-secure contexts (plain-HTTP LAN origins) — the plugin's
// wrapper swallows the registration error silently in those cases.
// We add a console log explaining the skip so future diagnostics
// (after Mark puts Seshat behind HTTPS) can confirm whether the
// handoff worked without digging through chrome://serviceworker-internals.
//
// autoUpdate mode means a new SW installs immediately when the page
// loads with a fresh Vite build, and — combined with the `skipWaiting`
// + `clientsClaim` flags in vite.config — activates on the same load.
// No "reload for new version" prompt; the cache just refreshes.
import { registerSW } from "virtual:pwa-register";

export function initPwa(): void {
  if (typeof window === "undefined") return;

  const isSecure = window.isSecureContext;
  const canRegister = "serviceWorker" in navigator;

  if (!canRegister) {
    console.info("[pwa] Service workers not supported — running as plain SPA.");
    return;
  }
  if (!isSecure) {
    console.info(
      "[pwa] Non-secure context — service worker not registering. " +
        "Seshat runs as a plain SPA. Put Seshat behind HTTPS to enable PWA features.",
    );
    return;
  }

  registerSW({
    immediate: true,
    onRegisteredSW(swUrl) {
      console.info(`[pwa] Service worker registered at ${swUrl}`);
    },
    onRegisterError(error) {
      console.warn("[pwa] Service worker registration failed:", error);
    },
  });
}
