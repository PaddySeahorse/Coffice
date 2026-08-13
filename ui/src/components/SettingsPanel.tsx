// Settings panel (LLM endpoint config): the human edits the OpenAI-compatible
// base URL, model, and API key that drive the agent loop. Changes are applied
// to the in-process LLMClient and persisted by POST /settings; the test button
// pings the candidate endpoint before committing to it.

import { useEffect, useState } from "react";
import { fetchSettings, saveSettings, testSettings } from "../api/settingsApi";
import type { LlmSettings } from "../types";

interface SettingsPanelProps {
  onNotify: (kind: "info" | "success" | "error", text: string) => void;
}

type Notice = { kind: "info" | "success" | "error"; text: string };

export function SettingsPanel({ onNotify }: SettingsPanelProps) {
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [apiKeySet, setApiKeySet] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);

  useEffect(() => {
    let active = true;
    void fetchSettings().then((settings: LlmSettings) => {
      if (!active) return;
      setBaseUrl(settings.base_url);
      setModel(settings.model);
      setApiKeySet(settings.api_key_set);
    });
    return () => {
      active = false;
    };
  }, []);

  const update = () => ({
    base_url: baseUrl.trim(),
    model: model.trim(),
    api_key: apiKey || undefined,
  });

  const handleSave = async () => {
    if (!baseUrl.trim() || !model.trim()) {
      setNotice({ kind: "error", text: "Base URL and Model cannot be empty" });
      return;
    }
    setBusy(true);
    setNotice(null);
    try {
      const settings = await saveSettings(update());
      setApiKeySet(settings.api_key_set);
      setApiKey("");
      setNotice({ kind: "success", text: "LLM configuration saved" });
      onNotify("success", "LLM configuration saved");
    } catch (error) {
      const text = error instanceof Error ? error.message : String(error);
      setNotice({ kind: "error", text });
    } finally {
      setBusy(false);
    }
  };

  const handleTest = async () => {
    if (!baseUrl.trim() || !model.trim()) {
      setNotice({ kind: "error", text: "Base URL and Model cannot be empty" });
      return;
    }
    setBusy(true);
    setNotice(null);
    try {
      const result = await testSettings(update());
      setNotice(
        result.ok
          ? { kind: "success", text: `Connection successful: ${result.reply ?? "ok"}` }
          : { kind: "error", text: result.error ?? "Connection failed" },
      );
      onNotify(
        result.ok ? "success" : "error",
        result.ok ? "LLM connection successful" : (result.error ?? "LLM Connection failed"),
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel settings-panel" data-testid="settings-panel">
      <div className="panel-scroll">
        <h3 className="section-title">LLM Configuration</h3>
        <p className="panel-hint">
          Configure the conversational model endpoint driving the Agent (OpenAI-compatible API).
        </p>

        <label className="settings-field" data-testid="field-base-url">
          <span className="settings-label">Base URL</span>
          <input
            type="text"
            value={baseUrl}
            onChange={(event) => setBaseUrl(event.target.value)}
            placeholder="http://127.0.0.1:11434/v1"
            spellCheck={false}
            data-testid="input-base-url"
          />
        </label>

        <label className="settings-field" data-testid="field-model">
          <span className="settings-label">Model</span>
          <input
            type="text"
            value={model}
            onChange={(event) => setModel(event.target.value)}
            placeholder="qwen2.5:7b"
            spellCheck={false}
            data-testid="input-model"
          />
        </label>

        <label className="settings-field" data-testid="field-api-key">
          <span className="settings-label">API Key</span>
          <input
            type="password"
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
            placeholder={
              apiKeySet ? "Set, leave blank to keep unchanged" : "Optional (can be blank for local services)"
            }
            autoComplete="off"
            data-testid="input-api-key"
          />
        </label>

        {notice && (
          <p className={`settings-notice settings-notice--${notice.kind}`} data-testid="settings-notice">
            {notice.text}
          </p>
        )}

        <div className="settings-actions">
          <button
            type="button"
            className="btn"
            data-testid="btn-test-settings"
            disabled={busy}
            onClick={() => void handleTest()}
          >
            {busy ? "Testing..." : "Test Connection"}
          </button>
          <button
            type="button"
            className="btn btn--primary"
            data-testid="btn-save-settings"
            disabled={busy}
            onClick={() => void handleSave()}
          >
            Save
          </button>
        </div>
      </div>
    </section>
  );
}
