import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the API runs on :8080 (python -m moda_ui serve).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": "http://localhost:8080" },
  },
  build: { outDir: "dist", chunkSizeWarningLimit: 2000 },
});
