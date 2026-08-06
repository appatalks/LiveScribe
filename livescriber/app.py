"""Main floating window application."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from PyQt6.QtCore import (
    Qt,
    QPoint,
    QTimer,
    QEvent,
    QRect,
    pyqtSignal,
    pyqtSlot,
    QSize,
)
from PyQt6.QtGui import QIcon, QPainter, QColor, QFont, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QTextEdit,
    QFrame,
    QSizePolicy,
    QFileDialog,
    QMessageBox,
    QDialog,
    QComboBox,
    QCheckBox,
    QSlider,
    QFormLayout,
    QDialogButtonBox,
    QLineEdit,
    QGroupBox,
    QSpinBox,
    QTabWidget,
    QScrollArea,
)

from livescriber.config import AppConfig
from livescriber.recorder import Recorder
from livescriber.transcriber import Transcriber
from livescriber.summarizer import Summarizer
from livescriber.styles import get_theme
from livescriber.i18n import t


def _resolve_assets_dir() -> Path | None:
    """Return the bundled assets directory when available."""
    candidates: list[Path] = []

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "assets")

    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir / "assets")
        candidates.append(exe_dir / "_internal" / "assets")

    candidates.append(Path(__file__).resolve().parent.parent / "assets")

    for candidate in candidates:
        if candidate.exists():
            return candidate

    return None


def _resolve_app_icon_path() -> Path | None:
    """Return the bundled app icon path when available."""
    assets_dir = _resolve_assets_dir()
    if not assets_dir:
        return None

    candidate = assets_dir / "livescriber.ico"
    return candidate if candidate.exists() else None


class RecordButton(QPushButton):
    """Circular record button with mic icon matching the LiveScriber banner."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("recordBtn")
        self.setFixedSize(72, 72)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._recording = False

    def set_recording(self, state: bool):
        self._recording = state
        self.setProperty("recording", "true" if state else "false")
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        cx, cy = w // 2, h // 2

        if self._recording:
            # Stop square
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#1e1e2e"))
            painter.drawRoundedRect(cx - 10, cy - 10, 20, 20, 3, 3)
        else:
            from PyQt6.QtGui import QPen
            from PyQt6.QtCore import QRectF

            # Mic body (capsule shape)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#f38ba8"))
            painter.drawRoundedRect(cx - 5, cy - 12, 10, 17, 5, 5)

            # Mic arc
            pen = QPen(QColor("#f38ba8"))
            pen.setWidth(2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            arc_rect = QRectF(cx - 9, cy - 6, 18, 20)
            painter.drawArc(arc_rect, -10 * 16, -160 * 16)

            # Mic stand line
            painter.drawLine(cx, cy + 14, cx, cy + 18)

            # Mic base
            painter.drawLine(cx - 5, cy + 18, cx + 5, cy + 18)

        painter.end()


class CollapsibleSection(QWidget):
    """A toggleable section with a header button and content area."""

    def __init__(self, title: str, object_name: str, editable: bool = False, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Toggle button
        self.toggle_btn = QPushButton(f"▶  {title}")
        self.toggle_btn.setObjectName("sectionToggle")
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_btn.toggled.connect(self._on_toggle)
        layout.addWidget(self.toggle_btn)

        # Content area
        self.content = QTextEdit()
        self.content.setObjectName(object_name)
        self.content.setReadOnly(not editable)
        self.content.setMinimumHeight(80)
        self.content.setMaximumHeight(250)
        self.content.setVisible(False)
        self.content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.content)

        self._title = title

    def _on_toggle(self, checked: bool):
        self.content.setVisible(checked)
        arrow = "▼" if checked else "▶"
        self.toggle_btn.setText(f"{arrow}  {self._title}")

    def set_text(self, text: str):
        self.content.setPlainText(text)

    def append_text(self, text: str):
        self.content.moveCursor(self.content.textCursor().MoveOperation.End)
        self.content.insertPlainText(text + "\n")
        self.content.moveCursor(self.content.textCursor().MoveOperation.End)

    def clear(self):
        self.content.clear()

    def expand(self):
        self.toggle_btn.setChecked(True)


class SettingsDialog(QDialog):
    """Settings dialog with key user-facing options."""

    _sig_local_model_downloaded = pyqtSignal(str)
    _sig_local_model_download_failed = pyqtSignal(str)

    _sig_update_result = pyqtSignal(str)

    def _t(self, key: str) -> str:
        """Shorthand for translating a UI string key."""
        return t(key, self._ui_lang)

    def __init__(self, config: AppConfig, parent=None):
        super().__init__(parent)
        self.cfg = config
        self._ui_lang = config.ui.ui_language
        self.setWindowTitle(self._t("settings_title"))
        self.setMinimumSize(520, 560)
        available = QApplication.primaryScreen().availableGeometry()
        self.resize(
            min(620, available.width() - 80),
            min(760, available.height() - 80),
        )
        self._downloading_local_model = False

        icon_path = _resolve_app_icon_path()
        if icon_path:
            self.setWindowIcon(QIcon(str(icon_path)))

        self._sig_local_model_downloaded.connect(self._on_local_model_downloaded)
        self._sig_local_model_download_failed.connect(self._on_local_model_download_failed)
        self._sig_update_result.connect(self._on_update_result)

        outer_layout = QVBoxLayout(self)
        outer_layout.setSpacing(8)

        self._tabs = QTabWidget()
        outer_layout.addWidget(self._tabs)

        # ── Settings tab ──────────────────────────────────────────────
        settings_scroll = QScrollArea()
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setFrameShape(QFrame.Shape.NoFrame)
        settings_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        settings_widget = QWidget()
        layout = QVBoxLayout(settings_widget)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)
        settings_scroll.setWidget(settings_widget)
        self._tabs.addTab(settings_scroll, self._t("settings"))

        # ── Transcription settings ─────────────────────────────────────
        tx_group = QGroupBox(self._t("grp_transcription"))
        tx_form = QFormLayout(tx_group)
        self._configure_settings_form(tx_form)

        self.model_combo = QComboBox()
        self.model_combo.addItems([
            "distil-large-v3", "large-v3", "medium", "small", "base", "tiny",
        ])
        self.model_combo.setCurrentText(config.transcription.model_size)
        tx_form.addRow(self._t("whisper_model"), self.model_combo)

        self.lang_edit = QLineEdit(config.transcription.language or "")
        self.lang_edit.setPlaceholderText(self._t("auto_detect"))
        tx_form.addRow(self._t("language"), self.lang_edit)

        self.live_transcription_check = QCheckBox(self._t("live_transcription_label"))
        self.live_transcription_check.setChecked(config.transcription.live_transcription)
        self.live_transcription_check.setToolTip(
            "Stream a rough transcription while recording. Results may be incomplete — "
            "use the Transcribe button after stopping for a full-accuracy pass."
        )
        tx_form.addRow(self.live_transcription_check)

        self.auto_translate_check = QCheckBox(self._t("auto_translate_label"))
        self.auto_translate_check.setChecked(config.transcription.auto_translate_english)
        self.auto_translate_check.setToolTip(
            "When enabled, an English translation is appended to the summary. "
            "The transcript stays in the original language."
        )
        tx_form.addRow(self.auto_translate_check)

        layout.addWidget(tx_group)

        # ── Summarization settings ─────────────────────────────────────
        sum_group = QGroupBox(self._t("grp_summarization"))
        sum_form = QFormLayout(sum_group)
        self._configure_settings_form(sum_form)

        self.sum_backend_combo = QComboBox()
        self.sum_backend_combo.addItems(["copilot", "local", "ollama-like", "openai"])
        self.sum_backend_combo.setCurrentText(Summarizer.normalize_backend_name(config.summarizer.backend))
        self.sum_backend_combo.currentTextChanged.connect(self._on_backend_changed)
        sum_form.addRow(self._t("backend"), self.sum_backend_combo)

        self.prompt_edit = QTextEdit()
        from livescriber.i18n import get_system_prompt
        # Show the persisted prompt, falling back to the localized default
        saved_prompt = config.summarizer.system_prompt
        default_en = get_system_prompt("en")
        if saved_prompt == default_en or not saved_prompt:
            self.prompt_edit.setPlainText(get_system_prompt(self._ui_lang))
        else:
            self.prompt_edit.setPlainText(saved_prompt)
        self.prompt_edit.setMaximumHeight(100)
        sum_form.addRow(self._t("system_prompt"), self.prompt_edit)

        layout.addWidget(sum_group)

        self.copilot_group = QGroupBox("Copilot")
        copilot_form = QFormLayout(self.copilot_group)
        self._configure_settings_form(copilot_form)

        self.copilot_model_combo = QComboBox()
        self.copilot_model_combo.addItems(Summarizer.get_copilot_model_options())
        model_index = self.copilot_model_combo.findText(config.summarizer.copilot_model)
        self.copilot_model_combo.setCurrentIndex(max(model_index, 0))
        self.copilot_model_combo.setToolTip(
            "Models are read from the installed Copilot CLI. "
            "Auto lets Copilot choose a model available to your account."
        )
        copilot_form.addRow("Model:", self.copilot_model_combo)

        copilot_auth_row = QHBoxLayout()
        self.copilot_auth_label = QLabel("Launch Copilot CLI login from here if needed.")
        self.copilot_auth_label.setWordWrap(True)
        copilot_auth_row.addWidget(self.copilot_auth_label, stretch=1)

        self.copilot_login_btn = QPushButton("Login")
        self.copilot_login_btn.setFixedWidth(70)
        self.copilot_login_btn.clicked.connect(self._launch_copilot_login)
        copilot_auth_row.addWidget(self.copilot_login_btn)
        copilot_form.addRow("Auth:", copilot_auth_row)

        layout.addWidget(self.copilot_group)

        # ── Embedded local model ─────────────────────────────────────
        self.embedded_group = QGroupBox("Embedded Local Summarizer")
        embedded_form = QFormLayout(self.embedded_group)
        self._configure_settings_form(embedded_form)

        self.local_model_combo = QComboBox()
        self._local_model_options = Summarizer.get_local_model_options()
        for key, label in self._local_model_options.items():
            self.local_model_combo.addItem(label, key)
        local_index = self.local_model_combo.findData(config.summarizer.local_model_key)
        if local_index >= 0:
            self.local_model_combo.setCurrentIndex(local_index)
        self.local_model_combo.currentIndexChanged.connect(self._refresh_local_model_status)
        embedded_form.addRow("Model:", self.local_model_combo)

        self.local_context_spin = QSpinBox()
        self.local_context_spin.setRange(2048, 32768)
        self.local_context_spin.setSingleStep(1024)
        self.local_context_spin.setValue(config.summarizer.local_context_window)
        self.local_context_spin.setSuffix(" tokens")
        embedded_form.addRow("Context window:", self.local_context_spin)

        local_action_row = QHBoxLayout()
        self.local_model_status = QLabel("")
        self.local_model_status.setWordWrap(True)
        local_action_row.addWidget(self.local_model_status, stretch=1)

        self.local_download_btn = QPushButton("Download")
        self.local_download_btn.clicked.connect(self._download_local_model)
        local_action_row.addWidget(self.local_download_btn)
        embedded_form.addRow("Status:", local_action_row)

        layout.addWidget(self.embedded_group)

        # ── Local server (Ollama-like / LM Studio) ───────────────────
        self.local_server_group = QGroupBox("Ollama-Like Server")
        local_form = QFormLayout(self.local_server_group)
        self._configure_settings_form(local_form)

        self.ollama_url_edit = QLineEdit(config.summarizer.ollama_url)
        self.ollama_url_edit.setPlaceholderText("http://localhost:11434")
        local_form.addRow("Server URL:", self.ollama_url_edit)

        ollama_row = QHBoxLayout()
        self.ollama_model_combo = QComboBox()
        self.ollama_model_combo.setEditable(True)
        self.ollama_model_combo.setMinimumWidth(200)
        if config.summarizer.ollama_model:
            self.ollama_model_combo.addItem(config.summarizer.ollama_model)
            self.ollama_model_combo.setCurrentText(config.summarizer.ollama_model)
        ollama_row.addWidget(self.ollama_model_combo)

        self.fetch_btn = QPushButton("Fetch")
        self.fetch_btn.setFixedWidth(60)
        self.fetch_btn.clicked.connect(self._fetch_models)
        ollama_row.addWidget(self.fetch_btn)
        local_form.addRow("Model:", ollama_row)

        layout.addWidget(self.local_server_group)

        # ── API Keys ──────────────────────────────────────────────────
        self.openai_group = QGroupBox("API Keys")
        keys_form = QFormLayout(self.openai_group)
        self._configure_settings_form(keys_form)

        self.openai_key_edit = QLineEdit(config.summarizer.openai_api_key)
        self.openai_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.openai_key_edit.setPlaceholderText("sk-... (for OpenAI backend)")
        keys_form.addRow("OpenAI key:", self.openai_key_edit)

        layout.addWidget(self.openai_group)

        # ── Audio settings ─────────────────────────────────────────────
        audio_group = QGroupBox(self._t("grp_audio"))
        audio_form = QFormLayout(audio_group)
        self._configure_settings_form(audio_form)

        self.capture_sys = QCheckBox(self._t("capture_system_audio"))
        self.capture_sys.setChecked(config.audio.capture_system_audio)
        audio_form.addRow(self.capture_sys)

        layout.addWidget(audio_group)

        # ── UI settings ───────────────────────────────────────────────
        ui_group = QGroupBox(self._t("grp_appearance"))
        ui_form = QFormLayout(ui_group)
        self._configure_settings_form(ui_form)

        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["dark", "light"])
        self.theme_combo.setCurrentText(config.ui.theme)

        theme_row = QHBoxLayout()
        theme_row.addWidget(self.theme_combo)
        self._theme_pro_label = QLabel("")
        theme_row.addWidget(self._theme_pro_label)
        ui_form.addRow(self._t("theme"), theme_row)
        self._refresh_theme_lock()

        self.on_top_check = QCheckBox(self._t("always_on_top"))
        self.on_top_check.setChecked(config.ui.always_on_top)
        ui_form.addRow(self.on_top_check)

        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(50, 100)
        self.opacity_slider.setValue(int(config.ui.opacity * 100))
        ui_form.addRow(self._t("opacity"), self.opacity_slider)

        layout.addWidget(ui_group)

        # ── About tab ─────────────────────────────────────────────────
        about_widget = QWidget()
        about_layout = QVBoxLayout(about_widget)
        about_layout.setSpacing(12)

        import livescriber
        version = livescriber.__version__

        project_group = QGroupBox("About LiveScriber")
        project_form = QFormLayout(project_group)
        self._configure_settings_form(project_form)

        project_form.addRow("Version:", QLabel(version))
        project_form.addRow("Author:", QLabel("appatalks"))

        repo_label = QLabel('<a href="https://github.com/appatalks/LiveScriber">github.com/appatalks/LiveScriber</a>')
        repo_label.setOpenExternalLinks(True)
        project_form.addRow("Repository:", repo_label)

        project_form.addRow("License:", QLabel("MIT"))

        desc_label = QLabel(
            "A floating desktop app that records, transcribes, and summarizes "
            "your spoken audio into organized notes."
        )
        desc_label.setWordWrap(True)
        project_form.addRow(desc_label)

        about_layout.addWidget(project_group)

        # ── Check for updates ─────────────────────────────────────────
        update_group = QGroupBox("Updates")
        update_form = QFormLayout(update_group)
        self._configure_settings_form(update_form)

        self._update_status = QLabel(f"Current version: {version}")
        self._update_status.setWordWrap(True)
        update_form.addRow(self._update_status)

        self._check_update_btn = QPushButton("Check for Updates")
        self._check_update_btn.clicked.connect(self._check_for_updates)
        update_form.addRow(self._check_update_btn)

        about_layout.addWidget(update_group)

        # ── Support ───────────────────────────────────────────────────
        support_group = QGroupBox("Support LiveScriber")
        support_form = QFormLayout(support_group)
        self._configure_settings_form(support_form)

        support_msg = QLabel(
            "LiveScriber is freeware and open source. If it's useful to you, "
            "please consider supporting development. Activate all features for free below."
        )
        support_msg.setWordWrap(True)
        support_form.addRow(support_msg)

        paypal_row = QHBoxLayout()
        paypal_qr_label = QLabel()
        qr_path = _resolve_assets_dir()
        if qr_path:
            qr_file = qr_path / "paypal-qr-code.png"
            if qr_file.exists():
                pixmap = QPixmap(str(qr_file)).scaled(
                    80, 80, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                paypal_qr_label.setPixmap(pixmap)
        paypal_row.addWidget(paypal_qr_label)
        paypal_link = QLabel(
            '<a href="https://www.paypal.com/donate/?hosted_button_id=3KPNXNL6QTZW2"'
            ' style="font-size: 18px; font-weight: bold;">'
            'Donate via PayPal</a>'
        )
        paypal_link.setOpenExternalLinks(True)
        paypal_row.addWidget(paypal_link, stretch=1)
        support_form.addRow("PayPal:", paypal_row)

        btc_addr = "16CowvxvLSR4BPEP9KJZiR622UU7hGEce5"
        btc_label = QLabel(btc_addr)
        btc_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        copy_btc_btn = QPushButton("Copy")
        copy_btc_btn.setFixedWidth(60)
        copy_btc_btn.clicked.connect(
            lambda: (
                QApplication.clipboard().setText(btc_addr),
                QMessageBox.information(self, "Copied", "Bitcoin address copied to clipboard."),
            )
        )
        btc_row = QHBoxLayout()
        btc_row.addWidget(btc_label, stretch=1)
        btc_row.addWidget(copy_btc_btn)
        support_form.addRow("Bitcoin:", btc_row)

        eth_addr = "0xf75278bd6e2006e6ef4847c9a9293e509ab815c5"
        eth_label = QLabel(eth_addr)
        eth_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        copy_eth_btn = QPushButton("Copy")
        copy_eth_btn.setFixedWidth(60)
        copy_eth_btn.clicked.connect(
            lambda: (
                QApplication.clipboard().setText(eth_addr),
                QMessageBox.information(self, "Copied", "Ethereum address copied to clipboard."),
            )
        )
        eth_row = QHBoxLayout()
        eth_row.addWidget(eth_label, stretch=1)
        eth_row.addWidget(copy_eth_btn)
        support_form.addRow("Ethereum:", eth_row)

        about_layout.addWidget(support_group)

        # ── Pro Registration ──────────────────────────────────────────
        pro_group = QGroupBox("Pro Registration")
        pro_form = QFormLayout(pro_group)
        self._configure_settings_form(pro_form)

        if config.license.registered and config.license.license_key:
            masked = config.license.license_key[:4] + "****" + config.license.license_key[-4:]
            self._pro_status = QLabel(f"✓ Registered — {masked}")
        else:
            self._pro_status = QLabel("Not yet activated.")
        self._pro_status.setWordWrap(True)
        pro_form.addRow("Status:", self._pro_status)

        self._activate_btn = QPushButton("Activate All Features")
        self._activate_btn.clicked.connect(self._activate_free)
        pro_form.addRow(self._activate_btn)

        about_layout.addWidget(pro_group)
        about_layout.addStretch()

        self._tabs.addTab(about_widget, self._t("about"))

        # ── Buttons ────────────────────────────────────────────────────
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        outer_layout.addWidget(buttons)

        self._on_backend_changed(self.sum_backend_combo.currentText())

    def _save(self):
        """Apply settings to config and persist."""
        self.cfg.transcription.model_size = self.model_combo.currentText()
        lang = self.lang_edit.text().strip()
        self.cfg.transcription.language = lang if lang else None
        self.cfg.transcription.live_transcription = self.live_transcription_check.isChecked()
        self.cfg.transcription.auto_translate_english = self.auto_translate_check.isChecked()

        self.cfg.summarizer.backend = self.sum_backend_combo.currentText()
        self.cfg.summarizer.copilot_model = self.copilot_model_combo.currentText()
        self.cfg.summarizer.local_model_key = self.local_model_combo.currentData()
        self.cfg.summarizer.local_context_window = self.local_context_spin.value()
        self.cfg.summarizer.system_prompt = self.prompt_edit.toPlainText().strip()
        self.cfg.summarizer.ollama_url = self.ollama_url_edit.text().strip()
        self.cfg.summarizer.ollama_model = self.ollama_model_combo.currentText().strip()
        self.cfg.summarizer.openai_api_key = self.openai_key_edit.text().strip()

        self.cfg.audio.capture_system_audio = self.capture_sys.isChecked()

        if self.theme_combo.isEnabled():
            self.cfg.ui.theme = self.theme_combo.currentText()
        self.cfg.ui.always_on_top = self.on_top_check.isChecked()
        self.cfg.ui.opacity = self.opacity_slider.value() / 100.0

        self.cfg.save()
        self.accept()

    @staticmethod
    def _configure_settings_form(form: QFormLayout):
        """Keep settings labels and controls readable at narrow dialog widths."""
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(8)

    def _on_backend_changed(self, backend: str):
        """Show/hide relevant fields based on backend selection."""
        is_copilot = backend == "copilot"
        is_local = backend == "local"
        is_ollama_like = backend == "ollama-like"
        is_openai = backend == "openai"
        self.copilot_group.setVisible(is_copilot)
        self.copilot_model_combo.setEnabled(is_copilot)
        self.copilot_auth_label.setEnabled(is_copilot)
        self.copilot_login_btn.setEnabled(is_copilot)
        self.embedded_group.setVisible(is_local)
        self.local_model_combo.setEnabled(is_local and not self._downloading_local_model)
        self.local_context_spin.setEnabled(is_local)
        self.local_model_status.setEnabled(is_local)
        self.local_download_btn.setEnabled(is_local and not self._downloading_local_model)
        self.local_server_group.setVisible(is_ollama_like)
        self.openai_group.setVisible(is_openai)
        self.openai_key_edit.setEnabled(is_openai)
        self._refresh_local_model_status()

    def _launch_copilot_login(self):
        """Start the Copilot CLI login flow from the settings dialog."""
        ok, message = Summarizer.launch_copilot_login()
        if ok:
            QMessageBox.information(
                self,
                "Copilot Login",
                message + "\n\nFinish login there, then come back and try summarizing again.",
            )
        else:
            QMessageBox.warning(self, "Copilot Login", message)

    def _refresh_local_model_status(self):
        """Refresh the embedded model availability label and button text."""
        model_key = self.local_model_combo.currentData()
        if not model_key:
            self.local_model_status.setText("Select a local model.")
            self.local_download_btn.setText("Download")
            return

        downloaded = Summarizer.is_local_model_downloaded(model_key)
        runtime_ready = Summarizer.has_local_runtime()
        if self._downloading_local_model:
            self.local_model_status.setText("Downloading model into ~/.livescriber/models…")
            self.local_download_btn.setText("Downloading…")
            return

        if downloaded and runtime_ready:
            self.local_model_status.setText("Model downloaded and ready for local summarization.")
            self.local_download_btn.setText("Re-download")
        elif downloaded:
            self.local_model_status.setText(
                "Model downloaded, but llama-cpp-python is not installed in this environment yet."
            )
            self.local_download_btn.setText("Re-download")
        else:
            self.local_model_status.setText("Model not downloaded yet.")
            self.local_download_btn.setText("Download")

    def _download_local_model(self):
        """Download the selected embedded local summarizer model in a background thread."""
        model_key = self.local_model_combo.currentData()
        if not model_key:
            QMessageBox.warning(self, "Local Model", "Choose a local model first.")
            return

        self._downloading_local_model = True
        self._on_backend_changed(self.sum_backend_combo.currentText())

        import threading

        def _worker():
            try:
                Summarizer.download_local_model(model_key)
                self._sig_local_model_downloaded.emit(model_key)
            except Exception as exc:
                self._sig_local_model_download_failed.emit(str(exc))

        threading.Thread(target=_worker, daemon=True).start()

    @pyqtSlot(str)
    def _on_local_model_downloaded(self, model_key: str):
        """Handle successful embedded local model download."""
        self._downloading_local_model = False
        self._on_backend_changed(self.sum_backend_combo.currentText())
        self._refresh_local_model_status()
        QMessageBox.information(self, "Local Model", "Local model download complete.")

    @pyqtSlot(str)
    def _on_local_model_download_failed(self, error: str):
        """Handle embedded local model download failure."""
        self._downloading_local_model = False
        self._on_backend_changed(self.sum_backend_combo.currentText())
        self._refresh_local_model_status()
        QMessageBox.warning(self, "Local Model", f"Download failed:\n{error}")

    def _fetch_models(self):
        """Fetch available models from the local server."""
        import requests
        url = self.ollama_url_edit.text().strip()
        if not url:
            return

        self.fetch_btn.setText("…")
        self.fetch_btn.setEnabled(False)
        models = []

        try:
            # Try OpenAI-compatible /v1/models (LM Studio, vLLM, etc.)
            resp = requests.get(f"{url}/v1/models", timeout=5)
            if resp.status_code == 200:
                for m in resp.json().get("data", []):
                    models.append(m.get("id", ""))
        except Exception:
            pass

        if not models:
            try:
                # Try Ollama API /api/tags
                resp = requests.get(f"{url}/api/tags", timeout=5)
                if resp.status_code == 200:
                    for m in resp.json().get("models", []):
                        models.append(m.get("name", ""))
            except Exception:
                pass

        self.fetch_btn.setText("Fetch")
        self.fetch_btn.setEnabled(True)

        if models:
            current = self.ollama_model_combo.currentText()
            self.ollama_model_combo.clear()
            self.ollama_model_combo.addItems(models)
            if current in models:
                self.ollama_model_combo.setCurrentText(current)
            else:
                self.ollama_model_combo.setCurrentIndex(0)
        else:
            QMessageBox.warning(self, "Fetch Models", f"Could not connect to {url}")

    # ── About tab helpers ──────────────────────────────────────────────────

    def _check_for_updates(self):
        """Check GitHub releases for a newer version in a background thread."""
        self._check_update_btn.setEnabled(False)
        self._check_update_btn.setText("Checking…")
        self._update_status.setText("Checking for updates…")

        import threading

        def _worker():
            try:
                import requests
                resp = requests.get(
                    "https://api.github.com/repos/appatalks/LiveScriber/releases/latest",
                    timeout=10,
                    headers={"Accept": "application/vnd.github.v3+json"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    latest = data.get("tag_name", "").lstrip("v")
                    import livescriber
                    current = livescriber.__version__
                    if latest and latest != current:
                        self._sig_update_result.emit(
                            f"New version available: {latest} (you have {current})\n"
                            f"Download: {data.get('html_url', '')}"
                        )
                    else:
                        self._sig_update_result.emit(f"You are up to date (v{current}).")
                elif resp.status_code == 404:
                    self._sig_update_result.emit("No releases found yet.")
                else:
                    self._sig_update_result.emit(f"GitHub returned status {resp.status_code}.")
            except Exception as exc:
                self._sig_update_result.emit(f"Update check failed: {exc}")

        threading.Thread(target=_worker, daemon=True).start()

    @pyqtSlot(str)
    def _on_update_result(self, message: str):
        """Handle the result of the update check."""
        self._update_status.setText(message)
        self._check_update_btn.setEnabled(True)
        self._check_update_btn.setText("Check for Updates")

    @staticmethod
    def _generate_license_key() -> str:
        """Generate a valid LiveScriber license key."""
        import hashlib
        import secrets
        import string
        charset = string.ascii_uppercase + string.digits
        groups = ["".join(secrets.choice(charset) for _ in range(4)) for _ in range(3)]
        payload = "-".join(groups)
        checksum = hashlib.sha256(
            ("LiveScriberPro:" + payload).encode()
        ).hexdigest()[:4].upper()
        return f"{payload}-{checksum}"

    def _activate_free(self):
        """Generate a key, activate it, and unlock all features in one click."""
        if self.cfg.license.registered and self.cfg.license.license_key:
            QMessageBox.information(self, "Already Activated", "All features are already activated.")
            return

        key = self._generate_license_key()
        self.cfg.license.license_key = key
        self.cfg.license.registered = True
        self.cfg.save()

        masked = key[:4] + "****" + key[-4:]
        self._pro_status.setText(f"✓ Activated — {masked}")
        self._activate_btn.setText("Activated ✓")
        self._activate_btn.setEnabled(False)
        self._refresh_theme_lock()
        QMessageBox.information(
            self, "Activated",
            "All features are now unlocked!\n\n"
            "If LiveScriber is useful to you, please consider "
            "supporting development with a donation. Thank you!",
        )

    def _refresh_theme_lock(self):
        """Enable or disable theme selection based on activation status."""
        is_active = self.cfg.license.registered and bool(self.cfg.license.license_key)
        self.theme_combo.setEnabled(is_active)
        if is_active:
            self._theme_pro_label.setText("")
        else:
            self._theme_pro_label.setText("<i>Activate to unlock</i>")
            self.theme_combo.setCurrentText("dark")


class LiveScriberWindow(QWidget):
    """Main floating window."""

    # Signals for thread-safe UI updates
    _sig_segment = pyqtSignal(str)
    _sig_transcription_done = pyqtSignal(str)
    _sig_transcription_error = pyqtSignal(str)
    _sig_summary_done = pyqtSignal(str)
    _sig_summary_error = pyqtSignal(str)

    def __init__(self, config: AppConfig):
        super().__init__()
        self.cfg = config
        self._drag_pos: QPoint | None = None
        self._ui_lang = config.ui.ui_language

        icon_path = _resolve_app_icon_path()
        if icon_path:
            self.setWindowIcon(QIcon(str(icon_path)))

        # ── Backend components ─────────────────────────────────────────
        self.recorder = Recorder(config.audio)
        self.transcriber = Transcriber(config.transcription)
        self.summarizer = Summarizer(config.summarizer)

        self._transcript_text = ""

        # ── Live transcription state ───────────────────────────────────
        self._live_thread: threading.Thread | None = None
        self._live_stop = threading.Event()
        self._live_frame_cursor: int = 0

        # ── Session history (in-memory) ────────────────────────────────
        # Each entry: {"audio": np.ndarray, "transcript": str, "summary": str,
        #              "timestamp": str, "duration": float}
        self._history: list[dict] = []
        self._history_idx: int = -1     # -1 = current/new session
        self._playback_thread = None

        # ── Window setup ───────────────────────────────────────────────
        flags = Qt.WindowType.FramelessWindowHint
        if config.ui.always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setWindowOpacity(config.ui.opacity)
        self.setMinimumWidth(320)
        self.setMinimumHeight(300)
        self.resize(config.ui.window_width, config.ui.window_height)

        # Edge resize state
        self._resize_edge: str | None = None
        self._resize_start_pos: QPoint | None = None
        self._resize_start_geo: QRect | None = None
        self._edge_margin = 8  # pixels from edge to trigger resize
        self.setMouseTracking(True)
        self.installEventFilter(self)

        # ── Build UI ───────────────────────────────────────────────────
        self._build_ui()
        self._connect_signals()

        # Install event filter on all child widgets for edge resize
        self._install_filter_recursive(self)

        # ── Timer for recording duration ───────────────────────────────
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._update_timer)

        # Apply theme
        self.setStyleSheet(get_theme(config.ui.theme))

    def _t(self, key: str) -> str:
        """Shorthand for translating a UI string key."""
        return t(key, self._ui_lang)

    # ── UI Construction ────────────────────────────────────────────────────

    def _install_filter_recursive(self, widget):
        """Install the event filter on all child widgets for edge resize."""
        for child in widget.findChildren(QWidget):
            child.setMouseTracking(True)
            child.installEventFilter(self)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)  # resize grip margin
        root.setSpacing(0)

        # ── Custom title bar ───────────────────────────────────────────
        title_bar = QWidget()
        title_bar.setObjectName("titleBar")
        title_bar.setFixedHeight(44)
        tb_layout = QHBoxLayout(title_bar)
        tb_layout.setContentsMargins(14, 0, 4, 0)

        title_label = QLabel("Live<span style='color: #f38ba8;'>Scriber</span>")
        title_label.setObjectName("titleLabel")
        title_label.setTextFormat(Qt.TextFormat.RichText)
        tb_layout.addWidget(title_label)

        tb_layout.addStretch()

        btn_settings = QPushButton("⚙")
        btn_settings.setObjectName("btnMinimize")
        btn_settings.setFixedSize(32, 32)
        btn_settings.setToolTip("Settings")
        btn_settings.clicked.connect(self._open_settings)
        tb_layout.addWidget(btn_settings)

        self.btn_lang = QPushButton("🌐")
        self.btn_lang.setObjectName("btnLang")
        self.btn_lang.setFixedSize(32, 32)
        self.btn_lang.setToolTip(f"Transcription language: {self._get_lang_display(self.cfg.ui.ui_language)}")
        self.btn_lang.clicked.connect(self._show_language_menu)
        tb_layout.addWidget(self.btn_lang)

        btn_minimize = QPushButton("─")
        btn_minimize.setObjectName("btnMinimize")
        btn_minimize.setFixedSize(32, 32)
        btn_minimize.clicked.connect(self.showMinimized)
        tb_layout.addWidget(btn_minimize)

        btn_close = QPushButton("✕")
        btn_close.setObjectName("btnClose")
        btn_close.setFixedSize(32, 32)
        btn_close.clicked.connect(self.close)
        tb_layout.addWidget(btn_close)

        root.addWidget(title_bar)

        # ── Content area ───────────────────────────────────────────────
        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(16, 16, 16, 12)
        cl.setSpacing(12)

        # Record button + timer
        rec_layout = QVBoxLayout()
        rec_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rec_layout.setSpacing(8)

        self.record_btn = RecordButton()
        self.record_btn.clicked.connect(self._toggle_recording)
        rec_layout.addWidget(self.record_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self.timer_label = QLabel("00:00")
        self.timer_label.setObjectName("timerLabel")
        self.timer_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rec_layout.addWidget(self.timer_label)

        cl.addLayout(rec_layout)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        cl.addWidget(sep)

        # History navigation
        hist_row = QHBoxLayout()
        hist_row.setSpacing(4)

        self.btn_hist_prev = QPushButton("◀")
        self.btn_hist_prev.setObjectName("secondaryBtn")
        self.btn_hist_prev.setFixedWidth(32)
        self.btn_hist_prev.setEnabled(False)
        self.btn_hist_prev.setToolTip(self._t("previous_session"))
        self.btn_hist_prev.clicked.connect(self._hist_prev)
        hist_row.addWidget(self.btn_hist_prev)

        self.hist_label = QLabel(self._t("new_session"))
        self.hist_label.setObjectName("statusLabel")
        self.hist_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hist_row.addWidget(self.hist_label)

        self.btn_hist_next = QPushButton("▶")
        self.btn_hist_next.setObjectName("secondaryBtn")
        self.btn_hist_next.setFixedWidth(32)
        self.btn_hist_next.setToolTip(self._t("next_session"))
        self.btn_hist_next.clicked.connect(self._hist_next)
        hist_row.addWidget(self.btn_hist_next)

        cl.addLayout(hist_row)

        # Transcript section
        self.transcript_section = CollapsibleSection(self._t("transcription"), "transcriptArea")
        self.transcript_section.expand()
        cl.addWidget(self.transcript_section)

        # Summary section
        self.summary_section = CollapsibleSection(self._t("summary_notes"), "summaryArea", editable=True)
        self.summary_section.expand()
        cl.addWidget(self.summary_section)

        # Action buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.btn_transcribe = QPushButton(self._t("transcribe"))
        self.btn_transcribe.setObjectName("actionBtn")
        self.btn_transcribe.setEnabled(False)
        self.btn_transcribe.clicked.connect(self._start_transcription)
        btn_row.addWidget(self.btn_transcribe)

        self.btn_summarize = QPushButton(self._t("summarize"))
        self.btn_summarize.setObjectName("actionBtn")
        self.btn_summarize.setEnabled(False)
        self.btn_summarize.clicked.connect(self._start_summarization)
        btn_row.addWidget(self.btn_summarize)

        cl.addLayout(btn_row)

        # Secondary buttons row
        btn_row2 = QHBoxLayout()
        btn_row2.setSpacing(8)

        self.btn_import = QPushButton(self._t("import_audio"))
        self.btn_import.setObjectName("secondaryBtn")
        self.btn_import.clicked.connect(self._import_audio)
        btn_row2.addWidget(self.btn_import)

        self.btn_copy = QPushButton(self._t("copy_all"))
        self.btn_copy.setObjectName("secondaryBtn")
        self.btn_copy.clicked.connect(self._copy_all)
        btn_row2.addWidget(self.btn_copy)

        self.btn_save = QPushButton(self._t("save_md"))
        self.btn_save.setObjectName("secondaryBtn")
        self.btn_save.clicked.connect(self._save_markdown)
        btn_row2.addWidget(self.btn_save)

        cl.addLayout(btn_row2)

        # Third row: audio actions
        btn_row3 = QHBoxLayout()
        btn_row3.setSpacing(8)

        self.btn_play = QPushButton(self._t("play"))
        self.btn_play.setObjectName("secondaryBtn")
        self.btn_play.setEnabled(False)
        self.btn_play.clicked.connect(self._play_audio)
        btn_row3.addWidget(self.btn_play)

        self.btn_save_wav = QPushButton(self._t("save_wav"))
        self.btn_save_wav.setObjectName("secondaryBtn")
        self.btn_save_wav.setEnabled(False)
        self.btn_save_wav.clicked.connect(self._save_wav)
        btn_row3.addWidget(self.btn_save_wav)

        cl.addLayout(btn_row3)

        cl.addStretch()

        # Status
        backend_label = self.cfg.summarizer.backend.capitalize()
        self.status_label = QLabel(f"{self._t('ready')} — {backend_label}")
        self.status_label.setObjectName("statusLabel")
        cl.addWidget(self.status_label)

        root.addWidget(content)

    def _connect_signals(self):
        self._sig_segment.connect(self._on_segment)
        self._sig_transcription_done.connect(self._on_transcription_done)
        self._sig_transcription_error.connect(self._on_transcription_error)
        self._sig_summary_done.connect(self._on_summary_done)
        self._sig_summary_error.connect(self._on_summary_error)

    # ── Window dragging & edge resizing ─────────────────────────────────

    def _edge_at(self, global_pos) -> str | None:
        """Return which edge/corner the global mouse pos is near, or None."""
        m = self._edge_margin
        geo = self.geometry()
        x, y = global_pos.x(), global_pos.y()

        on_left = x < geo.left() + m
        on_right = x > geo.right() - m
        on_top = y < geo.top() + m
        on_bottom = y > geo.bottom() - m

        if on_top and on_left:     return "tl"
        if on_top and on_right:    return "tr"
        if on_bottom and on_left:  return "bl"
        if on_bottom and on_right: return "br"
        if on_left:   return "l"
        if on_right:  return "r"
        if on_top:    return "t"
        if on_bottom: return "b"
        return None

    _CURSOR_MAP = {
        "l": Qt.CursorShape.SizeHorCursor,
        "r": Qt.CursorShape.SizeHorCursor,
        "t": Qt.CursorShape.SizeVerCursor,
        "b": Qt.CursorShape.SizeVerCursor,
        "tl": Qt.CursorShape.SizeFDiagCursor,
        "br": Qt.CursorShape.SizeFDiagCursor,
        "tr": Qt.CursorShape.SizeBDiagCursor,
        "bl": Qt.CursorShape.SizeBDiagCursor,
    }

    def eventFilter(self, obj, event):
        """Intercept mouse events from child widgets for edge resizing."""
        if event.type() == QEvent.Type.MouseMove:
            global_pos = event.globalPosition().toPoint()

            # Active resize drag
            if self._resize_edge and event.buttons() & Qt.MouseButton.LeftButton:
                self._do_resize(global_pos)
                return True

            # Hover cursor update
            edge = self._edge_at(global_pos)
            if edge:
                self.setCursor(self._CURSOR_MAP[edge])
            else:
                self.unsetCursor()

        elif event.type() == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                global_pos = event.globalPosition().toPoint()
                edge = self._edge_at(global_pos)
                if edge:
                    self._resize_edge = edge
                    self._resize_start_pos = global_pos
                    self._resize_start_geo = QRect(self.geometry())
                    return True

        elif event.type() == QEvent.Type.MouseButtonRelease:
            if self._resize_edge:
                self._resize_edge = None
                return True

        return super().eventFilter(obj, event)

    def _do_resize(self, global_pos: QPoint):
        """Apply resize based on current drag."""
        delta = global_pos - self._resize_start_pos
        geo = QRect(self._resize_start_geo)
        e = self._resize_edge

        if "r" in e: geo.setRight(self._resize_start_geo.right() + delta.x())
        if "b" in e: geo.setBottom(self._resize_start_geo.bottom() + delta.y())
        if "l" in e: geo.setLeft(self._resize_start_geo.left() + delta.x())
        if "t" in e: geo.setTop(self._resize_start_geo.top() + delta.y())

        if geo.width() < self.minimumWidth():
            if "l" in e: geo.setLeft(geo.right() - self.minimumWidth())
            else: geo.setRight(geo.left() + self.minimumWidth())
        if geo.height() < self.minimumHeight():
            if "t" in e: geo.setTop(geo.bottom() - self.minimumHeight())
            else: geo.setBottom(geo.top() + self.minimumHeight())

        self.setGeometry(geo)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        global_pos = event.globalPosition().toPoint()
        edge = self._edge_at(global_pos)
        if edge:
            self._resize_edge = edge
            self._resize_start_pos = global_pos
            self._resize_start_geo = QRect(self.geometry())
        elif event.position().y() < 44:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        global_pos = event.globalPosition().toPoint()
        if self._resize_edge and event.buttons() & Qt.MouseButton.LeftButton:
            self._do_resize(global_pos)
            return
        if self._drag_pos and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            return
        edge = self._edge_at(global_pos)
        if edge:
            self.setCursor(self._CURSOR_MAP[edge])
        else:
            self.unsetCursor()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        self._resize_edge = None

    # ── Recording ──────────────────────────────────────────────────────────

    @pyqtSlot()
    def _toggle_recording(self):
        # Debounce — ignore rapid clicks (< 500ms apart)
        import time
        now = time.monotonic()
        if hasattr(self, "_last_toggle_time") and now - self._last_toggle_time < 0.5:
            return
        self._last_toggle_time = now

        if self.recorder.is_recording:
            self._stop_recording()
        else:
            # Don't auto-start a new recording right after stopping
            # (user must have audio or be on a fresh session)
            self._start_recording()

    def _start_recording(self):
        try:
            self.recorder.start()
            self.record_btn.set_recording(True)
            self._timer.start()
            self._transcript_text = ""
            if self.cfg.audio.capture_system_audio and not self.recorder.system_audio_active:
                self.status_label.setText("Recording… (microphone only; macOS system audio unavailable)")
            else:
                self.status_label.setText("Recording…")
            self.btn_transcribe.setEnabled(False)
            self.btn_summarize.setEnabled(False)
            self.btn_play.setEnabled(False)
            self.btn_save_wav.setEnabled(False)

            # Start live transcription if enabled (not supported on Windows
            # where transcription is isolated in a helper subprocess)
            if self.cfg.transcription.live_transcription:
                import platform
                if platform.system() == "Windows":
                    self.status_label.setText(
                        "Recording… (live transcription not available on Windows)"
                    )
                else:
                    self._start_live_transcription()
        except Exception as exc:
            self.status_label.setText(f"Mic error: {exc}")

    def _stop_recording(self):
        try:
            # Stop live transcription first
            self._stop_live_transcription()

            self.recorder.stop()
            self.record_btn.set_recording(False)
            self._timer.stop()
            duration = int(self.recorder.duration_seconds)
            mins, secs = divmod(duration, 60)

            # Auto-save this recording as a new history session
            import datetime
            audio = self.recorder.get_audio().copy()
            entry = {
                "audio": audio,
                "transcript": self._transcript_text,
                "summary": "",
                "timestamp": datetime.datetime.now().strftime("%H:%M"),
                "duration": self.recorder.duration_seconds,
            }
            self._history.append(entry)
            self._history_idx = len(self._history) - 1

            # Preserve live transcript if we have one, otherwise clear
            if not self._transcript_text:
                self.transcript_section.clear()
            self.summary_section.clear()

            has_audio = self.recorder.has_audio
            has_live = bool(self._transcript_text)
            if has_audio:
                status = f"Recorded {mins}:{secs:02d} — session {self._history_idx + 1}"
                if has_live:
                    status += " (live transcript ready)"
            else:
                status = "No usable audio captured — check your microphone and system-audio device"
            self.status_label.setText(status)
            self.btn_transcribe.setEnabled(has_audio)
            self.btn_summarize.setEnabled(has_live)
            self.btn_play.setEnabled(has_audio)
            self.btn_save_wav.setEnabled(has_audio)
            self._hist_update_nav()
        except Exception as exc:
            self.status_label.setText(f"Stop error: {exc}")

    @pyqtSlot()
    def _update_timer(self):
        secs = int(self.recorder.duration_seconds)
        mins, secs = divmod(secs, 60)
        self.timer_label.setText(f"{mins:02d}:{secs:02d}")

    # ── Live transcription ─────────────────────────────────────────────────

    def _start_live_transcription(self):
        """Begin streaming transcription on a background thread while recording."""
        self._live_stop.clear()
        self._live_frame_cursor = 0
        self._transcript_text = ""
        self.transcript_section.clear()
        self.transcript_section.expand()
        self.status_label.setText("Recording… (loading transcription model)")

        def _live_worker():
            import numpy as np

            # Pre-load the Whisper model before entering the loop
            try:
                self.transcriber._ensure_local_model()
                self._sig_segment.emit("[Live transcription active]")
            except Exception as exc:
                self._sig_segment.emit(f"[Live transcription error: {exc}]")
                return

            interval = 8  # seconds between transcription passes
            overlap_seconds = 3  # overlap with previous chunk for continuity
            sample_rate = self.recorder._mic_rate
            overlap_frames = int(overlap_seconds * sample_rate)
            prev_tail: np.ndarray | None = None

            while not self._live_stop.wait(timeout=interval):
                # Grab new mic frames since last cursor
                with self.recorder._lock:
                    frames = list(self.recorder._mic_frames[self._live_frame_cursor:])
                    self._live_frame_cursor = len(self.recorder._mic_frames)

                if not frames:
                    continue

                chunk = np.concatenate(frames)
                if chunk.ndim > 1:
                    chunk = chunk[:, 0]

                # Prepend overlap from previous chunk for continuity
                if prev_tail is not None:
                    chunk = np.concatenate([prev_tail, chunk])

                # Save tail for next overlap
                if chunk.size > overlap_frames:
                    prev_tail = chunk[-overlap_frames:].copy()
                else:
                    prev_tail = chunk.copy()

                # Resample to 16 kHz if needed
                if sample_rate != 16000:
                    target_len = int(len(chunk) * 16000 / sample_rate)
                    indices = np.linspace(0, len(chunk) - 1, target_len)
                    chunk = np.interp(indices, np.arange(len(chunk)), chunk).astype(np.float32)

                text = self.transcriber.transcribe_live_chunk(chunk, 16000)
                if text:
                    self._sig_segment.emit(text)
                    self._transcript_text += ("\n" if self._transcript_text else "") + text

        self._live_thread = threading.Thread(target=_live_worker, daemon=True)
        self._live_thread.start()

    def _stop_live_transcription(self):
        """Signal the live transcription thread to stop and wait for it."""
        if self._live_thread is not None:
            self._live_stop.set()
            self._live_thread.join(timeout=5)
            self._live_thread = None

    # ── Transcription ──────────────────────────────────────────────────────

    @pyqtSlot()
    def _start_transcription(self):
        # Get audio from current history entry
        audio = self._get_current_audio()
        if audio is None or audio.size == 0:
            self.status_label.setText("No recording to transcribe")
            return

        self.btn_transcribe.setEnabled(False)
        self.transcript_section.clear()
        self.transcript_section.expand()
        self.status_label.setText("Transcribing… (this may take a moment)")

        self.transcriber.transcribe_array_async(
            audio,
            sample_rate=self.recorder.cfg.sample_rate,
            on_segment=lambda t: self._sig_segment.emit(t),
            on_complete=lambda t: self._sig_transcription_done.emit(t),
            on_error=lambda e: self._sig_transcription_error.emit(str(e)),
        )

    def _transcribe_imported(self, path: Path):
        self.btn_transcribe.setEnabled(False)
        self.transcript_section.clear()
        self.transcript_section.expand()
        self.status_label.setText("Transcribing imported audio…")

        self.transcriber.transcribe_file_async(
            path,
            on_segment=lambda t: self._sig_segment.emit(t),
            on_complete=lambda t: self._sig_transcription_done.emit(t),
            on_error=lambda e: self._sig_transcription_error.emit(str(e)),
        )

    @pyqtSlot(str)
    def _on_segment(self, text: str):
        self.transcript_section.append_text(text)

    @pyqtSlot(str)
    def _on_transcription_done(self, text: str):
        self._transcript_text = text
        self.transcript_section.set_text(text)
        if text:
            self.transcript_section.expand()
        self.btn_transcribe.setEnabled(True)
        self.btn_summarize.setEnabled(True)
        self.status_label.setText("Transcription complete")
        self._hist_save_current()

    @pyqtSlot(str)
    def _on_transcription_error(self, error: str):
        self.btn_transcribe.setEnabled(True)
        self.status_label.setText(f"Transcription error: {error}")

    # ── Summarization ──────────────────────────────────────────────────────

    @pyqtSlot()
    def _start_summarization(self):
        if not self._transcript_text:
            self.status_label.setText(self._t("transcribe_first"))
            return

        self.btn_summarize.setEnabled(False)
        self.summary_section.clear()
        self.summary_section.expand()
        self.status_label.setText(self._t("generating_summary"))

        # Always use the 🌐 selected language for summarization — that's the
        # user's explicit choice for what language they want the output in
        lang = self.cfg.ui.ui_language

        self.summarizer.summarize_async(
            self._transcript_text,
            on_complete=lambda s: self._sig_summary_done.emit(s),
            on_error=lambda e: self._sig_summary_error.emit(str(e)),
            detected_language=lang,
            auto_translate_english=self.cfg.transcription.auto_translate_english,
        )

    @pyqtSlot(str)
    def _on_summary_done(self, summary: str):
        self.summary_section.set_text(summary)
        if summary:
            self.summary_section.expand()
        self.btn_summarize.setEnabled(True)
        if summary.startswith("[") and "error" in summary.lower():
            self.status_label.setText("Summary error")
        else:
            self.status_label.setText("Summary ready")
        # Auto-save current session to history
        self._hist_save_current()

    @pyqtSlot(str)
    def _on_summary_error(self, error: str):
        self.btn_summarize.setEnabled(True)
        self.status_label.setText(f"Summary error: {error}")

    # ── Session history ────────────────────────────────────────────────────

    def _get_current_audio(self):
        """Get audio from the current history entry, or from the recorder."""
        import numpy as np
        if 0 <= self._history_idx < len(self._history):
            audio = self._history[self._history_idx].get("audio")
            if audio is not None and audio.size > 0:
                return audio
        # Fall back to recorder's current audio
        return self.recorder.get_audio()

    def _hist_save_current(self):
        """Update current history entry with transcript + summary."""
        if self._history_idx < 0 or self._history_idx >= len(self._history):
            return

        transcript = self._transcript_text
        summary = self.summary_section.content.toPlainText()

        entry = self._history[self._history_idx]
        entry["transcript"] = transcript
        entry["summary"] = summary

    @pyqtSlot()
    def _hist_prev(self):
        # Save edits before navigating
        self._hist_save_current()
        if self._history_idx > 0:
            self._history_idx -= 1
            self._hist_show(self._history_idx)
        elif self._history_idx >= len(self._history) and len(self._history) > 0:
            self._history_idx = len(self._history) - 1
            self._hist_show(self._history_idx)

    @pyqtSlot()
    def _hist_next(self):
        self._hist_save_current()
        if self._history_idx < len(self._history) - 1:
            self._history_idx += 1
            self._hist_show(self._history_idx)
        else:
            self._hist_new_session()

    def _hist_show(self, idx: int):
        """Display a history entry."""
        entry = self._history[idx]
        self._transcript_text = entry["transcript"]
        self.transcript_section.set_text(entry["transcript"])
        if entry["transcript"]:
            self.transcript_section.expand()
        self.summary_section.set_text(entry["summary"])
        if entry["summary"]:
            self.summary_section.expand()

        has_audio = entry.get("audio") is not None and entry["audio"].size > 0
        has_transcript = bool(entry["transcript"])
        self.btn_transcribe.setEnabled(has_audio)
        self.btn_summarize.setEnabled(has_transcript)
        self.btn_play.setEnabled(has_audio)
        self.btn_save_wav.setEnabled(has_audio)

        duration = int(entry.get("duration", 0))
        mins, secs = divmod(duration, 60)
        self.timer_label.setText(f"{mins:02d}:{secs:02d}")
        self._hist_update_nav()

    def _hist_update_nav(self):
        """Update history nav buttons and label."""
        total = len(self._history)
        idx = self._history_idx

        if total == 0 or idx >= total:
            label = f"{self._t('new_session')}  ({total})" if total > 0 else self._t("new_session")
            self.hist_label.setText(label)
            self.btn_hist_prev.setEnabled(total > 0)
            self.btn_hist_next.setEnabled(False)
            self.btn_hist_next.setToolTip("New session")
            return

        entry = self._history[idx]
        has_transcript = "✓" if entry.get("transcript") else "●"
        self.hist_label.setText(f"{idx + 1}/{total} {has_transcript}  {entry['timestamp']}")
        self.btn_hist_prev.setEnabled(idx > 0)
        self.btn_hist_next.setEnabled(True)
        if idx == total - 1:
            self.btn_hist_next.setToolTip("New session")
        else:
            self.btn_hist_next.setToolTip("Next session")

    def _hist_new_session(self):
        """Go to a fresh workspace (doesn't create a history entry until recording)."""
        self._hist_save_current()
        self._transcript_text = ""
        self.transcript_section.clear()
        self.summary_section.clear()
        self._history_idx = len(self._history)
        self.timer_label.setText("00:00")
        self.btn_transcribe.setEnabled(False)
        self.btn_summarize.setEnabled(False)
        self.btn_play.setEnabled(False)
        self.btn_save_wav.setEnabled(False)
        self._hist_update_nav()

    # ── Audio playback & export ────────────────────────────────────────────

    @pyqtSlot()
    def _play_audio(self):
        """Play back audio from the current session."""
        import sounddevice as sd
        audio = self._get_current_audio()
        if audio is None or audio.size == 0:
            self.status_label.setText("No audio to play")
            return

        # Stop any existing playback
        sd.stop()
        self.status_label.setText("Playing…")
        self.btn_play.setText("■ Stop")
        self.btn_play.clicked.disconnect()
        self.btn_play.clicked.connect(self._stop_playback)

        import threading
        def _play():
            try:
                sd.play(audio, samplerate=self.recorder.cfg.sample_rate)
                sd.wait()
            except sd.PortAudioError:
                try:
                    sd.stop()
                    sd.play(audio, samplerate=self.recorder.cfg.sample_rate)
                    sd.wait()
                except sd.PortAudioError as exc:
                    from PyQt6.QtCore import Q_ARG, QMetaObject, Qt as QtNS
                    QMetaObject.invokeMethod(
                        self,
                        "_on_playback_error",
                        QtNS.ConnectionType.QueuedConnection,
                        Q_ARG(str, str(exc)),
                    )
                    return
            # Reset button on main thread
            from PyQt6.QtCore import QMetaObject, Qt as QtNS
            QMetaObject.invokeMethod(
                self, "_on_playback_done", QtNS.ConnectionType.QueuedConnection
            )

        self._playback_thread = threading.Thread(target=_play, daemon=True)
        self._playback_thread.start()

    @pyqtSlot()
    def _stop_playback(self):
        import sounddevice as sd
        sd.stop()
        self._on_playback_done()

    @pyqtSlot()
    def _on_playback_done(self):
        self.btn_play.setText("▶ Play")
        self.btn_play.clicked.disconnect()
        self.btn_play.clicked.connect(self._play_audio)
        self.status_label.setText("Ready")

    @pyqtSlot(str)
    def _on_playback_error(self, error: str):
        self._on_playback_done()
        self.status_label.setText(f"Playback unavailable: {error}")

    @pyqtSlot()
    def _save_wav(self):
        """Save audio from current session to a WAV file."""
        import io, wave, datetime
        import numpy as np

        audio = self._get_current_audio()
        if audio is None or audio.size == 0:
            self.status_label.setText("No audio to save")
            return

        from livescriber.config import APP_DIR
        recordings_dir = APP_DIR / "recordings"
        recordings_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = f"livescriber_{ts}.wav"

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Audio",
            str(recordings_dir / default_name),
            "WAV Files (*.wav);;All Files (*)",
        )
        if path:
            audio_int16 = np.clip(audio * 32767, -32768, 32767).astype(np.int16)
            with wave.open(path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(self.recorder.cfg.sample_rate)
                wf.writeframes(audio_int16.tobytes())
            self.status_label.setText(f"Saved: {Path(path).name}")

    # ── Utility actions ────────────────────────────────────────────────────

    @pyqtSlot()
    def _import_audio(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Audio File",
            str(Path.home()),
            "Audio Files (*.wav *.mp3 *.m4a *.ogg *.flac *.webm);;All Files (*)",
        )
        if path:
            self.status_label.setText(f"Imported: {Path(path).name}")
            self._transcribe_imported(Path(path))

    @pyqtSlot()
    def _copy_all(self):
        clipboard = QApplication.clipboard()
        parts = []
        if self._transcript_text:
            parts.append("## Transcript\n" + self._transcript_text)
        summary = self.summary_section.content.toPlainText()
        if summary:
            parts.append("## Summary\n" + summary)

        if parts:
            clipboard.setText("\n\n".join(parts))
            self.status_label.setText("Copied to clipboard")
        else:
            self.status_label.setText("Nothing to copy")

    @pyqtSlot()
    def _save_markdown(self):
        """Save the generated summary as a Markdown file."""
        import datetime

        parts: list[str] = []
        ts = datetime.datetime.now()
        parts.append(f"# LiveScriber Notes — {ts.strftime('%B %d, %Y %H:%M')}\n")

        summary = self.summary_section.content.toPlainText()
        if summary:
            parts.append("## Summary\n")
            parts.append(summary + "\n")

        if len(parts) <= 1:
            self.status_label.setText("Nothing to save")
            return

        # Recording info
        duration = int(self.recorder.duration_seconds)
        if duration > 0:
            mins, secs = divmod(duration, 60)
            parts.append("---\n")
            parts.append(f"*Duration: {mins}m {secs}s*\n")

        default_name = f"livescriber_{ts.strftime('%Y%m%d_%H%M%S')}.md"
        from livescriber.config import APP_DIR
        notes_dir = APP_DIR / "notes"
        notes_dir.mkdir(parents=True, exist_ok=True)

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Notes as Markdown",
            str(notes_dir / default_name),
            "Markdown Files (*.md);;All Files (*)",
        )
        if path:
            Path(path).write_text("\n".join(parts), encoding="utf-8")
            self.status_label.setText(f"Saved: {Path(path).name}")

    # ── Language toggle ─────────────────────────────────────────────────────

    _LANG_CYCLE = ["en", "ko", "ja", "uk", "es", "fr", "de", "pt", "ar"]

    _LANG_LABELS = {
        "en": "English", "ko": "한국어", "ja": "日本語", "uk": "Українська",
        "es": "Español", "fr": "Français", "de": "Deutsch", "pt": "Português",
        "ar": "العربية",
    }

    @staticmethod
    def _get_lang_display(code: str) -> str:
        return LiveScriberWindow._LANG_LABELS.get(code, code.upper())

    @pyqtSlot()
    def _show_language_menu(self):
        """Show a dropdown menu to select the transcription language."""
        from PyQt6.QtWidgets import QMenu
        from PyQt6.QtGui import QAction

        menu = QMenu(self)
        menu.setStyleSheet(self.styleSheet())
        current = self.cfg.transcription.language or "en"

        for code in self._LANG_CYCLE:
            label = self._get_lang_display(code)
            prefix = "✓ " if code == current else "   "
            action = QAction(f"{prefix}{label}", menu)
            action.setData(code)
            action.triggered.connect(lambda checked, c=code: self._set_language(c))
            menu.addAction(action)

        menu.exec(self.btn_lang.mapToGlobal(self.btn_lang.rect().bottomLeft()))

    def _set_language(self, code: str):
        """Set both transcription and UI display language from the 🌐 button."""
        self.cfg.transcription.language = None if code == "en" else code
        self.cfg.ui.ui_language = code
        self._ui_lang = code

        # distil-large-v3 only supports English well — auto-upgrade for other languages
        if code != "en" and self.cfg.transcription.model_size.startswith("distil"):
            self.cfg.transcription.model_size = "large-v3"

        # Switch back to distil when returning to English for faster transcription
        if code == "en" and self.cfg.transcription.model_size == "large-v3":
            self.cfg.transcription.model_size = "distil-large-v3"

        self.cfg.save()
        self.transcriber = Transcriber(self.cfg.transcription)

        # Rebuild UI with new language
        self._rebuild_ui()

    # ── Settings ───────────────────────────────────────────────────────────

    @pyqtSlot()
    def _open_settings(self):
        from livescriber.styles import get_theme

        dialog = SettingsDialog(self.cfg, parent=self)
        dialog.setStyleSheet(self.styleSheet())
        if dialog.exec() == QDialog.DialogCode.Accepted:
            # Apply theme change immediately
            self.setStyleSheet(get_theme(self.cfg.ui.theme))
            self.setWindowOpacity(self.cfg.ui.opacity)

            # Reload transcriber with new model if changed
            self.transcriber = Transcriber(self.cfg.transcription)
            self.summarizer = Summarizer(self.cfg.summarizer)

            # Update always-on-top
            flags = Qt.WindowType.FramelessWindowHint
            if self.cfg.ui.always_on_top:
                flags |= Qt.WindowType.WindowStaysOnTopHint
            self.setWindowFlags(flags)
            self.show()  # re-show after setWindowFlags

            backend_label = self.cfg.summarizer.backend.capitalize()
            self.status_label.setText(f"Settings saved — {backend_label} summarizer")

    def _rebuild_ui(self):
        """Update all translatable labels/buttons to the current UI language."""
        display = self._get_lang_display(self._ui_lang)
        self.btn_lang.setToolTip(f"Language: {display}")
        self.btn_transcribe.setText(self._t("transcribe"))
        self.btn_summarize.setText(self._t("summarize"))
        self.btn_import.setText(self._t("import_audio"))
        self.btn_copy.setText(self._t("copy_all"))
        self.btn_save.setText(self._t("save_md"))
        self.btn_save_wav.setText(self._t("save_wav"))
        self.btn_play.setText(self._t("play"))
        self.btn_hist_prev.setToolTip(self._t("previous_session"))
        self.btn_hist_next.setToolTip(self._t("next_session"))
        self.transcript_section.toggle_btn.setText(f"▼  {self._t('transcription')}")
        self.summary_section.toggle_btn.setText(f"▼  {self._t('summary_notes')}")
        self.hist_label.setText(self._t("new_session"))
        self.status_label.setText(f"{self._t('ready')} — {display}")


def run_app(config: AppConfig | None = None):
    """Launch the LiveScriber floating window."""
    if config is None:
        config = AppConfig.load()

    app = QApplication(sys.argv)
    app.setApplicationName("LiveScriber")

    icon_path = _resolve_app_icon_path()
    if icon_path:
        app.setWindowIcon(QIcon(str(icon_path)))

    window = LiveScriberWindow(config)
    window.show()

    sys.exit(app.exec())
