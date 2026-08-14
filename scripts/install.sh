#!/usr/bin/env bash
# Coffice one-shot installer: fetch the latest GitHub release and deploy the
# Python package (wheel), the prebuilt Agent Deck UI, and optionally the
# LibreOffice extension (.oxt). No compilation or build toolchain is needed;
# every artifact comes from the release assets.
#
# Usage:
#   bash scripts/install.sh [--prefix DIR] [--version vX.Y.Z]
#                           [--install-oxt] [--configure-shell]
#                           [--with-co|--without-co] [--help]
#
# Options:
#   --prefix DIR          install under DIR (default: $HOME/.coffice)
#   --version vX.Y.Z      install a specific release tag (default: latest)
#   --install-oxt         require the LibreOffice extension: fail loudly if
#                         LibreOffice is not installed (otherwise the script
#                         installs it when LO is present and prints a hint
#                         when it is not)
#   --with-co             also install the co CLI from its GitHub release
#   --without-co          skip the co CLI even if it is missing
#   --configure-shell     append the coffice bin dir to your shell profile
#   --help                show this help
#
# Environment:
#   COFFICE_REPO          owner/repo (default: PaddySeahorse/Coffice)
#   COFFICE_PREFIX        install prefix (same as --prefix)
#   COFFICE_VERSION       release tag to install (same as --version)
#   CO_INSTALL            auto|yes|no (same as --with-co/--without-co)
#
# Requirements: curl, python3 >= 3.11, tar. The Python package installs from
# a prebuilt wheel, so no C/C++ compiler is required.

set -euo pipefail

REPO="${COFFICE_REPO:-PaddySeahorse/Coffice}"
CO_REPO="${CO_REPO:-PaddySeahorse/co}"
PREFIX="${COFFICE_PREFIX:-$HOME/.coffice}"
VERSION="${COFFICE_VERSION:-}"
INSTALL_OXT=0
CONFIGURE_SHELL=0
CO_INSTALL="${CO_INSTALL:-auto}"

die() {
    echo "ERROR: $*" >&2
    exit 1
}

usage() {
    echo "Coffice one-shot installer (no compilation required)"
    echo
    echo "Usage: bash scripts/install.sh [options]"
    echo
    echo "Options:"
    echo "  --prefix DIR          install under DIR (default: ~/.coffice)"
    echo "  --version vX.Y.Z      install a specific release tag (default: latest)"
    echo "  --install-oxt         require the LO extension: fail loudly if LibreOffice"
    echo "                        is missing (default: install when LO present, else hint)"
    echo "  --with-co             also install the co CLI from its GitHub release"
    echo "  --without-co          skip the co CLI even if it is missing"
    echo "  --configure-shell     append the coffice bin dir to your shell profile"
    echo "  --help                show this help"
    echo
    echo "Environment:"
    echo "  COFFICE_REPO          owner/repo (default: PaddySeahorse/Coffice)"
    echo "  COFFICE_PREFIX        install prefix (same as --prefix)"
    echo "  COFFICE_VERSION       release tag to install (same as --version)"
    echo "  CO_INSTALL            auto|yes|no (same as --with-co/--without-co)"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --prefix)
            [ $# -ge 2 ] || die "--prefix requires an argument"
            PREFIX="$2"
            shift 2
            ;;
        --version)
            [ $# -ge 2 ] || die "--version requires an argument"
            VERSION="$2"
            shift 2
            ;;
        --install-oxt)
            INSTALL_OXT=1
            shift
            ;;
        --with-co)
            CO_INSTALL=yes
            shift
            ;;
        --without-co)
            CO_INSTALL=no
            shift
            ;;
        --configure-shell)
            CONFIGURE_SHELL=1
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            die "unknown argument: $1 (run with --help)"
            ;;
    esac
done

need() {
    command -v "$1" >/dev/null 2>&1 || die "missing required tool '$1'"
}

need curl
need tar
need python3
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' ||
    die "python3 >= 3.11 is required (found: $(python3 --version 2>&1))"

