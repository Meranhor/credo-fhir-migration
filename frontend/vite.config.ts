import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [vue()],
  server: {
    // Same-origin calls in dev: no CORS configuration needed on the Django side.
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
});
