"""Tests for core module imports and platform-aware behavior."""

import platform
from subprocess import CompletedProcess
from unittest import mock


class TestImports:
    """Verify all core modules import without errors."""

    def test_import_config(self):
        from livescriber.config import AppConfig, AudioConfig, TranscriptionConfig, SummarizerConfig
        assert AppConfig is not None

    def test_import_recorder(self):
        from livescriber.recorder import Recorder
        assert Recorder is not None

    def test_import_transcriber(self):
        from livescriber.transcriber import Transcriber
        assert Transcriber is not None

    def test_import_summarizer(self):
        from livescriber.summarizer import Summarizer, LOCAL_MODEL_CATALOG
        assert Summarizer is not None
        assert len(LOCAL_MODEL_CATALOG) >= 4

    def test_import_styles(self):
        from livescriber.styles import get_theme
        assert get_theme("dark") != ""
        assert get_theme("light") != ""

    def test_import_main(self):
        from livescriber.main import main
        assert callable(main)


class TestTranscriberPlatform:
    """Verify transcriber subprocess routing based on platform."""

    def test_subprocess_only_on_windows(self):
        from livescriber.config import TranscriptionConfig
        from livescriber.transcriber import Transcriber

        t = Transcriber(TranscriptionConfig())
        result = t._should_use_subprocess()

        if platform.system() == "Windows":
            assert result is True
        else:
            assert result is False

    def test_live_chunk_method_exists(self):
        from livescriber.config import TranscriptionConfig
        from livescriber.transcriber import Transcriber

        t = Transcriber(TranscriptionConfig())
        assert hasattr(t, "transcribe_live_chunk")
        assert callable(t.transcribe_live_chunk)


class TestSummarizerPlatform:
    """Verify summarizer backend normalization and static helpers."""

    def test_normalize_ollama(self):
        from livescriber.summarizer import Summarizer
        assert Summarizer.normalize_backend_name("ollama") == "ollama-like"
        assert Summarizer.normalize_backend_name("copilot") == "copilot"
        assert Summarizer.normalize_backend_name("local") == "local"

    def test_local_model_catalog(self):
        from livescriber.summarizer import Summarizer
        options = Summarizer.get_local_model_options()
        assert "gemma-2-2b-it" in options
        assert "mistral-nemo-12b-instruct" in options

    def test_copilot_stderr_detection(self):
        from livescriber.summarizer import Summarizer
        assert Summarizer._copilot_stderr_has_error("") is False
        assert Summarizer._copilot_stderr_has_error("Error: auth failed") is True
        assert Summarizer._copilot_stderr_has_error("No authentication information found") is True

    def test_copilot_model_options_are_parsed_from_cli_help(self):
        from livescriber.summarizer import Summarizer

        config_help = """
`model`: AI model to use for Copilot CLI.
  - "claude-sonnet-5"
  - "gpt-5.6-terra"

`contextTier`: context window tier.
"""
        with mock.patch.object(
            Summarizer,
            "_build_copilot_command",
            return_value=["copilot", "help", "config"],
        ), mock.patch(
            "livescriber.summarizer.subprocess.run",
            return_value=CompletedProcess([], 0, stdout=config_help, stderr=""),
        ):
            assert Summarizer.get_copilot_model_options() == [
                "auto",
                "claude-sonnet-5",
                "gpt-5.6-terra",
            ]

    def test_copilot_model_options_fall_back_to_auto(self):
        from livescriber.summarizer import Summarizer

        with mock.patch.object(
            Summarizer,
            "_build_copilot_command",
            return_value=["copilot", "help", "config"],
        ), mock.patch(
            "livescriber.summarizer.subprocess.run",
            return_value=CompletedProcess([], 1, stdout="", stderr="failed"),
        ):
            assert Summarizer.get_copilot_model_options() == ["auto"]
