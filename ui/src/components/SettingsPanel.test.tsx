// Unit tests for the Settings panel (LLM endpoint config): loads current
// settings, pre-fills the form, saves a change (API key only sent when typed),
// and reports connection-probe results inline.

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsPanel } from "./SettingsPanel";

const mocks = vi.hoisted(() => ({
  fetchSettings: vi.fn(),
  saveSettings: vi.fn(),
  testSettings: vi.fn(),
}));

vi.mock("../api/settingsApi", () => ({
  fetchSettings: mocks.fetchSettings,
  saveSettings: mocks.saveSettings,
  testSettings: mocks.testSettings,
}));

const CURRENT_SETTINGS = {
  base_url: "http://127.0.0.1:11434/v1",
  model: "qwen2.5:7b",
  api_key: "sk-a***wxyz",
  api_key_set: true,
};

function renderPanel() {
  return render(<SettingsPanel onNotify={vi.fn()} />);
}

describe("SettingsPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });
  it("loads and pre-fills the current endpoint config", async () => {
    mocks.fetchSettings.mockResolvedValue(CURRENT_SETTINGS);
    renderPanel();

    await waitFor(() => {
      expect(screen.getByTestId("input-base-url")).toHaveValue(CURRENT_SETTINGS.base_url);
    });
    expect(screen.getByTestId("input-model")).toHaveValue(CURRENT_SETTINGS.model);
    // the key is never loaded back into the field; placeholder hints it is set
    expect(screen.getByTestId("input-api-key")).toHaveValue("");
    expect(screen.getByTestId("input-api-key")).toHaveAttribute(
      "placeholder",
      "已设置，留空保持不变",
    );
  });

  it("saves base_url and model but omits a blank API key", async () => {
    mocks.fetchSettings.mockResolvedValue({ ...CURRENT_SETTINGS, api_key_set: false });
    mocks.saveSettings.mockResolvedValue({
      base_url: "http://lm.test/v1",
      model: "lm-1",
      api_key: null,
      api_key_set: false,
    });
    const user = userEvent.setup();
    renderPanel();

    const baseUrl = await screen.findByTestId("input-base-url");
    const model = screen.getByTestId("input-model");
    const apiKey = screen.getByTestId("input-api-key");
    await user.clear(baseUrl);
    await user.type(baseUrl, "http://lm.test/v1");
    await user.clear(model);
    await user.type(model, "lm-1");
    await user.click(screen.getByTestId("btn-save-settings"));

    await waitFor(() => {
      expect(mocks.saveSettings).toHaveBeenCalledWith({
        base_url: "http://lm.test/v1",
        model: "lm-1",
        api_key: undefined,
      });
    });
    expect(await screen.findByTestId("settings-notice")).toHaveTextContent(
      "LLM 配置已保存",
    );
    // the key field clears after a successful save
    await waitFor(() => expect(apiKey).toHaveValue(""));
  });

  it("sends the API key when the user typed one", async () => {
    mocks.fetchSettings.mockResolvedValue({ ...CURRENT_SETTINGS, api_key_set: false });
    mocks.saveSettings.mockResolvedValue(CURRENT_SETTINGS);
    const user = userEvent.setup();
    renderPanel();

    const apiKey = await screen.findByTestId("input-api-key");
    await user.type(apiKey, "sk-new-secret");
    await user.click(screen.getByTestId("btn-save-settings"));

    await waitFor(() => {
      expect(mocks.saveSettings).toHaveBeenCalledWith({
        base_url: CURRENT_SETTINGS.base_url,
        model: CURRENT_SETTINGS.model,
        api_key: "sk-new-secret",
      });
    });
  });

  it("reports the connection-probe result inline", async () => {
    mocks.fetchSettings.mockResolvedValue({ ...CURRENT_SETTINGS, api_key_set: false });
    mocks.testSettings.mockResolvedValue({ ok: true, reply: "pong" });
    const user = userEvent.setup();
    renderPanel();

    await screen.findByTestId("input-base-url");
    await user.click(screen.getByTestId("btn-test-settings"));

    const notice = await screen.findByTestId("settings-notice");
    expect(notice).toHaveTextContent("连接成功");
    expect(mocks.testSettings).toHaveBeenCalledWith({
      base_url: CURRENT_SETTINGS.base_url,
      model: CURRENT_SETTINGS.model,
      api_key: undefined,
    });
  });

  it("surfaces a failed connection probe", async () => {
    mocks.fetchSettings.mockResolvedValue({ ...CURRENT_SETTINGS, api_key_set: false });
    mocks.testSettings.mockResolvedValue({ ok: false, error: "HTTP 401" });
    const user = userEvent.setup();
    renderPanel();

    await screen.findByTestId("input-base-url");
    await user.click(screen.getByTestId("btn-test-settings"));

    expect(await screen.findByTestId("settings-notice")).toHaveTextContent("HTTP 401");
  });

  it("blocks saving when base_url or model is empty", async () => {
    mocks.fetchSettings.mockResolvedValue({ ...CURRENT_SETTINGS, api_key_set: false });
    const user = userEvent.setup();
    renderPanel();

    await screen.findByTestId("input-base-url");
    const model = screen.getByTestId("input-model");
    await user.clear(model);
    await user.click(screen.getByTestId("btn-save-settings"));

    expect(await screen.findByTestId("settings-notice")).toHaveTextContent(
      "不能为空",
    );
    expect(mocks.saveSettings).not.toHaveBeenCalled();
  });
});
