import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Vite config for the Seshat frontend.
//
// - Dev server proxies /api → the FastAPI backend on :8789 so cookies
//   work without CORS shenanigans.
// - Build emits to ./dist; FastAPI mounts that at runtime.
// - Manual chunk for the React vendor bundle keeps page chunks small
//   and lets the browser cache react/react-dom across deploys.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://10.0.10.20:8789",
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
