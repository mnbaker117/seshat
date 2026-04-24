import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

// Vite config for the Seshat frontend.
//
// - Dev server proxies /api → the FastAPI backend on :8789 so cookies
//   work without CORS shenanigans.
// - Build emits to ./dist; FastAPI mounts that at runtime.
// - Manual chunk for the React vendor bundle keeps page chunks small
//   and lets the browser cache react/react-dom across deploys.
// - VitePWA generates the web-app manifest + service worker. The
//   service worker only activates under HTTPS (or localhost) —
//   browsers refuse to register SWs on plain-HTTP LAN origins, so on
//   http://<lan-ip> Seshat degrades gracefully to a regular SPA. When
//   Mark fronts Seshat with a reverse proxy + cert later, the PWA
//   layer lights up automatically on the next page load.
export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      // Silent auto-update: a new build auto-installs and activates on
      // the next full page load. `registerType: 'autoUpdate'`
      // combined with `skipWaiting` + `clientsClaim` below skips the
      // "waiting" limbo that the default `prompt` flow uses. No
      // user-facing "new version available" dialog — Mark ships by
      // pushing to main, the next visit just picks up the new assets.
      registerType: "autoUpdate",
      includeAssets: [
        "favicon.png",
        "icon.svg",
        "apple-touch-icon.png",
        "icon-16.png",
        "icon-32.png",
        "icon-180.png",
        "icon-512.png",
      ],
      manifest: {
        name: "Seshat",
        short_name: "Seshat",
        description:
          "Book discovery + MAM automation — unified library management",
        // Match the Egyptian-goddess dark theme. These drive the
        // standalone-window chrome color on Android + desktop.
        theme_color: "#e4b868",
        background_color: "#1a1c30",
        display: "standalone",
        orientation: "any",
        scope: "/",
        start_url: "/",
        icons: [
          {
            src: "/icon-180.png",
            sizes: "180x180",
            type: "image/png",
          },
          {
            src: "/icon-512.png",
            sizes: "512x512",
            type: "image/png",
          },
          // Same 512 reused as a maskable variant — Android adaptive
          // icons crop to a safe zone, and a fully-populated 512
          // reads correctly enough without a dedicated maskable. Can
          // swap in a real maskable source if icons look cropped.
          {
            src: "/icon-512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "maskable",
          },
        ],
      },
      workbox: {
        // Activate the new SW immediately on install instead of the
        // default "wait until all tabs close" behavior.
        skipWaiting: true,
        clientsClaim: true,
        // Precache the Vite build output (app shell). Runtime
        // caching rules for the API surface live in the next commit.
        globPatterns: ["**/*.{js,css,html,ico,png,svg,woff2}"],
        // SPA fallback: navigations resolve to index.html so
        // deep-linked routes work offline after the shell is cached.
        navigateFallback: "/index.html",
        navigateFallbackDenylist: [/^\/api\//],
      },
      // Dev build: SW runs in dev too so we can test registration
      // and caching locally without doing a full production build
      // each iteration. Note: service workers only register in
      // secure contexts (localhost is always secure; LAN IPs are
      // not) — this is a real browser constraint, not a config.
      devOptions: {
        enabled: true,
        type: "module",
      },
    }),
  ],
  server: {
    proxy: {
      "/api": "http://localhost:8789",
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    minify: "esbuild",
    target: "es2020",
    cssCodeSplit: true,
    reportCompressedSize: false,
    rollupOptions: {
      output: {
        manualChunks: {
          "react-vendor": ["react", "react-dom"],
        },
      },
    },
  },
});
