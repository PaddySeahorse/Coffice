"""Persistent LLM endpoint settings driven by the Agent Deck Settings panel.

The WebUI lets the human edit the OpenAI-compatible endpoint (base URL,
model, API key) at runtime. Changes are applied to the in-process
:class:`~coffice.agent.llm_client.LLMClient` immediately and persisted to a
small JSON file so they survive restarts; the ``COFFICE_LLM_BASE_URL`` /
``COFFICE_LLM_MODEL`` / ``COFFICE_LLM_API_KEY`` env vars remain the boot-time
defaults and are never overwritten.

The API key the human enters is stored in plaintext (it already lives in the
env for cloud endpoints) but the file is written with ``0600`` permissions and
the ``GET /settings`` response only ever returns a masked preview of it.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: env var that relocates the settings file (default ``~/.coffice/llm.json``).
ENV_CONFIG_PATH = "COFFICE_LLM_CONFIG"

#: keys persisted / exposed by the settings API.
SETTING_KEYS = ("base_url", "model", "api_key")


def settings_path() -> Path:
    """Where the settings file lives (``$COFFICE_LLM_CONFIG`` or default)."""
    override = os.environ.get(ENV_CONFIG_PATH)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".coffice" / "llm.json"


def load_settings() -> dict[str, Any]:
    """The persisted settings dict, or ``{}`` when absent/corrupt."""
    path = settings_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {key: value for key, value in data.items() if key in SETTING_KEYS}


def save_settings(settings: dict[str, Any]) -> None:
    """Persist ``settings`` to disk, creating the parent directory."""
    path = settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            key: settings[key]
            for key in SETTING_KEYS
            if settings.get(key) is not None
        }
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except OSError as exc:
        logger.warning("could not persist LLM settings to %s: %s", path, exc)


def mask_api_key(api_key: str | None) -> str | None:
    """A preview of the key for the UI: ``sk-ab…wxyz``; ``None`` if unset."""
    if not api_key:
        return None
    if len(api_key) <= 8:
        return "***"
    return f"{api_key[:4]}***{api_key[-4:]}"


def current_values(client: Any) -> dict[str, Any]:
    """The client's raw endpoint fields (plaintext key, for persistence)."""
    return {
        "base_url": str(getattr(client, "base_url", "") or ""),
        "model": str(getattr(client, "model", "") or ""),
        "api_key": getattr(client, "api_key", None),
    }


def effective_settings(client: Any) -> dict[str, Any]:
    """The read-safe settings payload the WebUI renders (key masked)."""
    values = current_values(client)
    api_key = values["api_key"]
    return {
        "base_url": values["base_url"],
        "model": values["model"],
        "api_key": mask_api_key(api_key),
        "api_key_set": bool(api_key),
    }


def apply_to_client(client: Any, settings: dict[str, Any]) -> None:
    """Apply ``settings`` (``None`` = leave untouched) to a chat client.

    Scripted fakes without a ``configure`` method are left alone; the caller
    persists the settings regardless so a real client picks them up at boot.
    """
    configure = getattr(client, "configure", None)
    if callable(configure):
        configure(
            base_url=settings.get("base_url"),
            model=settings.get("model"),
            api_key=settings.get("api_key"),
        )


def apply_persisted(client: Any) -> None:
    """Overlay persisted settings onto a freshly-built client (``build_sessions``)."""
    settings = load_settings()
    if settings:
        apply_to_client(client, settings)


__all__ = [
    "ENV_CONFIG_PATH",
    "SETTING_KEYS",
    "apply_persisted",
    "apply_to_client",
    "current_values",
    "effective_settings",
    "load_settings",
    "mask_api_key",
    "save_settings",
    "settings_path",
]
