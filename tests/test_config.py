"""Tests for config loading, saving, and migration."""

import json
import tempfile
from pathlib import Path
from unittest import mock

from livescriber.config import AppConfig, AudioConfig, TranscriptionConfig, SummarizerConfig, UIConfig


class TestConfigDefaults:
    """Verify default config values for fresh installs."""

    def test_transcription_defaults(self):
        cfg = TranscriptionConfig()
        assert cfg.model_size == "distil-large-v3"
        assert cfg.device == "auto"
        assert cfg.compute_type == "int8"
        assert cfg.language is None
        assert cfg.beam_size == 5
        assert cfg.chunk_minutes == 10
        assert cfg.live_transcription is False
        assert cfg.auto_translate_english is False

    def test_summarizer_defaults(self):
        cfg = SummarizerConfig()
        assert cfg.backend == "local"
        assert cfg.copilot_model == "auto"
        assert cfg.local_model_key == "mistral-nemo-12b-instruct"
        assert cfg.local_context_window == 8192

    def test_audio_defaults(self):
        cfg = AudioConfig()
        assert cfg.sample_rate == 16_000
        assert cfg.channels == 1
        assert cfg.capture_system_audio is True

    def test_ui_defaults(self):
        cfg = UIConfig()
        assert cfg.window_width == 340
        assert cfg.window_height == 720
        assert cfg.opacity == 0.95
        assert cfg.always_on_top is True
        assert cfg.theme == "dark"
        assert cfg.ui_language == "en"


class TestConfigMigration:
    """Verify legacy config values are migrated on load."""

    def test_ollama_backend_normalized(self):
        data = {
            "summarizer": {"backend": "ollama"},
            "audio": {},
            "transcription": {},
            "ui": {},
            "license": {},
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            tmp = Path(f.name)

        with mock.patch("livescriber.config.CONFIG_PATH", tmp):
            cfg = AppConfig.load()
            assert cfg.summarizer.backend == "ollama-like"

        tmp.unlink(missing_ok=True)

    def test_old_model_keys_migrated(self):
        data = {
            "summarizer": {"local_model_key": "gemma-3-4b-it"},
            "audio": {},
            "transcription": {},
            "ui": {},
            "license": {},
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            tmp = Path(f.name)

        with mock.patch("livescriber.config.CONFIG_PATH", tmp):
            cfg = AppConfig.load()
            assert cfg.summarizer.local_model_key == "llama-3.1-4b-instruct"

        tmp.unlink(missing_ok=True)

    def test_removed_copilot_model_migrated_to_auto(self):
        data = {
            "summarizer": {"copilot_model": "gpt-5.1"},
            "audio": {},
            "transcription": {},
            "ui": {},
            "license": {},
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            tmp = Path(f.name)

        with mock.patch("livescriber.config.CONFIG_PATH", tmp):
            cfg = AppConfig.load()
            assert cfg.summarizer.copilot_model == "auto"

        tmp.unlink(missing_ok=True)

    def test_invalid_theme_reset(self):
        data = {
            "ui": {"theme": "neon"},
            "audio": {},
            "transcription": {},
            "summarizer": {},
            "license": {},
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            tmp = Path(f.name)

        with mock.patch("livescriber.config.CONFIG_PATH", tmp):
            cfg = AppConfig.load()
            assert cfg.ui.theme == "dark"

        tmp.unlink(missing_ok=True)


class TestConfigRoundTrip:
    """Verify config can be saved and loaded back identically."""

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = Path(tmpdir) / "config.json"
            app_dir = Path(tmpdir)

            with mock.patch("livescriber.config.CONFIG_PATH", cfg_path), \
                 mock.patch("livescriber.config.APP_DIR", app_dir):
                cfg = AppConfig()
                cfg.summarizer.backend = "openai"
                cfg.transcription.model_size = "small"
                cfg.save()

                loaded = AppConfig.load()
                assert loaded.summarizer.backend == "openai"
                assert loaded.transcription.model_size == "small"
