"""Tests for audio processing and WAV export (no real mic required)."""

import numpy as np
import sounddevice as sd

from livescriber.config import AudioConfig
from livescriber.recorder import Recorder


class TestSyntheticAudio:
    """Verify audio processing with synthetic numpy arrays."""

    def test_wav_export_silence(self):
        """WAV bytes from 1 second of silence should produce a valid header + data."""
        r = Recorder(AudioConfig())
        r._processed_audio = np.zeros(16000, dtype=np.float32)
        wav = r.get_wav_bytes()
        assert len(wav) > 44  # WAV header is 44 bytes
        assert wav[:4] == b"RIFF"

    def test_wav_export_tone(self):
        """WAV bytes from a synthetic sine wave."""
        r = Recorder(AudioConfig())
        t = np.linspace(0, 1, 16000, dtype=np.float32)
        r._processed_audio = 0.5 * np.sin(2 * np.pi * 440 * t)
        wav = r.get_wav_bytes()
        assert len(wav) > 16000

    def test_empty_audio_returns_empty_bytes(self):
        r = Recorder(AudioConfig())
        assert r.get_wav_bytes() == b""
        assert r.get_audio().size == 0

    def test_duration_after_processing(self):
        r = Recorder(AudioConfig())
        r._processed_audio = np.zeros(32000, dtype=np.float32)  # 2 seconds
        assert abs(r.duration_seconds - 2.0) < 0.01

    def test_resample(self):
        """Resample from 44100 to 16000 should change array length."""
        r = Recorder(AudioConfig())
        audio = np.zeros(44100, dtype=np.float32)  # 1 second at 44.1kHz
        resampled = r._resample_to_target(audio, 44100)
        assert abs(len(resampled) - 16000) < 10  # should be ~16000 samples

    def test_low_level_noise_is_not_amplified(self):
        r = Recorder(AudioConfig())
        r._mic_rate = 16000
        r._mic_frames = [np.full((16000, 1), 0.0001, dtype=np.float32)]
        assert r._process_audio().size == 0

    def test_low_volume_speech_gain_is_capped(self):
        r = Recorder(AudioConfig())
        r._mic_rate = 16000
        r._mic_frames = [np.full((16000, 1), 0.01, dtype=np.float32)]
        processed = r._process_audio()
        assert np.max(np.abs(processed)) <= 0.08


class TestMacOSSystemAudioDetection:
    """Verify macOS loopback-device selection without CoreAudio hardware."""

    def test_meeting_app_virtual_device_is_not_used_automatically(self, monkeypatch):
        devices = [
            {"name": "MacBook Pro Microphone", "max_input_channels": 1},
            {"name": "Microsoft Teams Audio", "max_input_channels": 1},
        ]
        monkeypatch.setattr("livescriber.recorder.sd.query_devices", lambda: devices)
        assert Recorder._find_macos_system_audio_device() is None

    def test_blackhole_is_detected(self, monkeypatch):
        devices = [
            {"name": "Microsoft Teams Audio", "max_input_channels": 1},
            {"name": "BlackHole 2ch", "max_input_channels": 2},
        ]
        monkeypatch.setattr("livescriber.recorder.sd.query_devices", lambda: devices)
        assert Recorder._find_macos_system_audio_device() == 1

    def test_builtin_microphone_avoids_bluetooth_call_mode(self, monkeypatch):
        devices = [
            {"name": "WH-CH520", "max_input_channels": 1},
            {"name": "WH-CH520", "max_input_channels": 0},
            {"name": "MacBook Pro Microphone", "max_input_channels": 1},
        ]
        monkeypatch.setattr("livescriber.recorder.platform.system", lambda: "Darwin")
        monkeypatch.setattr("livescriber.recorder.sd.default.device", [0, 1])
        monkeypatch.setattr("livescriber.recorder.sd.query_devices", lambda: devices)
        assert Recorder._select_mic_input_device() == 2

    def test_default_microphone_is_used_when_not_shared_with_output(self, monkeypatch):
        devices = [
            {"name": "USB Microphone", "max_input_channels": 1},
            {"name": "WH-CH520", "max_input_channels": 0},
        ]
        monkeypatch.setattr("livescriber.recorder.platform.system", lambda: "Darwin")
        monkeypatch.setattr("livescriber.recorder.sd.default.device", [0, 1])
        monkeypatch.setattr("livescriber.recorder.sd.query_devices", lambda: devices)
        assert Recorder._select_mic_input_device() == 0


class TestLiveChunkFiltering:
    """Verify the transcriber's live chunk pre-filtering without loading Whisper."""

    def test_short_chunk_rejected(self):
        from livescriber.config import TranscriptionConfig
        from livescriber.transcriber import Transcriber

        t = Transcriber(TranscriptionConfig())
        result = t.transcribe_live_chunk(np.zeros(8000, dtype=np.float32), 16000)
        assert result == ""

    def test_silence_rejected(self):
        from livescriber.config import TranscriptionConfig
        from livescriber.transcriber import Transcriber

        t = Transcriber(TranscriptionConfig())
        result = t.transcribe_live_chunk(np.zeros(48000, dtype=np.float32), 16000)
        assert result == ""

    def test_device_listing(self):
        """list_devices should return a list (may be empty in CI)."""
        try:
            devices = Recorder.list_devices()
            assert isinstance(devices, list)
        except (OSError, sd.PortAudioError):
            pass  # sound system may not be available in CI
