"""Application configuration with sensible defaults."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
APP_DIR = Path.home() / ".livescriber"
RECORDINGS_DIR = APP_DIR / "recordings"
MODELS_DIR = APP_DIR / "models"
CONFIG_PATH = APP_DIR / "config.json"


@dataclass
class AudioConfig:
    sample_rate: int = 16_000       # 16 kHz – Whisper's native rate
    channels: int = 1               # mono
    dtype: str = "float32"
    block_duration_ms: int = 30_000  # chunk size for streaming transcription
    capture_system_audio: bool = True  # also capture system audio output (speakers)


@dataclass
class TranscriptionConfig:
    # Local faster-whisper settings
    model_size: str = "distil-large-v3"  # tiny | base | small | medium | large-v3 | distil-large-v3
    device: str = "auto"             # auto | cpu | cuda
    compute_type: str = "int8"       # int8 | float16 | float32
    language: str | None = None      # None = auto-detect
    beam_size: int = 5
    vad_filter: bool = False         # voice-activity-detection filter (can drop quiet audio)
    chunk_minutes: int = 10          # split long recordings into chunks of this size
    live_transcription: bool = False  # transcribe while recording (streams rough draft)
    auto_translate_english: bool = False  # append English translation to summary (transcript stays in original language)


@dataclass
class SummarizerConfig:
    backend: str = "local"            # copilot | local | ollama-like | openai
    # Copilot CLI settings (uses copilot --prompt)
    copilot_model: str = "auto"
    # Ollama settings
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3"
    # Embedded local llama.cpp settings
    local_model_key: str = "mistral-nemo-12b-instruct"
    local_context_window: int = 8192
    local_max_tokens: int = 768
    local_temperature: float = 0.2
    local_gpu_layers: int = 0
    # OpenAI settings (direct, not via GitHub)
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    # Prompt
    system_prompt: str = (
        "You are a note-taking assistant. Given a transcript of spoken audio, produce clear, "
        "organized notes. The audio may be a meeting, a solo brainstorm, troubleshooting session, "
        "or any spoken thoughts. Adapt your format to fit the content:\n"
        "- Start with a brief summary (2-3 sentences)\n"
        "- Key points or topics discussed as bullet points\n"
        "- Any decisions, conclusions, or solutions reached\n"
        "- Action items or next steps if mentioned\n"
        "Be concise and factual. Do NOT ask questions, add commentary, or include anything "
        "not present in the transcript. Output only the notes."
    )


@dataclass
class UIConfig:
    window_width: int = 340
    window_height: int = 720
    opacity: float = 0.95
    always_on_top: bool = True
    theme: str = "dark"              # dark | light
    ui_language: str = "en"          # UI display language code (en, ko, es, fr, etc.)


@dataclass
class LicenseConfig:
    license_key: str = ""
    registered: bool = False


@dataclass
class AppConfig:
    audio: AudioConfig = field(default_factory=AudioConfig)
    transcription: TranscriptionConfig = field(default_factory=TranscriptionConfig)
    summarizer: SummarizerConfig = field(default_factory=SummarizerConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    license: LicenseConfig = field(default_factory=LicenseConfig)

    # ── Persistence ────────────────────────────────────────────────────────

    def save(self) -> None:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(asdict(self), indent=2))

    @classmethod
    def load(cls) -> "AppConfig":
        cfg = cls()
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text())
                summarizer_data = data.get("summarizer", {})
                if summarizer_data.get("backend") == "ollama":
                    summarizer_data["backend"] = "ollama-like"
                if summarizer_data.get("local_model_key") == "gemma-3-4b-it":
                    summarizer_data["local_model_key"] = "llama-3.1-4b-instruct"
                if summarizer_data.get("local_model_key") == "llama-3.2-3b-instruct":
                    summarizer_data["local_model_key"] = "gemma-2-9b-it"
                if summarizer_data.get("local_model_key") == "mistral-7b-instruct-v0.3":
                    summarizer_data["local_model_key"] = "mistral-nemo-12b-instruct"
                if summarizer_data.get("local_context_window") in (None, 4096):
                    summarizer_data["local_context_window"] = 8192
                if summarizer_data.get("copilot_model") in {
                    "gpt-5.1",
                    "gpt-5.1-codex",
                    "gpt-5.1-codex-mini",
                    "gpt-5.1-codex-max",
                    "gemini-3-pro-preview",
                }:
                    summarizer_data["copilot_model"] = "auto"

                cfg.audio = AudioConfig(**data.get("audio", {}))
                cfg.transcription = TranscriptionConfig(**data.get("transcription", {}))
                cfg.summarizer = SummarizerConfig(**summarizer_data)
                cfg.ui = UIConfig(**data.get("ui", {}))
                if cfg.ui.theme not in {"dark", "light"}:
                    cfg.ui.theme = "dark"
                cfg.license = LicenseConfig(**data.get("license", {}))
            except Exception:
                pass  # fall back to defaults
        # Env-var overrides
        if key := os.environ.get("OPENAI_API_KEY"):
            cfg.summarizer.openai_api_key = key

        return cfg
