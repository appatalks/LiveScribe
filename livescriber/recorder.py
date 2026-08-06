"""Audio recording — captures mic + system audio, keeps everything in memory."""

from __future__ import annotations

import datetime
import io
import platform
import shutil
import subprocess
import threading
import wave
from pathlib import Path
from typing import Callable

import numpy as np
import sounddevice as sd

from livescriber.config import AudioConfig


class Recorder:
    """Captures mic input + system audio output and mixes into a single stream."""

    _SILENCE_RMS_THRESHOLD = 0.002

    def __init__(self, config: AudioConfig, on_chunk: Callable[[np.ndarray], None] | None = None):
        self.cfg = config
        self.on_chunk = on_chunk

        self._mic_stream: sd.InputStream | None = None
        self._sys_stream: sd.InputStream | None = None  # macOS virtual audio device
        self._parec_proc: subprocess.Popen | None = None  # Linux parec subprocess
        self._parec_thread: threading.Thread | None = None
        self._mic_frames: list[np.ndarray] = []
        self._sys_frames: list[np.ndarray] = []
        self._recording = False
        self._lock = threading.Lock()
        self._mic_rate: int = config.sample_rate
        self._mic_device_name: str | None = None
        self._sys_rate: int = 16000
        self._processed_audio: np.ndarray | None = None
        self._record_start: datetime.datetime | None = None
        self._system_audio_device_name: str | None = None

    # ── Public API ─────────────────────────────────────────────────────────

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def has_audio(self) -> bool:
        return self._processed_audio is not None and self._processed_audio.size > 0

    @property
    def system_audio_active(self) -> bool:
        """Return True when a system-audio capture path is currently active."""
        return (
            self._sys_stream is not None
            or self._parec_proc is not None
        )

    @property
    def duration_seconds(self) -> float:
        if self._recording:
            with self._lock:
                total_samples = sum(f.shape[0] for f in self._mic_frames)
            return total_samples / self._mic_rate
        elif self._processed_audio is not None:
            return self._processed_audio.size / self.cfg.sample_rate
        return 0.0

    @property
    def record_timestamp(self) -> datetime.datetime | None:
        return self._record_start

    def start(self) -> None:
        """Begin recording mic + system audio into memory."""
        if self._recording:
            raise RuntimeError("Already recording")

        with self._lock:
            self._mic_frames.clear()
            self._sys_frames.clear()
        self._processed_audio = None
        self._record_start = datetime.datetime.now()
        self._system_audio_device_name = None

        # ── Mic stream ─────────────────────────────────────────────────
        try:
            mic_device = self._select_mic_input_device()
            dev_info = sd.query_devices(mic_device)
            self._mic_rate = int(dev_info["default_samplerate"])
            self._mic_device_name = str(dev_info["name"])
        except Exception:
            mic_device = None
            self._mic_rate = 44100
            self._mic_device_name = None

        self._recording = True
        self._mic_stream = sd.InputStream(
            device=mic_device,
            samplerate=self._mic_rate,
            channels=1,
            dtype=self.cfg.dtype,
            callback=self._mic_callback,
        )
        self._mic_stream.start()
        if self._mic_device_name:
            print(f"[Recorder] Capturing microphone: {self._mic_device_name}")

        # ── System audio stream (monitor source) ──────────────────────
        if self.cfg.capture_system_audio:
            self._start_system_capture()

    def stop(self) -> None:
        """Stop recording. Audio stays in memory."""
        if not self._recording:
            raise RuntimeError("Not recording")

        self._recording = False

        if self._mic_stream:
            self._mic_stream.stop()
            self._mic_stream.close()
            self._mic_stream = None

        if self._sys_stream:
            self._sys_stream.stop()
            self._sys_stream.close()
            self._sys_stream = None

        if self._parec_proc:
            self._parec_proc.terminate()
            try:
                self._parec_proc.wait(timeout=3)
            except Exception:
                self._parec_proc.kill()
            self._parec_proc = None
        if self._parec_thread:
            self._parec_thread.join(timeout=3)
            self._parec_thread = None

        self._processed_audio = self._process_audio()

    def get_audio(self) -> np.ndarray:
        """Return processed audio (16 kHz, mono, normalized float32)."""
        if self._processed_audio is not None:
            return self._processed_audio
        return np.array([], dtype=np.float32)

    def get_wav_bytes(self) -> bytes:
        """Return audio as WAV file bytes."""
        audio = self.get_audio()
        if audio.size == 0:
            return b""
        buf = io.BytesIO()
        audio_int16 = np.clip(audio * 32767, -32768, 32767).astype(np.int16)
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.cfg.sample_rate)
            wf.writeframes(audio_int16.tobytes())
        return buf.getvalue()

    def save_wav(self, path: Path) -> Path:
        """Export audio to a WAV file on disk."""
        data = self.get_wav_bytes()
        if not data:
            raise RuntimeError("No audio to save")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def clear(self) -> None:
        with self._lock:
            self._mic_frames.clear()
            self._sys_frames.clear()
        self._processed_audio = None
        self._record_start = None

    @staticmethod
    def list_devices() -> list[dict]:
        devices = sd.query_devices()
        return [
            {"index": i, "name": d["name"], "channels": d["max_input_channels"]}
            for i, d in enumerate(devices)
            if d["max_input_channels"] > 0
        ]

    @staticmethod
    def _select_mic_input_device() -> int | None:
        """Choose a microphone without forcing a Bluetooth headset into call mode."""
        if platform.system() != "Darwin":
            return None

        try:
            default_input, default_output = sd.default.device
            devices = sd.query_devices()
            input_device = devices[default_input]
            output_device = devices[default_output]
        except Exception:
            return None

        input_name = str(input_device["name"]).lower()
        output_name = str(output_device["name"]).lower()
        if input_name != output_name:
            return default_input

        for index, device in enumerate(devices):
            name = str(device["name"]).lower()
            if (
                device["max_input_channels"] > 0
                and ("macbook" in name or "built-in" in name)
                and ("microphone" in name or "mic" in name)
            ):
                return index

        return default_input

    # ── System audio capture ──────────────────────────────────────────────

    def _start_system_capture(self) -> None:
        """Capture system audio output on the current platform."""
        import platform

        system = platform.system()
        if system == "Darwin":
            self._start_system_capture_macos()
        elif system == "Windows":
            self._start_system_capture_windows()
        else:
            self._start_system_capture_linux()

    # ── Linux: parec ───────────────────────────────────────────────────────

    def _start_system_capture_linux(self) -> None:
        """Find a monitor source and capture it via parec subprocess."""
        if not shutil.which("parec"):
            print("[Recorder] parec not found — system audio capture unavailable")
            print("[Recorder] Install PulseAudio utils: sudo apt install pulseaudio-utils")
            return

        monitor = self._find_monitor_source()
        if not monitor:
            print("[Recorder] No monitor source found — mic only")
            return

        source_name = monitor["pa_name"]
        self._sys_rate = 16000

        try:
            self._parec_proc = subprocess.Popen(
                [
                    "parec",
                    "--rate=16000",
                    "--channels=1",
                    "--format=float32le",
                    f"--device={source_name}",
                    "--raw",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            self._parec_thread = threading.Thread(
                target=self._parec_reader, daemon=True
            )
            self._parec_thread.start()
            print(f"[Recorder] Capturing system audio: {source_name}")
        except Exception as exc:
            print(f"[Recorder] Failed to start parec: {exc}")
            self._parec_proc = None

    # ── macOS: loopback device ─────────────────────────────────────────────

    def _start_system_capture_macos(self) -> None:
        """Capture system audio from a configured loopback input device."""
        system_audio_idx = self._find_macos_system_audio_device()
        if system_audio_idx is None:
            print("[Recorder] No system-audio loopback device found — mic only")
            return

        try:
            dev_info = sd.query_devices(system_audio_idx)
            self._sys_rate = int(dev_info["default_samplerate"])
            channels = max(1, min(int(dev_info["max_input_channels"]), 2))

            sd.check_input_settings(
                device=system_audio_idx,
                samplerate=self._sys_rate,
                channels=channels,
                dtype=self.cfg.dtype,
            )

            self._sys_stream = sd.InputStream(
                device=system_audio_idx,
                samplerate=self._sys_rate,
                channels=channels,
                dtype=self.cfg.dtype,
                callback=self._sys_callback,
            )
            self._sys_stream.start()
            self._system_audio_device_name = str(dev_info["name"])
            print(f"[Recorder] Capturing system audio: {dev_info['name']} (device {system_audio_idx})")
        except Exception as exc:
            print(f"[Recorder] Failed to open virtual audio device: {exc}")
            self._sys_stream = None

    # ── Windows: WASAPI loopback ───────────────────────────────────────────

    def _start_system_capture_windows(self) -> None:
        """Capture system audio via a Windows loopback or mixer input device."""
        if not hasattr(sd, "WasapiSettings"):
            print("[Recorder] WASAPI support is unavailable in this sounddevice build — mic only")
            return

        loopback_idx = self._find_windows_loopback_device()
        if loopback_idx is None:
            print("[Recorder] No Windows system-audio input found — mic only")
            print("[Recorder] Enable Stereo Mix or a similar loopback input if your driver provides one")
            return

        try:
            dev_info = sd.query_devices(loopback_idx)
            self._sys_rate = int(dev_info["default_samplerate"])
            channels = max(1, min(int(dev_info["max_input_channels"]), 2))
            extra_settings = None
            hostapi_name = sd.query_hostapis(dev_info["hostapi"])["name"].lower()
            if "wasapi" in hostapi_name:
                extra_settings = sd.WasapiSettings(auto_convert=True)

            sd.check_input_settings(
                device=loopback_idx,
                samplerate=self._sys_rate,
                channels=channels,
                dtype=self.cfg.dtype,
                extra_settings=extra_settings,
            )

            self._sys_stream = sd.InputStream(
                device=loopback_idx,
                samplerate=self._sys_rate,
                channels=channels,
                dtype=self.cfg.dtype,
                latency="high",
                extra_settings=extra_settings,
                callback=self._sys_callback,
            )
            self._sys_stream.start()
            print(f"[Recorder] Capturing system audio: {dev_info['name']} (device {loopback_idx})")
        except Exception as exc:
            print(f"[Recorder] Failed to open WASAPI loopback device: {exc}")
            self._sys_stream = None

    def _sys_callback(self, indata: np.ndarray, frames: int, time_info, status):
        """Callback for platform-specific system audio streams."""
        if status:
            print(f"[Recorder/sys] {status}")
        with self._lock:
            self._sys_frames.append(indata.copy())

    @staticmethod
    def _find_macos_system_audio_device() -> int | None:
        """Find the best installed macOS virtual system-audio input device."""
        devices = sd.query_devices()
        loopback_names = [
            "blackhole",
            "loopback",
            "soundflower",
            "vb-cable",
            "parrot audio",
        ]
        candidates: list[tuple[int, int]] = []
        for i, d in enumerate(devices):
            if d["max_input_channels"] > 0:
                name_lower = d["name"].lower()
                for score, name in enumerate(reversed(loopback_names), start=1):
                    if name in name_lower:
                        candidates.append((score, i))
                        break

        if not candidates:
            return None

        candidates.sort(reverse=True)
        return candidates[0][1]

    @staticmethod
    def _find_windows_loopback_device() -> int | None:
        """Find the best Windows system-audio capture input device."""
        try:
            devices = sd.query_devices()
            hostapis = sd.query_hostapis()
        except Exception:
            return None

        preferred_names = [
            "stereo mix",
            "wave out mix",
            "what u hear",
            "loopback",
            "monitor",
        ]
        preferred_hostapis = {
            "windows wasapi": 30,
            "windows wdm-ks": 20,
            "mme": 10,
        }

        candidates: list[tuple[int, int]] = []
        for i, device in enumerate(devices):
            if device["max_input_channels"] <= 0:
                continue

            hostapi_name = hostapis[device["hostapi"]]["name"].lower()
            name_lower = device["name"].lower()
            score = 0

            for term in preferred_names:
                if term in name_lower:
                    score += 100

            score += preferred_hostapis.get(hostapi_name, 0)

            if score > 0:
                candidates.append((score, i))

        if not candidates:
            return None

        candidates.sort(reverse=True)
        return candidates[0][1]

    def _parec_reader(self) -> None:
        """Read raw float32 audio from parec stdout in chunks."""
        chunk_bytes = 16000 * 4  # 1 second of float32 at 16kHz
        proc = self._parec_proc
        while self._recording and proc and proc.poll() is None:
            data = proc.stdout.read(chunk_bytes)
            if not data:
                break
            audio = np.frombuffer(data, dtype=np.float32).copy()
            with self._lock:
                self._sys_frames.append(audio)

    @staticmethod
    def _find_monitor_source() -> dict | None:
        """Find the best monitor source via pactl."""
        if not shutil.which("pactl"):
            return None

        try:
            result = subprocess.run(
                ["pactl", "list", "sources", "short"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return None

            monitors = []
            for line in result.stdout.strip().split("\n"):
                parts = line.split("\t")
                if len(parts) >= 2 and ".monitor" in parts[1]:
                    rate = 48000
                    if len(parts) > 3:
                        fmt = parts[3]
                        if "Hz" in fmt:
                            try:
                                rate = int(fmt.split("Hz")[0].split()[-1])
                            except Exception:
                                pass
                    state = parts[4].strip() if len(parts) > 4 else ""
                    monitors.append({
                        "pa_name": parts[1],
                        "rate": rate,
                        "state": state,
                    })

            # Prefer RUNNING monitor (active audio output)
            for m in monitors:
                if m["state"] == "RUNNING":
                    return m
            return monitors[0] if monitors else None

        except Exception:
            return None

    # ── Callbacks ──────────────────────────────────────────────────────────

    def _mic_callback(self, indata: np.ndarray, frames: int, time_info, status):
        if status:
            print(f"[Recorder/mic] {status}")
        with self._lock:
            self._mic_frames.append(indata.copy())
        if self.on_chunk:
            try:
                self.on_chunk(indata.copy())
            except Exception:
                pass


    # ── Audio processing ───────────────────────────────────────────────────

    def _resample_to_target(self, audio: np.ndarray, source_rate: int) -> np.ndarray:
        """Resample audio to target rate (16 kHz) using linear interpolation."""
        target_rate = self.cfg.sample_rate
        if source_rate == target_rate:
            return audio
        target_length = int(len(audio) * target_rate / source_rate)
        indices = np.linspace(0, len(audio) - 1, target_length)
        return np.interp(indices, np.arange(len(audio)), audio).astype(np.float32)

    def _process_audio(self) -> np.ndarray:
        """Mix mic + system audio, resample to 16 kHz, normalize."""
        with self._lock:
            mic_data = list(self._mic_frames)
            sys_data = list(self._sys_frames)

        # Process mic audio
        if mic_data:
            mic_audio = np.concatenate(mic_data)
            if mic_audio.ndim > 1:
                mic_audio = mic_audio[:, 0]
            mic_audio = self._resample_to_target(mic_audio, self._mic_rate)
        else:
            mic_audio = np.array([], dtype=np.float32)

        # Process system audio
        if sys_data:
            sys_audio = np.concatenate(sys_data)
            if sys_audio.ndim > 1:
                sys_audio = sys_audio[:, 0]
            sys_audio = self._resample_to_target(sys_audio, self._sys_rate)
        else:
            sys_audio = np.array([], dtype=np.float32)

        # Mix: pad shorter to match longer, then sum
        if mic_audio.size > 0 and sys_audio.size > 0:
            max_len = max(mic_audio.size, sys_audio.size)
            if mic_audio.size < max_len:
                mic_audio = np.pad(mic_audio, (0, max_len - mic_audio.size))
            if sys_audio.size < max_len:
                sys_audio = np.pad(sys_audio, (0, max_len - sys_audio.size))
            audio = mic_audio + sys_audio
        elif mic_audio.size > 0:
            audio = mic_audio
        elif sys_audio.size > 0:
            audio = sys_audio
        else:
            return np.array([], dtype=np.float32)

        rms = float(np.sqrt(np.mean(np.square(audio))))
        if rms < self._SILENCE_RMS_THRESHOLD:
            return np.array([], dtype=np.float32)

        # Normalize recorded speech while avoiding excessive gain on room noise.
        peak = np.max(np.abs(audio))
        if 0 < peak < 0.1:
            audio = audio * min(8.0, 0.9 / peak)
        elif peak > 1.0:
            audio = audio / peak

        return audio.astype(np.float32, copy=False)
