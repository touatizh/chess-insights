import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev proxy: the typed client uses relative `/api` paths so the same code works
// behind the Phase 5 nginx `/api` proxy. In dev, forward to the FastAPI service.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
