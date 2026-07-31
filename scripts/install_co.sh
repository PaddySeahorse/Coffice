#!/usr/bin/env bash
# Install the `co` CLI (https://github.com/PaddySeahorse/co) for Coffice.
#
# `co` is a C++17/CMake project. This script clones it, builds it with cmake,
# and installs the resulting binary to a discoverable location (default
# ~/.local/bin/co). It then prints the `CO_BIN` export line the Python client
# (`src/coffice/versioning/co_client.py`) reads at startup.
#
# Requirements: git, cmake >= 3.16, a C++17 compiler (g++/clang++), zlib, and
# OpenSSL development headers. If a dependency is missing the script fails
# loudly with the exact command to install it.
#
# Usage:
#   bash scripts/install_co.sh [--prefix DIR] [--configure-shell]
#
# Options:
#   --prefix DIR         Install to DIR/bin/co (default: $HOME/.local)
#   --configure-shell    Append "export CO_BIN=..." to ~/.bashrc
#   --keep-src           Keep the cloned/build tree (default: remove it)
#   --src-dir DIR        Clone/build in DIR instead of a temp dir
#
# Environment:
#   CO_PREFIX            Install prefix (same as --prefix)
#   CO_SRC_DIR           Persist the source tree here instead of a temp dir
#
# The binary ends up at $PREFIX/bin/co; set CO_BIN to that path (the script
# prints the exact line).

set -euo pipefail

REPO_URL="${CO_REPO_URL:-https://github.com/PaddySeahorse/co.git}"
PREFIX="${CO_PREFIX:-$HOME/.local}"
CONFIGURE_SHELL=0
KEEP_SRC=0
SRC_DIR="${CO_SRC_DIR:-}"

die() {
    echo "ERROR: $*" >&2
    exit 1
}

# ---- option parsing -------------------------------------------------------
while [ $# -gt 0 ]; do
    case "$1" in
        --prefix)
            [ $# -ge 2 ] || die "--prefix requires an argument"
            PREFIX="$2"
            shift 2
            ;;
        --configure-shell)
            CONFIGURE_SHELL=1
            shift
            ;;
        --keep-src)
            KEEP_SRC=1
            shift
            ;;
        --src-dir)
            [ $# -ge 2 ] || die "--src-dir requires an argument"
            SRC_DIR="$2"
            shift 2
            ;;
        --help|-h)
            sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            die "unknown argument: $1 (run with --help)"
            ;;
    esac
done

BIN_DIR="$PREFIX/bin"
BIN_PATH="$BIN_DIR/co"
MIN_CMAKE="3.16"

# ---- dependency checks (fail loudly, print the fix) ----------------------
need() { # need <bin> <apt-pkg>
    command -v "$1" >/dev/null 2>&1 || die \
        "missing required tool '$1' (apt: sudo apt-get install $2)"
}
need git git
need cmake cmake
if command -v c++ >/dev/null 2>&1; then
    CXX=c++
elif command -v g++ >/dev/null 2>&1; then
    CXX=g++
else
    die "missing a C++17 compiler (apt: sudo apt-get install g++)"
fi

cmake --version | grep -q "version \(3\.\([1-9][0-9]*\|0\)\|[4-9]\)" || die \
    "cmake >= $MIN_CMAKE is required (found: $(cmake --version | head -1)); apt: sudo apt-get install cmake"

# zlib + OpenSSL headers
if command -v pkg-config >/dev/null 2>&1; then
    pkg-config --exists zlib || die "missing zlib headers (apt: sudo apt-get install zlib1g-dev)"
    pkg-config --exists openssl || die "missing OpenSSL headers (apt: sudo apt-get install libssl-dev)"
else
    [ -f /usr/include/zlib.h ] || die "missing zlib headers (apt: sudo apt-get install zlib1g-dev)"
    [ -f /usr/include/openssl/evp.h ] || die "missing OpenSSL headers (apt: sudo apt-get install libssl-dev)"
fi

echo "==> co CLI installer"
echo "    prefix: $PREFIX"
echo "    binary: $BIN_PATH"

# ---- clone ---------------------------------------------------------------
if [ -z "$SRC_DIR" ]; then
    SRC_DIR="$(mktemp -d)"
    CLEANUP_SRC=1
else
    CLEANUP_SRC=0
fi
[ "$KEEP_SRC" = 1 ] && CLEANUP_SRC=0

echo "==> cloning $REPO_URL"
rm -rf "$SRC_DIR"
if ! git clone --quiet "$REPO_URL" "$SRC_DIR"; then
    rm -rf "$SRC_DIR"
    die "git clone failed for $REPO_URL (check network access)"
fi

# ---- build ---------------------------------------------------------------
echo "==> building with cmake (this can take a few minutes)"
VERSION="$(git -C "$SRC_DIR" describe --tags --always --dirty 2>/dev/null || echo dev)"
if ! (
    cmake -S "$SRC_DIR" -B "$SRC_DIR/build" -DVERSION="$VERSION" -DCMAKE_BUILD_TYPE=Release
    cmake --build "$SRC_DIR/build" -j"$(nproc 2>/dev/null || echo 2)"
) >"$SRC_DIR/build.log" 2>&1; then
    echo "ERROR: build failed. Last 30 lines of the build log:" >&2
    tail -n 30 "$SRC_DIR/build.log" >&2
    [ "$CLEANUP_SRC" = 1 ] && rm -rf "$SRC_DIR"
    die "co build failed; see the log above. If the toolchain is unavailable in this environment, install co manually per its README and set CO_BIN."
fi

[ -x "$SRC_DIR/build/co" ] || die "build completed but $SRC_DIR/build/co is missing"

# ---- install -------------------------------------------------------------
echo "==> installing to $BIN_PATH"
mkdir -p "$BIN_DIR"
cp "$SRC_DIR/build/co" "$BIN_PATH"
chmod +x "$BIN_PATH"
if [ "$CLEANUP_SRC" = 1 ]; then
    rm -rf "$SRC_DIR"
fi

# ---- report / configure shell --------------------------------------------
echo
echo "==> co installed successfully"
echo "    version: $("$BIN_PATH" --version 2>/dev/null || echo unknown)"
echo
echo "    export CO_BIN=\"$BIN_PATH\""
echo "    export PATH=\"$BIN_DIR:\$PATH\"   # optional"

if [ "$CONFIGURE_SHELL" = 1 ]; then
    RC_FILE="${HOME}/.bashrc"
    touch "$RC_FILE"
    grep -q '^export CO_BIN=' "$RC_FILE" || printf '\nexport CO_BIN="%s"\n' "$BIN_PATH" >>"$RC_FILE"
    echo
    echo "    appended 'export CO_BIN=\"$BIN_PATH\"' to $RC_FILE"
fi
