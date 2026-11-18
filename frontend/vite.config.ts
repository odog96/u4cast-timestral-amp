import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The web server serves the built files. During development, `npm run dev`
// serves the front end and passes API calls on to the web server.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8123" } },
  build: { outDir: "dist", sourcemap: false },
});
