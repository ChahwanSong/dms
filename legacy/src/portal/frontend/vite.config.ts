import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies the BFF so the SPA and /api share an origin (cookies work).
// PORTAL_BFF_URL overrides the BFF target for `npm run dev`.
const bffTarget = process.env.PORTAL_BFF_URL || "http://localhost:8090";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": { target: bffTarget, changeOrigin: true },
      "/healthz": { target: bffTarget, changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
  },
});
