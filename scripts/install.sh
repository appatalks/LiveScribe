#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# LiveScriber — Setup Script (Linux / macOS)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $*"; exit 1; }

install_local_runtime() {
    info "Installing embedded local summarization runtime..."

    # The extra-index CPU wheels may ship musl-linked binaries that fail on
    # glibc systems (Debian, Ubuntu, Fedora, etc.).  Try the extra index
    # first, then verify the library actually loads.  If it doesn't, fall
    # back to a source build which compiles against the host libc.
    if pip install --prefer-binary --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu llama-cpp-python -q; then
        if python -c "import llama_cpp" 2>/dev/null; then
            ok "Embedded local summarization runtime installed"
            return
        fi
        warn "Prebuilt wheel installed but failed to load (likely musl/glibc mismatch). Rebuilding from source..."
        pip uninstall llama-cpp-python -y -q
    fi

    warn "Prebuilt llama.cpp wheel was not available. Attempting a local build setup..."

    if [[ "$(uname)" == "Linux" ]]; then
        if command -v apt-get &>/dev/null; then
            sudo apt-get update -qq
            sudo apt-get install -y -qq build-essential cmake
        elif command -v dnf &>/dev/null; then
            sudo dnf install -y gcc-c++ make cmake
        elif command -v pacman &>/dev/null; then
            sudo pacman -S --noconfirm base-devel cmake
        else
            warn "Could not detect a supported Linux package manager for compiler tool installation"
        fi
    elif [[ "$(uname)" == "Darwin" ]]; then
        if ! xcode-select -p &>/dev/null; then
            warn "Xcode Command Line Tools are required to build llama-cpp-python"
            xcode-select --install || true
        fi
        if command -v brew &>/dev/null; then
            brew install cmake || true
        fi
    fi

    if pip install llama-cpp-python -q; then
        ok "Embedded local summarization runtime installed"
        return
    fi

    warn "Could not install llama-cpp-python automatically. LiveScriber will still work, but the embedded local summarizer will remain unavailable until its runtime is installed."
}

# ── Check Python ───────────────────────────────────────────────────────────
PYTHON=""
for cmd in python3.12 python3.11 python3.10 python3; do
    if command -v "$cmd" &>/dev/null; then
        ver=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        major=$(echo "$ver" | cut -d. -f1)
        minor=$(echo "$ver" | cut -d. -f2)
        if [[ "$major" -ge 3 && "$minor" -ge 10 ]]; then
            PYTHON="$cmd"
            break
        fi
    fi
done

[[ -z "$PYTHON" ]] && fail "Python 3.10+ is required. Install it first."
info "Using $PYTHON ($($PYTHON --version))"

# ── System dependencies ───────────────────────────────────────────────────
if [[ "$(uname)" == "Linux" ]]; then
    info "Checking Linux audio dependencies…"
    if command -v apt-get &>/dev/null; then
        pkgs=()
        dpkg -s portaudio19-dev &>/dev/null || pkgs+=(portaudio19-dev)
        dpkg -s python3-pyaudio &>/dev/null || pkgs+=(python3-pyaudio)
        if [[ ${#pkgs[@]} -gt 0 ]]; then
            info "Installing: ${pkgs[*]}"
            sudo apt-get update -qq && sudo apt-get install -y -qq "${pkgs[@]}"
        fi
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y portaudio-devel
    elif command -v pacman &>/dev/null; then
        sudo pacman -S --noconfirm portaudio
    fi
elif [[ "$(uname)" == "Darwin" ]]; then
    info "Checking macOS audio dependencies…"
    if ! brew list portaudio &>/dev/null 2>&1; then
        info "Installing portaudio via Homebrew…"
        brew install portaudio
    fi
    if ! brew list --cask blackhole-2ch &>/dev/null; then
        info "Installing BlackHole 2ch for system audio capture…"
        brew install --cask blackhole-2ch
        warn "Restart macOS before using system audio capture so CoreAudio can load BlackHole."
    else
        ok "BlackHole detected — system audio capture available"
    fi
fi

# ── Create virtual environment ────────────────────────────────────────────
VENV_DIR="$PROJECT_DIR/.venv"
if [[ ! -d "$VENV_DIR" ]]; then
    info "Creating virtual environment…"
    "$PYTHON" -m venv "$VENV_DIR"
fi
ok "Virtual environment: $VENV_DIR"

# ── Install dependencies ──────────────────────────────────────────────────
source "$VENV_DIR/bin/activate"

info "Upgrading pip…"
pip install --upgrade pip -q

info "Installing LiveScriber…"
pip install -e "$PROJECT_DIR" -q

install_local_runtime

ok "Installation complete!"

# ── Create ~/.livescriber ──────────────────────────────────────────────────
mkdir -p "$HOME/.livescriber/recordings"

# ── Print summary ─────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║         LiveScriber installed successfully!       ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════╝${NC}"
echo ""
echo "  Activate the environment:"
echo -e "    ${CYAN}source $VENV_DIR/bin/activate${NC}"
echo ""
echo "  Run LiveScriber:"
echo -e "    ${CYAN}livescriber${NC}"
echo ""
echo "  Options:"
echo -e "    ${CYAN}livescriber --model small${NC}       # Use a larger Whisper model"
echo -e "    ${CYAN}livescriber --backend openai${NC}    # Use OpenAI for summaries"
echo -e "    ${CYAN}livescriber --theme light${NC}       # Light theme"
echo ""
echo "  For local summarization, install Ollama:"
echo -e "    ${CYAN}curl -fsSL https://ollama.com/install.sh | sh${NC}"
echo -e "    ${CYAN}ollama pull llama3${NC}"
echo ""