# ---- LibreOffice detection --------------------------------------------------
LO_UNOPKG=""
detect_lo() {
    # Locate the unopkg tool shipped with LibreOffice. Returns 0 and sets
    # LO_UNOPKG when found, 1 otherwise.
    if command -v unopkg >/dev/null 2>&1; then
        LO_UNOPKG="$(command -v unopkg)"
        return 0
    fi
    for cand in \
        "/Applications/LibreOffice.app/Contents/MacOS/unopkg" \
        "/usr/lib/libreoffice/program/unopkg" \
        "/usr/lib64/libreoffice/program/unopkg"; do
        [ -x "$cand" ] || continue
        LO_UNOPKG="$cand"
        return 0
    done
    return 1
}

# ---- co CLI detection (mirrors src/coffice/versioning/co_client.py) -------
co_available() {
    [ -n "${CO_BIN:-}" ] && [ -x "$CO_BIN" ] && return 0
    [ -n "${COFFICE_CO_BIN:-}" ] && [ -x "$COFFICE_CO_BIN" ] && return 0
    command -v co >/dev/null 2>&1 && return 0
    [ -x "$HOME/.local/bin/co" ] && return 0
    [ -x "/usr/local/bin/co" ] && return 0
    return 1
}

resolve_co_tag() {
    git ls-remote --tags "https://github.com/${CO_REPO}.git" 2>/dev/null |
        sed -n 's#.*refs/tags/\(v[0-9][0-9.]*\)$#\1#p' |
        sort -V |
        tail -n 1
}

detect_co_asset() {
    # co releases ship amd64 binaries only: co_{tag}_{os}_{arch}.{tar.gz,zip}
    CO_OS=""
    CO_ARCH=""
    case "$(uname -s)" in
        Linux) CO_OS="linux" ;;
        Darwin) CO_OS="darwin" ;;
    esac
    case "$(uname -m)" in
        x86_64|amd64) CO_ARCH="amd64" ;;
        aarch64|arm64) CO_ARCH="arm64" ;;
    esac
    if [ "$CO_OS" = "linux" ] && [ "$CO_ARCH" = "amd64" ]; then
        return 0
    fi
    if [ "$CO_OS" = "darwin" ] && [ "$CO_ARCH" = "amd64" ]; then
        return 0
    fi
    return 1
}

BIN_DIR="$PREFIX/bin"
PY="$PREFIX/venv/bin/python"

echo "==> Coffice installer"
echo "    repo:   $REPO"
echo "    prefix: $PREFIX"
echo "    tag:    ${VERSION:-latest}"

# Resolve the release tag and asset URLs. Release assets follow a fixed naming
# pattern (see .github/workflows/release.yml), so the download URLs are built
# by template and only the tag has to be discovered:
#   wheel: coffice-{version}-py3-none-any.whl   (version = tag without leading v)
#   ui:    coffice-agent-deck_{tag}.tar.gz
#   oxt:   Coffice.oxt
resolve_latest_tag() {
    # Every Coffice release is published as prerelease, so the releases/latest
    # endpoint never resolves; list releases and take the newest entry.
    local json
    if json="$(curl -fsSL --max-time 20 "https://api.github.com/repos/${REPO}/releases?per_page=1" 2>/dev/null)" &&
        [ -n "$json" ]; then
        python3 -c 'import json,sys
try:
    d=json.load(sys.stdin)
    print(d[0]["tag_name"] if isinstance(d,list) and d else d.get("tag_name",""))
except Exception:
    pass' <<<"$json"
    elif command -v git >/dev/null 2>&1; then
        # API unauthenticated rate limit exhausted (60/hr per IP): fall back
        # to the git protocol, which has no such limit.
        git ls-remote --tags "https://github.com/${REPO}.git" 2>/dev/null |
            sed -n 's#.*refs/tags/\(v[0-9][0-9.]*\)$#\1#p' |
            sort -V |
            tail -n 1
    fi
}

if [ -n "$VERSION" ]; then
    TAG="$VERSION"
else
    echo "==> resolving latest release"
    TAG="$(resolve_latest_tag)" ||
        die "failed to resolve the latest release for ${REPO}"
    [ -n "$TAG" ] ||
        die "failed to resolve the latest release for ${REPO} (network access required)"
fi

VERSION_NO_V="${TAG#v}"
DOWNLOAD_BASE="https://github.com/${REPO}/releases/download/${TAG}"
WHEEL_URL="$DOWNLOAD_BASE/coffice-${VERSION_NO_V}-py3-none-any.whl"
UI_URL="$DOWNLOAD_BASE/coffice-agent-deck_${TAG}.tar.gz"
OXT_URL="$DOWNLOAD_BASE/Coffice.oxt"

