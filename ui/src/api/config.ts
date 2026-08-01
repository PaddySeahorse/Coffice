// Environment-driven configuration for the Agent Deck.
//
// The UI talks to the agent HTTP facade (agent bead) over localhost and is
// served on the origin the LO sidebar shell loads (sidebar bead contract:
// DEFAULT_UI_PORT = 8787). Override at dev time with VITE_* env vars.

export interface ApiConfig {
  /** Base URL of the agent HTTP facade (POST /chat, /confirm, GET /tools...). */
  agentBaseUrl: string;
  /** Port the Vite dev/preview server binds (matches COFFICE_UI_PORT). */
  uiPort: number;
  /** Abort timeout for API calls before falling back to mock data (ms). */
  requestTimeoutMs: number;
}

function normalizeBaseUrl(url: string): string {
  return url.endsWith("/") ? url : `${url}/`;
}

export function getApiConfig(): ApiConfig {
  const env = import.meta.env ?? {};
  const agentBaseUrl = normalizeBaseUrl(
    String(env.VITE_COFFICE_AGENT_URL ?? "http://127.0.0.1:8790/"),
  );
  const uiPort = Number(env.VITE_COFFICE_UI_PORT ?? 8787);
  return {
    agentBaseUrl,
    uiPort: Number.isFinite(uiPort) ? uiPort : 8787,
    requestTimeoutMs: 3000,
  };
}
