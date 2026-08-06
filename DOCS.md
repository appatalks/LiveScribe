# LiveScriber — Technical Documentation

This document covers installation from source, detailed configuration, backend options, project structure, and system requirements. For a quick overview, see the main [README](README.md).

---

## Install from Source

### Windows

```powershell
git clone https://github.com/appatalks/LiveScriber
cd LiveScriber
.\scripts\install.ps1
```

Then run:

```powershell
.\.venv\Scripts\Activate.ps1
livescriber
```

### macOS

```bash
git clone https://github.com/appatalks/LiveScriber && cd LiveScriber
./scripts/install.sh
```

Then run:

```bash
source .venv/bin/activate
livescriber
```

The setup script installs [BlackHole](https://existential.audio/blackhole/) automatically for system-audio capture. Restart macOS after the first installation, then create a Multi-Output Device in Audio MIDI Setup that includes both your headphones or speakers and `BlackHole 2ch`. Set it as the system output so LiveScriber can capture call audio while you continue listening normally.

### Linux

```bash
git clone https://github.com/appatalks/LiveScriber && cd LiveScriber
./scripts/install.sh
```

Then run:

```bash
source .venv/bin/activate
livescriber
```

System audio capture works automatically on Linux via PulseAudio/PipeWire.

---

## CLI Options

```
livescriber                          # default (local Whisper + local summarizer)
livescriber --backend copilot        # use Copilot CLI for summaries
livescriber --backend ollama-like    # use a local LLM server for notes
livescriber --backend openai         # use OpenAI directly (needs API key)
livescriber --theme light            # light theme
livescriber --no-on-top              # disable always-on-top
```

---

## Summarization Backends

### Local (embedded — default, no server required)

Runs directly inside LiveScriber with `llama.cpp` and downloadable GGUF models. No internet connection needed after the model is downloaded.

Included presets:
- `Gemma 2 2B Instruct`
- `Gemma 2 9B Instruct`
- `Llama 3.1 ELM Turbo 4B Instruct`
- `Mistral Nemo 12B Instruct` (default)

Download the selected model from **Settings** when using the `local` backend.
You can also choose the embedded model and local context window there.
On Windows, setup also attempts to install the embedded llama.cpp runtime automatically.

### Copilot CLI (uses your Copilot subscription)

Requires [Copilot CLI](https://github.com/github/copilot-cli) installed and authenticated.

Available models:
- `claude-sonnet-4.5` (default), `claude-sonnet-4`, `claude-haiku-4.5`
- `gpt-5`, `gpt-5.1`, `gpt-5.1-codex`
- `gemini-3-pro-preview`

### Ollama-Like / LM Studio (local, private)

Works with any OpenAI-compatible local server. Configure the URL and model in **⚙ Settings**.

### OpenAI (direct)

```bash
export OPENAI_API_KEY="sk-..."
livescriber --backend openai
```

---

## Configuration

Settings are accessible via the **⚙** button in the title bar, or edit `~/.livescriber/config.json` directly.

```json
{
  "audio": {
    "sample_rate": 16000,
    "channels": 1,
    "capture_system_audio": true
  },
  "transcription": {
    "model_size": "distil-large-v3",
    "language": null,
    "chunk_minutes": 10
  },
  "summarizer": {
    "backend": "local",
    "copilot_model": "claude-sonnet-4.5",
    "local_model_key": "mistral-nemo-12b-instruct",
    "local_context_window": 8192,
    "ollama_url": "http://localhost:11434",
    "ollama_model": "llama3"
  },
  "ui": {
    "window_width": 340,
    "window_height": 720,
    "opacity": 0.95,
    "always_on_top": true,
    "theme": "dark"
  }
}
```

### Whisper Model Sizes

| Model | Size | RAM | Speed | Accuracy |
|-------|------|-----|-------|----------|
| `tiny` | 75 MB | ~1 GB | Fastest | Basic |
| `base` | 142 MB | ~1 GB | Fast | Good |
| `small` | 466 MB | ~2 GB | Medium | Better |
| `distil-large-v3` | 1.5 GB | ~3 GB | Fast | Great (default) |
| `medium` | 1.5 GB | ~5 GB | Slow | Great |
| `large-v3` | 3.1 GB | ~10 GB | Slowest | Best |

---

## System Audio Capture

LiveScriber can capture both your mic and system audio output (e.g., the other side of a call).

**Linux** — works automatically via PulseAudio/PipeWire monitor sources (`parec`).

**macOS** — requires a virtual audio loopback device:

```bash
brew install blackhole-2ch
```

Then open **Audio MIDI Setup** → click **+** → **Create Multi-Output Device** → check both your speakers/headphones and "BlackHole 2ch". Set this as your system output. LiveScriber will automatically detect BlackHole and capture system audio through it.

**Windows** — uses a driver-exposed system-audio input such as `Stereo Mix`, `Wave Out Mix`, or another loopback-style device when available. If your audio driver does not expose one of those inputs, LiveScriber will still record your microphone and continue without system audio.

---

## Windows Installer Build

If you want to build a distributable Windows installer from source:

```powershell
.\.venv\Scripts\Activate.ps1
.\scripts\build_windows_installer.ps1
```

This produces:
- `dist\LiveScriber\LiveScriber.exe` — the app bundle
- `dist\LiveScriber\LiveScriberTranscriber.exe` — transcription helper
- `dist\installer\LiveScriber-Setup-<version>.exe` — the installer (requires [Inno Setup 6](https://jrsoftware.org/isinfo.php))

A GitHub Actions workflow also builds the installer automatically on every push.

---

## Requirements

- **Python 3.10+**
- **PortAudio** (for microphone access)
  - Linux: `sudo apt install portaudio19-dev`
  - macOS: `brew install portaudio`
  - Windows: included with the `sounddevice` wheel
- **llama-cpp-python** (installed during setup for the embedded local summarizer when available)
- **Copilot CLI** (optional — for Copilot summarization backend)
- **Inno Setup 6** (optional — only for building the Windows installer)
- **Ollama-like server or LM Studio** (optional — for fully local/private server operation)
- **OpenAI API key** (optional — for direct OpenAI backend)

---

## Project Structure

```
LiveScriber/
├── livescriber/
│   ├── __init__.py          # Package version
│   ├── main.py              # CLI entry point & arg parsing
│   ├── app.py               # PyQt6 floating window UI + settings dialog
│   ├── recorder.py          # Mic + system audio capture (sounddevice + platform backends)
│   ├── transcriber.py       # Chunked local Whisper transcription
│   ├── summarizer.py        # Copilot CLI / local / ollama-like / OpenAI summarization
│   ├── config.py            # Dataclass config with JSON persistence
│   └── styles.py            # QSS dark/light themes
├── scripts/
│   ├── install.sh           # One-command Linux/macOS setup
│   ├── install.ps1          # One-command Windows setup
│   ├── build_windows_installer.ps1  # Build Windows app bundle + installer
│   └── windows_launcher.py  # PyInstaller launcher entry point
├── tests/                   # Cross-platform test suite
├── installer/
│   └── LiveScriber.iss       # Inno Setup installer definition
├── pyproject.toml
├── requirements.txt
└── README.md
```

---

## License

MIT
