/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The Agent Deck is served by `make run-ui` and loaded by the LO sidebar shell
// (src/coffice/sidebar/contract.py): DEFAULT_UI_PORT = 8787 on 127.0.0.1.
const UI_PORT = Number(process.env.COFFICE_UI_PORT ?? 8787);

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: UI_PORT,
  },
  preview: {
    host: "127.0.0.1",
    port: UI_PORT,
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
