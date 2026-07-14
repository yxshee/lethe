import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const backend = "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": backend,
      "/healthz": backend,
      "/readyz": backend,
    },
  },
  preview: {
    host: "127.0.0.1",
    port: 4173,
    strictPort: true,
  },
});
