PYTHON ?= python3
UV ?= uv
VENV := .venv
BIN := $(VENV)/bin
PY := $(BIN)/python
PIP := $(BIN)/pip
UI := ui

.PHONY: setup install-co test lint run-mcp run-agent run-ui build-sidebar ui-test build-oxt install-oxt smoke

setup: ## Create .venv, install Python deps, and npm install the Agent Deck UI
	@if command -v $(UV) >/dev/null 2>&1; then \
		echo "Using uv for setup"; \
		$(UV) venv $(VENV); \
		$(UV) pip install -e ".[dev]"; \
	elif $(PYTHON) -m pip --version >/dev/null 2>&1; then \
		echo "Using pip for setup"; \
		$(PYTHON) -m venv $(VENV); \
		$(PIP) install -e ".[dev]"; \
	else \
		echo "ERROR: neither 'uv' nor 'python3 -m pip' is available."; \
		echo "Install uv (https://docs.astral.sh/uv/) or ensure python3-pip is installed."; \
		exit 1; \
	fi
	@if [ -f $(UI)/package.json ]; then \
		echo "Installing Agent Deck UI dependencies"; \
		npm --prefix $(UI) install; \
	else \
		echo "SKIPPED: $(UI)/ has no package.json yet"; \
	fi
	@echo "Setup complete. Activate with: source $(VENV)/bin/activate"

install-co:
	@bash scripts/install_co.sh

test:
	$(PY) -m pytest

lint:
	$(BIN)/ruff check src tests

run-mcp:
	$(PY) -m coffice.mcp.server

run-agent:
	$(PY) -m coffice.agent.agent_api

# The Agent Deck UI: served on http://127.0.0.1:8787/ (the origin the sidebar
# shell loads, src/coffice/sidebar/contract.py DEFAULT_UI_PORT). Vite reads
# COFFICE_UI_PORT too, matching the shell's override.
run-ui:
	@if [ -f $(UI)/package.json ]; then npm --prefix $(UI) run dev; \
	else echo "SKIPPED: $(UI)/ has no package.json yet"; fi

build-sidebar:
	@if [ -f $(UI)/package.json ]; then npm --prefix $(UI) run build; else echo "SKIPPED: $(UI)/ is a placeholder, no package.json yet"; fi

ui-test:
	@if [ -f $(UI)/package.json ]; then npm --prefix $(UI) test; \
	else echo "SKIPPED: $(UI)/ has no package.json yet"; fi

build-oxt:
	@bash extension/build.sh

install-oxt:
	@if command -v unopkg >/dev/null 2>&1; then \
		unopkg add --force dist/Coffice.oxt; \
	else \
		echo "unopkg not found; run 'make build-oxt' then install manually:"; \
		echo "  unopkg add dist/Coffice.oxt"; \
	fi

smoke: ## End-to-end Route 1 smoke test (skips gracefully when LO/co are absent)
	@bash scripts/smoke_route1.sh
