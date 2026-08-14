import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // local dev only; in the container the api serves dist/ same-origin
    proxy: {
      "/chat": "http://localhost:8000",
      "/me": "http://localhost:8000",
      "/stats": "http://localhost:8000",
      "/ready": "http://localhost:8000",
    },
  },
});
