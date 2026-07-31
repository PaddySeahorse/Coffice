PYTHON ?= python3
UV ?= uv
VENV := .venv
BIN := $(VENV)/bin
PY := $(BIN)/python
PIP := $(BIN)/pip
UI := ui

.PHONY: setup install-co test lint run-mcp build-sidebar smoke

setup:
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
	@echo "Setup complete. Activate with: source $(VENV)/bin/activate"

install-co:
	@bash scripts/install_co.sh

test:
	$(PY) -m pytest

lint:
	$(BIN)/ruff check src tests

run-mcp:
	$(PY) -m coffice.mcp.server

build-sidebar:
	@if [ -f $(UI)/package.json ]; then npm --prefix $(UI) run build; else echo "SKIPPED: $(UI)/ is a placeholder, no package.json yet"; fi

smoke:
	@if [ -f scripts/smoke_route1.sh ]; then bash scripts/smoke_route1.sh; else echo "SKIPPED: scripts/smoke_route1.sh lands in the final integration bead"; fi
