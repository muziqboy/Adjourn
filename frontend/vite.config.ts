import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// BACKEND_PORT overrides 8010 only when 8010 is taken on this machine
const backend = process.env.BACKEND_PORT ?? "8010";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": `http://127.0.0.1:${backend}`,
      "/ws": { target: `ws://127.0.0.1:${backend}`, ws: true },
    },
  },
});
