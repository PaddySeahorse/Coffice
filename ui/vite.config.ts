/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The Agent Deck is served by `make run-ui` and loaded by the LO sidebar shell
// (src/coffice/sidebar/contract.py): DEFAULT_UI_PORT = 8787 on 127.0.0.1.
const UI_PORT = Number(process.env.COFFICE_UI_PORT ?? 8787);

// Reverse-proxy the agent HTTP facade so the deck works in single-port
// previews (frontend-reverse-proxy): /api/* -> http://127.0.0.1:8790/*
const AGENT_TARGET =
  process.env.COFFICE_AGENT_TARGET ?? "http://127.0.0.1:8790";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: UI_PORT,
    allowedHosts: [".monkeycode-ai.online"],
    proxy: {
      "/api": {
        target: AGENT_TARGET,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  preview: {
    host: "0.0.0.0",
    port: UI_PORT,
    allowedHosts: [".monkeycode-ai.online"],
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
