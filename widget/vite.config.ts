import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Library mode, single self-contained IIFE: dist/widget.js (React bundled in).
export default defineConfig({
  plugins: [react()],
  define: { "process.env.NODE_ENV": JSON.stringify("production") },
  build: {
    lib: {
      entry: "src/main.tsx",
      name: "NaisChatWidget",
      formats: ["iife"],
      fileName: () => "widget.js",
    },
    cssCodeSplit: false,
    emptyOutDir: true,
  },
});
