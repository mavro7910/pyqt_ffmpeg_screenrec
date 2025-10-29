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
from core.ffmpeg_recorder import FFmpegRecorder, FFmpegOptions

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FFmpeg Screen&Audio Recorder")
        self.setMinimumSize(720, 520)

        self.settings = Settings()
        self.monitors: list[MonitorInfo] = []
        self.audio_devices: list[str] = []

        self.rec = FFmpegRecorder()
        self.rec.started.connect(lambda: self._append_log("녹화 시작"))
        self.rec.stopped.connect(self._on_stopped)
        self.rec.error.connect(self._on_error)
        self.rec.log.connect(self._append_log)

        self._build_ui()
        self._load_initial_state()
        
        self.audio_items = []  # [(label:str, ff_arg:str|None)]
        
        self._log_buf = []
        self._log_timer = QTimer(self)
        self._log_timer.setInterval(200)        # 0.2초마다 UI 반영
        self._log_timer.timeout.connect(self._flush_log)
        self._log_timer.start()

    # ---- UI ----
    def _build_ui(self):
        cw = QWidget(); self.setCentralWidget(cw)
        root = QVBoxLayout(cw)

        # FFmpeg 경로
        ffmpeg_row = QHBoxLayout()
        self.ffmpeg_edit = QLineEdit(self.settings.get("ffmpeg_path"))
        btn_ffmpeg = QPushButton("찾기…")
        btn_ffmpeg.clicked.connect(self._pick_ffmpeg)
        ffmpeg_row.addWidget(QLabel("FFmpeg 경로:"))
        ffmpeg_row.addWidget(self.ffmpeg_edit, 1)
        ffmpeg_row.addWidget(btn_ffmpeg)
        root.addLayout(ffmpeg_row)

        # 출력 폴더
        out_row = QHBoxLayout()
        self.out_edit = QLineEdit(self.settings.get("output_dir"))
        btn_out = QPushButton("폴더…")
        btn_out.clicked.connect(self._pick_out_dir)
        out_row.addWidget(QLabel("출력 폴더:"))
        out_row.addWidget(self.out_edit, 1)
        out_row.addWidget(btn_out)
        root.addLayout(out_row)

        # 모니터/오디오/FPS
        row = QHBoxLayout()
        self.monitor_combo = QComboBox(); self.monitor_combo.setMinimumWidth(320)
        self.audio_combo = QComboBox(); self.audio_combo.setMinimumWidth(320)
        self.fps_spin = QSpinBox(); self.fps_spin.setRange(5, 120); self.fps_spin.setValue(int(self.settings.get("video_fps", 30)))
        row.addWidget(QLabel("모니터:")); row.addWidget(self.monitor_combo, 1)
        row.addWidget(QLabel("오디오(dshow):")); row.addWidget(self.audio_combo, 1)
        row.addWidget(QLabel("FPS:")); row.addWidget(self.fps_spin)
        root.addLayout(row)

        # x264 preset
        preset_row = QHBoxLayout()
        self.preset_combo = QComboBox(); self.preset_combo.addItems(["ultrafast", "superfast", "veryfast", "faster", "fast", "medium"])
        self.preset_combo.setCurrentText(self.settings.get("video_preset", "veryfast"))
        preset_row.addWidget(QLabel("x264 preset:")); preset_row.addWidget(self.preset_combo)
        root.addLayout(preset_row)

        # ▼ 추가: 하드웨어 인코더 선택
        enc_row = QHBoxLayout()
        self.encoder_combo = QComboBox()
        self.encoder_combo.addItems([
            "libx264 (software)",
            "NVIDIA (h264_nvenc)",
            "Intel QSV (h264_qsv)",
            "AMD AMF (h264_amf)",
            "HEVC NVENC (hevc_nvenc)",
            "HEVC QSV (hevc_qsv)",
            "HEVC AMF (hevc_amf)",
        ])
        enc_row.addWidget(QLabel("Video Encoder:")); enc_row.addWidget(self.encoder_combo)
        root.addLayout(enc_row)

        # 버튼
        btn_row = QHBoxLayout()
        self.btn_refresh = QPushButton("장치 새로고침")
        self.btn_start = QPushButton("녹화 시작")
        self.btn_stop = QPushButton("정지")
        self.btn_stop.setEnabled(False)
        btn_row.addWidget(self.btn_refresh)
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_start)
        btn_row.addWidget(self.btn_stop)
        root.addLayout(btn_row)

        # 로그
        self.log_view = QPlainTextEdit(); self.log_view.setReadOnly(True)
        root.addWidget(self.log_view, 1)

        # 이벤트 연결
        self.btn_refresh.clicked.connect(self._refresh_devices)
        self.btn_start.clicked.connect(self._start)
        self.btn_stop.clicked.connect(self._stop)

    # ---- 초기화 ----
    def _load_initial_state(self):
        self._refresh_monitors()
        self._refresh_audio()
        # 이전 선택 복원
        saved_idx = int(self.settings.get("monitor_index", 0))
        if 0 <= saved_idx < self.monitor_combo.count():
            self.monitor_combo.setCurrentIndex(saved_idx)
        saved_audio = self.settings.get("audio_device")
        if saved_audio and (idx := self.audio_combo.findText(saved_audio)) >= 0:
            self.audio_combo.setCurrentIndex(idx)

    def _refresh_monitors(self):
        try:
            self.monitors = list_monitors()
        except Exception as e:
            self._on_error(f"모니터 조회 실패: {e}")
            self.monitors = []
        self.monitor_combo.clear()
        for m in self.monitors:
            self.monitor_combo.addItem(f"#{m.index}  {m.width}x{m.height}  @({m.x},{m.y})")

    def _refresh_audio(self):
        ffmpeg_path = self.ffmpeg_edit.text().strip() or "ffmpeg"
        devs = list_dshow_audio_devices(ffmpeg_path)

        # devs 가 문자열 리스트일 수도, 딕셔너리 리스트일 수도 있으니 모두 처리
        self.audio_combo.clear()
        self.audio_items = []
        # 0번: 무음
        self.audio_combo.addItem("(무음)")
        self.audio_items.append(("(무음)", None))

        for d in devs:
            if isinstance(d, dict):
                # 딕셔너리인 경우: 표시용은 display, ffmpeg 인자는 moniker가 있으면 우선 사용
                label = d.get("display") or d.get("name") or d.get("moniker") or "Unknown"
                arg = d.get("moniker") or d.get("display") or d.get("name")
            else:
                # 문자열인 경우: 표시/인자 동일
                label = str(d)
                arg = label
            self.audio_combo.addItem(label)
            self.audio_items.append((label, arg))


    def _refresh_devices(self):
        self._refresh_monitors()
        self._refresh_audio()
        self._append_log("장치 목록 갱신")

    # ---- 동작 ----
    def _start(self):
        if not self.monitors:
            self._on_error("모니터 정보가 없습니다.")
            return
        mon = self.monitors[self.monitor_combo.currentIndex()]

        ffmpeg_path = self.ffmpeg_edit.text().strip() or "ffmpeg"
        out_dir = Path(self.out_edit.text().strip() or Path.cwd() / "recordings")
        fps = int(self.fps_spin.value())
        preset = self.preset_combo.currentText()

        idx = self.audio_combo.currentIndex()
        label, ff_arg = self.audio_items[idx]  # (표시문자열, ffmpeg용 인자)

        if ff_arg is None:  # "(무음)"
            audio_mode = "none"
            audio_dev = None
        else:
            audio_mode = "dshow"
            audio_dev = ff_arg  # 모니커가 있으면 그게 들어갑니다


        # 인코더 매핑
        enc_text = self.encoder_combo.currentText()
        enc_map = {
            "libx264 (software)": "libx264",
            "NVIDIA (h264_nvenc)": "h264_nvenc",
            "Intel QSV (h264_qsv)": "h264_qsv",
            "AMD AMF (h264_amf)": "h264_amf",
            "HEVC NVENC (hevc_nvenc)": "hevc_nvenc",
            "HEVC QSV (hevc_qsv)": "hevc_qsv",
            "HEVC AMF (hevc_amf)": "hevc_amf",
        }
        encoder = enc_map.get(enc_text, "libx264")

        # 설정 저장(dshow일 때만)
        self.settings.set("audio_device", None if audio_mode != "dshow" else audio_dev)

        opt = FFmpegOptions(
            ffmpeg_path=ffmpeg_path,
            output_dir=out_dir,
            fps=fps,
            preset=preset,
            monitor=mon,
            audio_mode=audio_mode,
            audio_device=audio_dev,
            encoder=encoder,      # ← 추가된 옵션 전달
        )

        self.rec.start(opt)
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)

    def _stop(self):
        self.rec.stop()

    # ---- 유틸 ----
    def _append_log(self, msg: str):
        if not msg:
            return
        self._log_buf.append(msg)
        
    def _flush_log(self):
        if not self._log_buf:
            return
        text = "\n".join(self._log_buf)
        self._log_buf.clear()
        self.log_view.appendPlainText(text)
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_error(self, msg: str):
        self._append_log("[ERROR] " + msg)
        QMessageBox.warning(self, "오류", msg)

    def _on_stopped(self, code: int):
        self._append_log(f"녹화 종료 (exit={code})")
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)

    def _pick_ffmpeg(self):
        path, _ = QFileDialog.getOpenFileName(self, "ffmpeg.exe 선택", "", "ffmpeg (ffmpeg.exe)")
        if path:
            self.ffmpeg_edit.setText(path)

    def _pick_out_dir(self):
        d = QFileDialog.getExistingDirectory(self, "출력 폴더 선택", self.out_edit.text())
        if d:
            self.out_edit.setText(d)