echo "==> installing coffice ${TAG}"

mkdir -p "$BIN_DIR" "$PREFIX/ui"
if [ ! -x "$PY" ]; then
    echo "==> creating virtualenv"
    if command -v uv >/dev/null 2>&1; then
        uv venv "$PREFIX/venv"
    else
        python3 -m venv "$PREFIX/venv" || die \
            "python3 -m venv failed. On Debian/Ubuntu install python3-venv " \
            "(sudo apt-get install python3-venv), or install uv " \
            "(https://docs.astral.sh/uv/) and rerun this script."
    fi
fi

echo "==> installing Python package (prebuilt wheel, no compilation)"
if command -v uv >/dev/null 2>&1 && [ -x "$PY" ]; then
    uv pip install --python "$PY" --upgrade pip "$WHEEL_URL"
else
    "$PY" -m pip install --quiet --upgrade pip
    "$PY" -m pip install --quiet "$WHEEL_URL"
fi

echo "==> deploying Agent Deck UI"
curl -fsSL "$UI_URL" | tar -xz -C "$PREFIX/ui"
[ -f "$PREFIX/ui/dist/index.html" ] ||
    die "UI bundle did not contain dist/ (broke download or release layout)"

OXT_PATH="$PREFIX/Coffice.oxt"
if [ -n "$OXT_URL" ]; then
    echo "==> downloading LibreOffice extension"
    curl -fsSL -o "$OXT_PATH" "$OXT_URL"
    if detect_lo; then
        echo "==> LibreOffice found ($LO_UNOPKG)"
        echo "==> installing Coffice extension (unopkg)"
        "$LO_UNOPKG" add --force "$OXT_PATH"
    else
        echo "==> LibreOffice not detected"
        echo "    The Coffice sidebar lives inside LibreOffice, so the extension"
        echo "    cannot be installed until LibreOffice is present."
        if [ "$INSTALL_OXT" = 1 ]; then
            die "LibreOffice is required for --install-oxt. Download it from https://www.libreoffice.org/download/"
        fi
        echo "    Install LibreOffice first, then run:"
        echo "        coffice install-oxt   (installs $OXT_PATH)"
    fi
fi

# ---- co CLI: detect, then optionally install from its release ---------------
CO_BIN_TARGET="$BIN_DIR/co"
INSTALL_CO=0
if [ "$CO_INSTALL" = "no" ]; then
    echo "==> skipping co CLI (requested)"
elif [ "$CO_INSTALL" = "yes" ]; then
    INSTALL_CO=1
elif co_available; then
    echo "==> co CLI already available"
else
    echo "==> co CLI not found (version-control snapshots)"
    echo "    Coffice works without it, but document history (co log/diff,"
    echo "    snapshot commits) is disabled until the co binary is available."
    if [ -t 0 ]; then
        printf '    Install co from its GitHub release? [y/N] '
        read -r REPLY
        case "$REPLY" in
            y|Y|yes|YES) INSTALL_CO=1 ;;
        esac
    else
        echo "    (non-interactive shell: use --with-co to install, or"
        echo "     set CO_INSTALL=yes|no)"
    fi
fi

if [ "$INSTALL_CO" = 1 ]; then
    echo "==> installing co CLI from its release"
    if ! detect_co_asset; then
        echo "    WARNING: co ships amd64 binaries only; your platform is" >&2
        echo "    $(uname -s)/$(uname -m). Building from source instead:" >&2
        echo "    bash scripts/install_co.sh" >&2
        echo "    (or point CO_BIN at an existing co binary)." >&2
    else
        CO_TAG="$(resolve_co_tag)" ||
            die "failed to resolve the latest co release for ${CO_REPO}"
        [ -n "$CO_TAG" ] ||
            die "failed to resolve the latest co release for ${CO_REPO} (network access required)"
        CO_URL="https://github.com/${CO_REPO}/releases/download/${CO_TAG}/co_${CO_TAG}_${CO_OS}_${CO_ARCH}.tar.gz"
        echo "==> downloading co ${CO_TAG}"
        mkdir -p "$BIN_DIR"
        curl -fsSL "$CO_URL" -o "$BIN_DIR/co.tgz" ||
            die "failed to download co from ${CO_URL}"
        tar -xzf "$BIN_DIR/co.tgz" -C "$BIN_DIR" ||
            die "failed to unpack the co binary"
        chmod +x "$CO_BIN_TARGET"
        [ -x "$CO_BIN_TARGET" ] || die "co bundle did not contain a 'co' executable"
        echo "    installed: $CO_BIN_TARGET"
    fi
