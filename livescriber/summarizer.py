"""LLM-powered summarization — supports Copilot, local llama.cpp, ollama-like servers, and OpenAI backends."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable

import requests

from livescriber.config import MODELS_DIR, SummarizerConfig


LOCAL_MODEL_CATALOG = {
    "gemma-2-2b-it": {
        "label": "Gemma 2 2B Instruct",
        "repo_id": "bartowski/gemma-2-2b-it-GGUF",
        "filename": "gemma-2-2b-it-Q4_K_M.gguf",
        "size": "2B",
        "ram": "~3 GB RAM",
        "max_context": 8192,
    },
    "gemma-2-9b-it": {
        "label": "Gemma 2 9B Instruct",
        "repo_id": "bartowski/gemma-2-9b-it-GGUF",
        "filename": "gemma-2-9b-it-Q4_K_M.gguf",
        "size": "9B",
        "ram": "~9 GB RAM",
        "max_context": 8192,
    },
    "llama-3.1-4b-instruct": {
        "label": "Llama 3.1 ELM Turbo 4B Instruct",
        "repo_id": "mradermacher/Llama3.1-elm-turbo-4B-instruct-GGUF",
        "filename": "Llama3.1-elm-turbo-4B-instruct.Q4_K_M.gguf",
        "size": "4B",
        "ram": "~5 GB RAM",
        "max_context": 8192,
    },
    "mistral-nemo-12b-instruct": {
        "label": "Mistral Nemo 12B Instruct",
        "repo_id": "bartowski/Mistral-Nemo-Instruct-2407-GGUF",
        "filename": "Mistral-Nemo-Instruct-2407-Q4_K_M.gguf",
        "size": "12B",
        "ram": "~12 GB RAM",
        "max_context": 32768,
    },
}

LOCAL_SUMMARY_MODELS_DIR = MODELS_DIR / "summarizer"


class Summarizer:
    """Generate meeting summaries from transcript text."""

    def __init__(self, config: SummarizerConfig):
        self.cfg = config
        self._local_llm = None
        self._local_model_key: str | None = None

    # ── Public API ─────────────────────────────────────────────────────────

    def summarize(self, transcript: str, detected_language: str | None = None,
                  auto_translate_english: bool = False) -> str:
        """Summarize transcript text. Returns the summary string.

        When auto_translate_english is True and the detected language is not
        English, produces a bilingual summary: native language first, then
        an English translation appended below.
        """
        if not transcript.strip():
            return ""

        from livescriber.i18n import get_system_prompt

        # Use a fully translated system prompt for the detected language
        original_prompt = self.cfg.system_prompt
        is_non_english = detected_language and detected_language != "en"
        self._current_lang = detected_language or "en"
        self._auto_translate = auto_translate_english

        if is_non_english:
            self.cfg.system_prompt = get_system_prompt(detected_language)

        backend = self.normalize_backend_name(self.cfg.backend)

        try:
            if backend == "copilot":
                summary = self._summarize_copilot(transcript)
            elif backend == "local":
                summary = self._summarize_local(transcript)
            elif backend == "openai":
                summary = self._summarize_openai(transcript)
            else:
                summary = self._summarize_ollama(transcript)
        finally:
            self.cfg.system_prompt = original_prompt

        # Bilingual: append English summary when auto-translate is on and source isn't English
        # Copilot handles this in a single prompt, so skip the second pass for it
        if (auto_translate_english
                and is_non_english
                and backend != "copilot"
                and not summary.startswith("[Local")
                and not summary.startswith("[Copilot")
                and not summary.startswith("[Server")
                and not summary.startswith("[OpenAI")):
            # Restore original prompt for the English translation pass
            self.cfg.system_prompt = original_prompt
            english_prompt = (
                "Translate the following summary into English. "
                "Output only the English translation, nothing else.\n\n"
                + summary
            )
            if backend == "local":
                english = self._summarize_local(english_prompt)
            elif backend == "openai":
                english = self._summarize_openai(english_prompt)
            else:
                english = self._summarize_ollama(english_prompt)

            if english and not english.startswith("["):
                summary += "\n\n---\n\n**English Summary:**\n\n" + english

        return summary

    def summarize_async(
        self,
        transcript: str,
        on_complete: Callable[[str], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
        detected_language: str | None = None,
        auto_translate_english: bool = False,
    ) -> threading.Thread:
        """Run summarization in a background thread."""

        def _worker():
            try:
                result = self.summarize(
                    transcript,
                    detected_language=detected_language,
                    auto_translate_english=auto_translate_english,
                )
                if on_complete:
                    on_complete(result)
            except Exception as exc:
                if on_error:
                    on_error(exc)

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        return t

    # ── Copilot CLI backend ────────────────────────────────────────────────

    @staticmethod
    def get_copilot_model_options() -> list[str]:
        """Return model names supported by the installed Copilot CLI."""
        command = Summarizer._build_copilot_command("help", "config")
        if not command:
            return ["auto"]

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ["auto"]

        if result.returncode != 0:
            return ["auto"]

        models = Summarizer._parse_copilot_model_options(result.stdout)
        return ["auto", *models] if models else ["auto"]

    @staticmethod
    def _parse_copilot_model_options(config_help: str) -> list[str]:
        """Extract the model catalog from ``copilot help config`` output."""
        models = []
        in_model_section = False

        for line in config_help.splitlines():
            stripped = line.strip()
            if stripped.startswith("`model`:"):
                in_model_section = True
                continue
            if in_model_section and stripped.startswith("`"):
                break
            if in_model_section:
                match = re.fullmatch(r'- "([^"]+)"', stripped)
                if match and match.group(1) not in models:
                    models.append(match.group(1))

        return models

    def _summarize_copilot(self, transcript: str) -> str:
        """Summarize via Copilot CLI (copilot --prompt)."""
        from livescriber.i18n import get_copilot_prompt

        # Use Copilot-specific prompt that frames the task as documentation
        # generation, which Copilot accepts (unlike generic note-taking prompts)
        lang = getattr(self, '_current_lang', 'en') or 'en'
        append_english = getattr(self, '_auto_translate', False)
        copilot_prompt = get_copilot_prompt(lang, append_english=append_english) + transcript

        command = self._build_copilot_command(
            "--model",
            self.cfg.copilot_model,
            "--prompt",
            copilot_prompt,
            "--allow-all-tools",
            "--no-color",
        )
        if not command:
            return "[Copilot CLI not found] Install it from https://github.com/github/copilot-cli"

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=180,
            )

            stdout_text = result.stdout.strip()
            stderr_text = result.stderr.strip()

            if result.returncode != 0:
                return f"[Copilot CLI error] {stderr_text or stdout_text or 'Unknown error'}"

            if self._copilot_stderr_has_error(stderr_text):
                return f"[Copilot CLI error] {self._extract_copilot_error(stderr_text)}"

            # Parse output — strip the usage stats footer
            output = stdout_text
            lines = output.split("\n")

            # Find where the stats footer begins (starts with empty line then "Total usage")
            summary_lines = []
            for line in lines:
                if line.strip().startswith("Total usage") or line.strip().startswith("Usage by model"):
                    break
                summary_lines.append(line)

            # Remove trailing empty lines
            while summary_lines and not summary_lines[-1].strip():
                summary_lines.pop()

            summary = "\n".join(summary_lines).strip()
            if summary:
                return summary

            if stderr_text:
                return f"[Copilot CLI error] {self._extract_copilot_error(stderr_text)}"

            return "[Copilot CLI error] No summary was returned"

        except subprocess.TimeoutExpired:
            return "[Copilot CLI timeout] The request took too long"
        except Exception as exc:
            return f"[Copilot CLI error] {exc}"

    @staticmethod
    def _copilot_stderr_has_error(stderr_text: str) -> bool:
        """Return True when Copilot emitted a real error despite a zero exit code."""
        if not stderr_text:
            return False

        error_markers = (
            "error:",
            "no authentication information found",
            "failed",
            "exception",
        )
        lowered = stderr_text.lower()
        return any(marker in lowered for marker in error_markers)

    @staticmethod
    def _extract_copilot_error(stderr_text: str) -> str:
        """Reduce noisy CLI stderr down to the most relevant user-facing error lines."""
        lines = [line.strip() for line in stderr_text.splitlines() if line.strip()]
        relevant = []
        for line in lines:
            lower = line.lower()
            if "microsoft.powershell_profile" in lower:
                continue
            if "missing file specification after redirection operator" in lower:
                continue
            if line.startswith("At ") or line.startswith("char:"):
                continue
            if line.startswith("+") or line.startswith("~"):
                continue
            if line.startswith("CategoryInfo") or line.startswith("FullyQualifiedErrorId"):
                continue
            relevant.append(line)

        return "\n".join(relevant) if relevant else stderr_text.strip()

    @staticmethod
    def _build_copilot_command(*args: str) -> list[str] | None:
        """Build a working Copilot CLI command across supported platforms."""
        copilot_bin = shutil.which("copilot")
        if not copilot_bin:
            return None

        if platform.system() == "Windows" and copilot_bin.lower().endswith(".bat"):
            copilot_ps1 = Summarizer._find_adjacent_powershell_shim(copilot_bin)
            if copilot_ps1:
                return [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    copilot_ps1,
                    *args,
                ]

        return [copilot_bin, *args]

    @staticmethod
    def launch_copilot_login() -> tuple[bool, str]:
        """Launch the Copilot CLI login flow in a user-visible terminal."""
        system = platform.system()
        if system == "Windows":
            copilot_bin = shutil.which("copilot")
            copilot_ps1 = Summarizer._find_adjacent_powershell_shim(copilot_bin or "")
            if copilot_ps1:
                subprocess.Popen(
                    [
                        "powershell",
                        "-NoExit",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        copilot_ps1,
                        "login",
                    ],
                    creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
                )
                return True, "Opened Copilot login in a new PowerShell window."
            if copilot_bin:
                subprocess.Popen(
                    ["cmd", "/k", copilot_bin, "login"],
                    creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
                )
                return True, "Opened Copilot login in a new Command Prompt window."
            return False, "Copilot CLI was not found in PATH."

        if system == "Darwin":
            copilot_bin = shutil.which("copilot")
            if not copilot_bin:
                return False, "Copilot CLI was not found in PATH."

            command = f'{copilot_bin} login'
            apple_script = (
                'tell application "Terminal" to do script ' + json.dumps(command) + '\n'
                'tell application "Terminal" to activate'
            )
            subprocess.Popen(["osascript", "-e", apple_script])
            return True, "Opened Copilot login in Terminal."

        copilot_bin = shutil.which("copilot")
        if not copilot_bin:
            return False, "Copilot CLI was not found in PATH."

        shell_command = f'{copilot_bin} login; exec bash'
        terminal_commands = [
            ["x-terminal-emulator", "-e", "bash", "-lc", shell_command],
            ["gnome-terminal", "--", "bash", "-lc", shell_command],
            ["konsole", "-e", "bash", "-lc", shell_command],
            ["xfce4-terminal", "-e", f"bash -lc \"{shell_command}\""],
            ["xterm", "-e", "bash", "-lc", shell_command],
        ]
        for command in terminal_commands:
            if shutil.which(command[0]):
                subprocess.Popen(command)
                return True, "Opened Copilot login in a terminal window."

        return False, "No supported terminal emulator was found to launch Copilot login."

    @staticmethod
    def _find_adjacent_powershell_shim(copilot_bin: str) -> str | None:
        """Find the PowerShell Copilot shim that lives next to the batch launcher on Windows."""
        if not copilot_bin:
            return None

        candidate = str(Path(copilot_bin).with_suffix(".ps1"))
        if Path(candidate).exists():
            return candidate
        return None

    # ── Embedded local llama.cpp backend ──────────────────────────────────

    def _summarize_local(self, transcript: str) -> str:
        """Summarize with a GGUF model embedded directly in the app."""
        model_path = self.get_local_model_path(self.cfg.local_model_key)
        if not model_path.exists():
            label = self.get_local_model_options().get(self.cfg.local_model_key, self.cfg.local_model_key)
            return (
                f"[Local model not downloaded] {label}\n"
                "Open Settings, choose the Local backend, and click Download."
            )

        # Auto-scale context window based on model capability
        effective_ctx = self._effective_context_window()

        # Truncate transcript to avoid overflowing the context window.
        # Non-English text uses ~2-4x more tokens per character.
        max_chars = effective_ctx * 2
        if len(transcript) > max_chars:
            transcript = transcript[:max_chars] + "\n\n[...transcript truncated to fit context window]"

        try:
            llm = self._ensure_local_llm(model_path)

            # Enable fault handler to get a traceback on segfaults instead of silent crash
            import faulthandler
            import sys
            if hasattr(sys, "stderr") and sys.stderr is not None and hasattr(sys.stderr, "fileno"):
                try:
                    faulthandler.enable()
                except Exception:
                    pass

            messages = [
                {"role": "system", "content": self.cfg.system_prompt},
                {
                    "role": "user",
                    "content": f"Here is the meeting transcript:\n\n{transcript}",
                },
            ]
            try:
                response = llm.create_chat_completion(
                    messages=messages,
                    temperature=self.cfg.local_temperature,
                    max_tokens=self.cfg.local_max_tokens,
                )
            except Exception as exc:
                if "system role not supported" not in str(exc).lower():
                    raise

                response = llm.create_chat_completion(
                    messages=[
                        {
                            "role": "user",
                            "content": (
                                f"{self.cfg.system_prompt}\n\n"
                                f"Here is the meeting transcript:\n\n{transcript}"
                            ),
                        }
                    ],
                    temperature=self.cfg.local_temperature,
                    max_tokens=self.cfg.local_max_tokens,
                )
            return response["choices"][0]["message"]["content"].strip()
        except ModuleNotFoundError:
            return (
                "[Local runtime unavailable] llama-cpp-python is not installed.\n"
                "Install the local backend runtime or use Copilot, Ollama, or OpenAI instead."
            )
        except Exception as exc:
            return (
                f"[Local summarization error] {exc}\n\n"
                "If this keeps happening with non-English text, try switching to "
                "Copilot, Ollama, or OpenAI backend in Settings."
            )

    def _ensure_local_llm(self, model_path: Path):
        """Load the configured local GGUF model if needed."""
        effective_ctx = self._effective_context_window()
        needs_reload = (
            self._local_llm is None
            or self._local_model_key != self.cfg.local_model_key
            or getattr(self, "_loaded_ctx", None) != effective_ctx
        )
        if not needs_reload:
            return self._local_llm

        from llama_cpp import Llama

        threads = max(1, (os.cpu_count() or 4) - 1)
        self._local_llm = Llama(
            model_path=str(model_path),
            n_ctx=effective_ctx,
            n_threads=threads,
            n_gpu_layers=self.cfg.local_gpu_layers,
            verbose=False,
        )
        self._local_model_key = self.cfg.local_model_key
        self._loaded_ctx = effective_ctx
        return self._local_llm

    def _effective_context_window(self) -> int:
        """Return the context window to use, capped at the model's max_context.

        The effective context window will not exceed the model's catalog
        max_context value. If the user configures a smaller context window,
        that smaller value is used instead.
        """
        user_ctx = self.cfg.local_context_window
        meta = LOCAL_MODEL_CATALOG.get(self.cfg.local_model_key, {})
        model_max = meta.get("max_context", user_ctx)
        return min(user_ctx, model_max) if user_ctx > 0 else model_max

    @staticmethod
    def get_local_model_options() -> dict[str, str]:
        """Return the embedded local summarizer model labels keyed by config id."""
        return {
            key: f"{meta['label']} ({meta['size']}, {meta['ram']})"
            for key, meta in LOCAL_MODEL_CATALOG.items()
        }

    @staticmethod
    def get_local_model_path(model_key: str) -> Path:
        """Return the expected on-disk path for a downloaded local GGUF model."""
        meta = LOCAL_MODEL_CATALOG.get(model_key)
        if not meta:
            return LOCAL_SUMMARY_MODELS_DIR / model_key / "model.gguf"
        return LOCAL_SUMMARY_MODELS_DIR / model_key / meta["filename"]

    @staticmethod
    def is_local_model_downloaded(model_key: str) -> bool:
        """Return True when the selected embedded model is already present on disk."""
        return Summarizer.get_local_model_path(model_key).exists()

    @staticmethod
    def download_local_model(model_key: str) -> Path:
        """Download the selected embedded model into the app's local cache."""
        meta = LOCAL_MODEL_CATALOG.get(model_key)
        if not meta:
            raise ValueError(f"Unknown local model: {model_key}")

        try:
            from huggingface_hub import hf_hub_download
            from huggingface_hub.utils import disable_progress_bars
        except Exception as exc:
            raise RuntimeError("huggingface_hub is required to download local models") from exc

        target_dir = LOCAL_SUMMARY_MODELS_DIR / model_key
        target_dir.mkdir(parents=True, exist_ok=True)

        # In packaged GUI builds, tqdm can be given a missing stderr stream and crash
        # while rendering progress. Disable progress bars for app-managed downloads.
        disable_progress_bars()

        downloaded_path = hf_hub_download(
            repo_id=meta["repo_id"],
            filename=meta["filename"],
            local_dir=str(target_dir),
        )
        return Path(downloaded_path)

    @staticmethod
    def has_local_runtime() -> bool:
        """Return True when llama-cpp-python is importable in the current environment."""
        try:
            import llama_cpp  # noqa: F401
            return True
        except Exception:
            return False

    @staticmethod
    def normalize_backend_name(backend: str) -> str:
        """Map legacy backend labels to the current canonical names."""
        if backend == "ollama":
            return "ollama-like"
        return backend

    # ── Ollama-like / Local server backend ────────────────────────────────

    def _summarize_ollama(self, transcript: str) -> str:
        """Summarize via an ollama-like local server — tries OpenAI-compatible API first, then Ollama native."""
        # Try OpenAI-compatible endpoint first (LM Studio, vLLM, etc.)
        try:
            from openai import OpenAI

            base_url = self.cfg.ollama_url.rstrip("/")
            if not base_url.endswith("/v1"):
                base_url = f"{base_url}/v1"

            client = OpenAI(
                base_url=base_url,
                api_key="local",  # most local servers don't need a real key
            )
            response = client.chat.completions.create(
                model=self.cfg.ollama_model,
                messages=[
                    {"role": "system", "content": self.cfg.system_prompt},
                    {
                        "role": "user",
                        "content": f"Here is the meeting transcript:\n\n{transcript}",
                    },
                ],
                temperature=0.3,
            )
            return response.choices[0].message.content.strip()
        except Exception:
            pass

        # Fall back to native Ollama API
        url = f"{self.cfg.ollama_url}/api/generate"
        prompt = f"{self.cfg.system_prompt}\n\nTranscript:\n{transcript}\n\nSummary:"

        payload = {
            "model": self.cfg.ollama_model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.3},
        }

        try:
            resp = requests.post(url, json=payload, timeout=120)
            resp.raise_for_status()
            return resp.json().get("response", "").strip()
        except requests.ConnectionError:
            return (
                f"[Server not reachable at {self.cfg.ollama_url}]\n"
                "Make sure the server is running."
            )
        except Exception as exc:
            return f"[Summarization error] {exc}"

    # ── OpenAI backend ─────────────────────────────────────────────────────

    def _summarize_openai(self, transcript: str) -> str:
        try:
            from openai import OpenAI

            client = OpenAI(api_key=self.cfg.openai_api_key)
            response = client.chat.completions.create(
                model=self.cfg.openai_model,
                messages=[
                    {"role": "system", "content": self.cfg.system_prompt},
                    {
                        "role": "user",
                        "content": f"Here is the meeting transcript:\n\n{transcript}",
                    },
                ],
                temperature=0.3,
                max_tokens=2048,
            )
            return response.choices[0].message.content.strip()
        except Exception as exc:
            return f"[OpenAI error] {exc}"

    # ── Utility ────────────────────────────────────────────────────────────

    @staticmethod
    def check_ollama(url: str = "http://localhost:11434") -> bool:
        """Return True if an ollama-like server is reachable."""
        try:
            resp = requests.get(url, timeout=3)
            return resp.status_code == 200
        except Exception:
            return False
