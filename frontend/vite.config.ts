import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const target = process.env.VITE_PROXY_TARGET ?? "http://localhost:8000";

// In dev, /api/* is forwarded to the FastAPI server (including the replay WebSocket).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      "/api": { target, ws: true, changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, "") },
    },
  },
});
