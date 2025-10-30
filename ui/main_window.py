# ui/main_window.py
from __future__ import annotations
from pathlib import Path

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QFileDialog, QSpinBox, QPlainTextEdit, QMessageBox, QLineEdit
)

from core.settings import Settings
from core.monitor_utils import list_monitors, MonitorInfo
from core.device_utils import list_dshow_audio_devices
from core.ffmpeg_recorder import FFmpegRecorder, FFmpegOptions, VideoEncoder

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FFmpeg Screen&Audio Recorder")
        self.setMinimumSize(760, 560)

        self.settings = Settings()
        self.monitors: list[MonitorInfo] = []
        self.audio_items: list[tuple[str, str|None]] = []  # (label, ff_arg)

        self.rec = FFmpegRecorder()
        self.rec.started.connect(lambda: self._append_log("녹화 시작"))
        self.rec.stopped.connect(self._on_stopped)
        self.rec.error.connect(self._on_error)
        self.rec.log.connect(self._append_log)
        self.rec.stateChanged.connect(self._on_state_changed)

        self._build_ui()
        self._load_initial_state()

        # 로그 쓰로틀링
        self._log_buf = []
        self._log_timer = QTimer(self)
        self._log_timer.setInterval(120)
        self._log_timer.timeout.connect(self._flush_log)
        self._log_timer.start()

    def _build_ui(self):
        cw = QWidget(); self.setCentralWidget(cw)
        root = QVBoxLayout(cw)

        # FFmpeg 경로
        row = QHBoxLayout()
        self.edit_ffmpeg = QLineEdit(self.settings.get("ffmpeg_path"))
        b = QPushButton("찾기…"); b.clicked.connect(self._pick_ffmpeg)
        row.addWidget(QLabel("FFmpeg:")); row.addWidget(self.edit_ffmpeg, 1); row.addWidget(b)
        root.addLayout(row)

        # 출력 폴더
        row = QHBoxLayout()
        self.edit_out = QLineEdit(self.settings.get("output_dir"))
        b = QPushButton("폴더…"); b.clicked.connect(self._pick_out_dir)
        row.addWidget(QLabel("출력 폴더:")); row.addWidget(self.edit_out, 1); row.addWidget(b)
        root.addLayout(row)

        # 모니터/오디오/FPS
        row = QHBoxLayout()
        self.combo_monitor = QComboBox(); self.combo_monitor.setMinimumWidth(280)
        self.combo_audio = QComboBox(); self.combo_audio.setMinimumWidth(280)
        self.spin_fps = QSpinBox(); self.spin_fps.setRange(5, 120); self.spin_fps.setValue(int(self.settings.get("video_fps", 30)))
        row.addWidget(QLabel("모니터:")); row.addWidget(self.combo_monitor, 1)
        row.addWidget(QLabel("오디오(dshow):")); row.addWidget(self.combo_audio, 1)
        row.addWidget(QLabel("FPS:")); row.addWidget(self.spin_fps)
        root.addLayout(row)

        # 프리셋 + 인코더 + 오디오 지연
        row = QHBoxLayout()
        self.combo_preset = QComboBox(); self.combo_preset.addItems(["ultrafast","superfast","veryfast","faster","fast","medium"])
        self.combo_preset.setCurrentText(self.settings.get("video_preset", "veryfast"))

        self.combo_encoder = QComboBox()
        self.combo_encoder.addItems([
            "libx264 (software)",
            "NVIDIA (h264_nvenc)",
            "Intel QSV (h264_qsv)",
            "AMD AMF (h264_amf)",
            "HEVC NVENC (hevc_nvenc)",
            "HEVC QSV (hevc_qsv)",
            "HEVC AMF (hevc_amf)",
        ])

        self.spin_audio_delay = QSpinBox()
        self.spin_audio_delay.setRange(-2000, 2000)
        self.spin_audio_delay.setSingleStep(50)
        self.spin_audio_delay.setValue(500)  # 기본 500ms
        row.addWidget(QLabel("x264 preset:")); row.addWidget(self.combo_preset)
        row.addWidget(QLabel("Encoder:")); row.addWidget(self.combo_encoder)
        row.addWidget(QLabel("Audio delay(ms):")); row.addWidget(self.spin_audio_delay)
        root.addLayout(row)

        # 버튼
        row = QHBoxLayout()
        self.btn_refresh = QPushButton("장치 새로고침")
        self.btn_start = QPushButton("녹화 시작")
        self.btn_pause = QPushButton("일시정지")
        self.btn_resume = QPushButton("재개")
        self.btn_stop = QPushButton("정지")
        row.addWidget(self.btn_refresh)
        row.addStretch(1)
        row.addWidget(self.btn_start)
        row.addWidget(self.btn_pause)
        row.addWidget(self.btn_resume)
        row.addWidget(self.btn_stop)
        root.addLayout(row)

        # 로그
        self.log_view = QPlainTextEdit(); self.log_view.setReadOnly(True)
        root.addWidget(self.log_view, 1)

        # 연결
        self.btn_refresh.clicked.connect(self._refresh_devices)
        self.btn_start.clicked.connect(self._start)
        self.btn_pause.clicked.connect(self._pause)
        self.btn_resume.clicked.connect(self._resume)
        self.btn_stop.clicked.connect(self._stop)

        # 초기 버튼 상태
        self._update_buttons("IDLE")

    def _load_initial_state(self):
        self._refresh_monitors()
        self._refresh_audio()
        idx = int(self.settings.get("monitor_index", 0))
        if 0 <= idx < self.combo_monitor.count():
            self.combo_monitor.setCurrentIndex(idx)
        saved_audio = self.settings.get("audio_device")
        if saved_audio and (i := self.combo_audio.findText(saved_audio)) >= 0:
            self.combo_audio.setCurrentIndex(i)

    def _refresh_monitors(self):
        try:
            mons = list_monitors()
        except Exception as e:
            self._on_error(f"모니터 조회 실패: {e}")
            mons = []
        self.monitors = mons
        self.combo_monitor.clear()
        for m in mons:
            self.combo_monitor.addItem(f"#{m.index}  {m.width}x{m.height}  @({m.x},{m.y})")

    def _refresh_audio(self):
        ffmpeg_path = self.edit_ffmpeg.text().strip() or "ffmpeg"
        devs = list_dshow_audio_devices(ffmpeg_path)
        self.combo_audio.clear()
        self.audio_items.clear()

        self.combo_audio.addItem("(무음)")
        self.audio_items.append(("(무음)", None))

        # devs는 문자열 리스트 또는 dict 리스트( display/name/moniker )일 수 있음
        for d in devs:
            if isinstance(d, dict):
                label = d.get("display") or d.get("name") or d.get("moniker") or "Unknown"
                arg = d.get("moniker") or d.get("display") or d.get("name")
            else:
                label = str(d)
                arg = label
            self.combo_audio.addItem(label)
            self.audio_items.append((label, arg))

    def _refresh_devices(self):
        self._refresh_monitors()
        self._refresh_audio()
        self._append_log("장치 목록 갱신")

    # ---- 버튼 핸들러 ----
    def _start(self):
        if not self.monitors:
            self._on_error("모니터 정보가 없습니다.")
            return
        m = self.monitors[self.combo_monitor.currentIndex()]
        ffmpeg_path = self.edit_ffmpeg.text().strip() or "ffmpeg"
        out_dir = Path(self.edit_out.text().strip() or Path.cwd() / "recordings")
        fps = int(self.spin_fps.value())
        preset = self.combo_preset.currentText()
        enc_map = {
            "libx264 (software)": "libx264",
            "NVIDIA (h264_nvenc)": "h264_nvenc",
            "Intel QSV (h264_qsv)": "h264_qsv",
            "AMD AMF (h264_amf)": "h264_amf",
            "HEVC NVENC (hevc_nvenc)": "hevc_nvenc",
            "HEVC QSV (hevc_qsv)": "hevc_qsv",
            "HEVC AMF (hevc_amf)": "hevc_amf",
        }
        encoder: VideoEncoder = enc_map.get(self.combo_encoder.currentText(), "libx264")  # type: ignore

        idx = self.combo_audio.currentIndex()
        _, ff_arg = self.audio_items[idx]
        audio_mode = "none" if ff_arg is None else "dshow"
        audio_dev = ff_arg

        self.settings.set("audio_device", None if audio_mode != "dshow" else audio_dev)

        opt = FFmpegOptions(
            ffmpeg_path=ffmpeg_path,
            output_dir=out_dir,
            fps=fps,
            preset=preset,
            monitor=m,
            audio_mode=audio_mode, audio_device=audio_dev,
            encoder=encoder,
            audio_delay_ms=int(self.spin_audio_delay.value()),
        )
        ok = self.rec.start(opt)
        if ok:
            self._update_buttons("RUNNING")

    def _pause(self):
        if self.rec.pause():
            # 실제 PAUSED 전환은 세그먼트 종료 후 stateChanged 시그널에서 처리
            self._append_log("(일시정지 요청)")

    def _resume(self):
        if self.rec.resume():
            self._append_log("(재개 요청)")

    def _stop(self):
        self.rec.stop()  # stateChanged/로그는 내부에서 처리

    # ---- 상태/로그/오류 ----
    def _update_buttons(self, state: str):
        # IDLE: Start만 활성
        # RUNNING: Pause/Stop 활성
        # CLOSING: 모두 비활성(중복 입력 방지)
        # PAUSED: Resume/Stop 활성
        s = state
        self.btn_start.setEnabled(s in ("IDLE", "PAUSED"))
        self.btn_pause.setEnabled(s == "RUNNING")
        self.btn_resume.setEnabled(s in ("PAUSED",))
        self.btn_stop.setEnabled(s in ("RUNNING", "PAUSED", "CLOSING"))

    def _on_state_changed(self, s: str):
        self._append_log(f"[state] {s}")
        self._update_buttons(s)

    def _append_log(self, msg: str):
        if not msg: return
        self._log_buf.append(msg)

    def _flush_log(self):
        if not self._log_buf: return
        text = "\n".join(self._log_buf); self._log_buf.clear()
        self.log_view.appendPlainText(text)
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_error(self, msg: str):
        self._append_log("[ERROR] " + msg)
        QMessageBox.warning(self, "오류", msg)

    def _on_stopped(self, code: int):
        self._append_log(f"녹화 종료 (exit={code})")
        self._update_buttons("IDLE")

    def _pick_ffmpeg(self):
        path, _ = QFileDialog.getOpenFileName(self, "ffmpeg.exe 선택", "", "ffmpeg (ffmpeg.exe)")
        if path:
            self.edit_ffmpeg.setText(path)

    def _pick_out_dir(self):
        d = QFileDialog.getExistingDirectory(self, "출력 폴더 선택", self.edit_out.text())
        if d:
            self.edit_out.setText(d)