fi

cat > "$BIN_DIR/coffice" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

PREFIX="__PREFIX__"
PY="$PREFIX/venv/bin/python"
UI_DIR="$PREFIX/ui/dist"

usage() {
    echo "coffice — Coffice launcher"
    echo
    echo "usage: coffice {run-agent|run-ui|run-mcp|install-oxt}"
    echo
    echo "  run-agent   start the agent HTTP facade (COFFICE_AGENT_PORT, default 8790)"
    echo "  run-ui      serve the Agent Deck UI (COFFICE_UI_PORT, default 8787)"
    echo "  run-mcp     start the MCP server over stdio"
    echo "  install-oxt install the LibreOffice extension via unopkg"
    exit 1
}

case "${1:-}" in
    run-agent)
        exec "$PY" -m coffice.agent.agent_api
        ;;
    run-ui)
        exec "$PY" -m http.server "${COFFICE_UI_PORT:-8787}" --directory "$UI_DIR"
        ;;
    run-mcp)
        exec "$PY" -m coffice.mcp.server
        ;;
    install-oxt)
        [ -f "$PREFIX/Coffice.oxt" ] || {
            echo "no .oxt bundled; rerun install.sh (with --install-oxt) to fetch it" >&2
            exit 1
        }
        if command -v unopkg >/dev/null 2>&1; then
            exec unopkg add --force "$PREFIX/Coffice.oxt"
        elif [ -x "/Applications/LibreOffice.app/Contents/MacOS/unopkg" ]; then
            exec /Applications/LibreOffice.app/Contents/MacOS/unopkg add --force "$PREFIX/Coffice.oxt"
        else
            echo "LibreOffice not detected. Install it from" >&2
            echo "https://www.libreoffice.org/download/ then rerun this command." >&2
            exit 1
        fi
        ;;
    *)
        usage
        ;;
esac
EOF

sed -i "s|__PREFIX__|$PREFIX|" "$BIN_DIR/coffice"
chmod +x "$BIN_DIR/coffice"

if [ "$CONFIGURE_SHELL" = 1 ]; then
    for RC_FILE in "$HOME/.bashrc" "$HOME/.zshrc"; do
        [ -f "$RC_FILE" ] || continue
        grep -q "^export PATH=.*$BIN_DIR" "$RC_FILE" ||
            printf '\nexport PATH="%s:$PATH"\n' "$BIN_DIR" >>"$RC_FILE"
        if [ -x "$CO_BIN_TARGET" ]; then
            grep -q '^export CO_BIN=' "$RC_FILE" ||
                printf 'export CO_BIN="%s"\n' "$CO_BIN_TARGET" >>"$RC_FILE"
        fi
    done
    echo "==> appended $BIN_DIR to your PATH in shell profile"
fi

echo
echo "==> coffice ${TAG} installed"
echo "    prefix: $PREFIX"
echo "    launcher: $BIN_DIR/coffice"
if [ -x "$CO_BIN_TARGET" ]; then
    echo "    co CLI:  $CO_BIN_TARGET (set CO_BIN to it if not on PATH)"
fi
if [ -f "$OXT_PATH" ]; then
    echo "    extension: $OXT_PATH (LibreOffice extension)"
fi
echo
echo "Run the agent and UI, then open http://127.0.0.1:8787/ in a browser:"
echo "    export PATH=\"$BIN_DIR:\$PATH\""
echo "    coffice run-agent"
echo "    coffice run-ui"
echo
echo "Configure the LLM via COFFICE_LLM_BASE_URL / COFFICE_LLM_MODEL /"
echo "COFFICE_LLM_API_KEY, or from the Agent Deck Settings panel"
echo "(~/.coffice/llm.json persists changes between launches)."
