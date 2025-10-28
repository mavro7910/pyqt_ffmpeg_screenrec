from __future__ import annotations
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QFileDialog, QSpinBox, QPlainTextEdit, QMessageBox, QLineEdit
)

from core.settings import Settings
from core.monitor_utils import list_monitors, MonitorInfo
from core.device_utils import list_dshow_audio_devices, pick_virtual_audio, choose_device_arg
from core.ffmpeg_recorder import FFmpegRecorder, FFmpegOptions

DISPLAY_ROLE = Qt.UserRole + 1
ARG_ROLE = Qt.UserRole + 2

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FFmpeg Screen Recorder (dshow auto-pick)")
        self.setMinimumSize(800, 580)

        self.settings = Settings()
        self.monitors: list[MonitorInfo] = []
        self.audio_devices = []

        self.rec = FFmpegRecorder()
        self.rec.started.connect(lambda: self._append_log("녹화 시작"))
        self.rec.stopped.connect(self._on_stopped)
        self.rec.error.connect(self._on_error)
        self.rec.log.connect(self._append_log)

        self._build_ui()
        self._load_initial_state()

    def _build_ui(self):
        cw = QWidget(); self.setCentralWidget(cw)
        root = QVBoxLayout(cw)

        ffmpeg_row = QHBoxLayout()
        self.ffmpeg_edit = QLineEdit(self.settings.get("ffmpeg_path"))
        btn_ffmpeg = QPushButton("찾기…")
        btn_ffmpeg.clicked.connect(self._pick_ffmpeg)
        ffmpeg_row.addWidget(QLabel("FFmpeg 경로:"))
        ffmpeg_row.addWidget(self.ffmpeg_edit, 1)
        ffmpeg_row.addWidget(btn_ffmpeg)
        root.addLayout(ffmpeg_row)

        out_row = QHBoxLayout()
        self.out_edit = QLineEdit(self.settings.get("output_dir"))
        btn_out = QPushButton("폴더…")
        btn_out.clicked.connect(self._pick_out_dir)
        out_row.addWidget(QLabel("출력 폴더:"))
        out_row.addWidget(self.out_edit, 1)
        out_row.addWidget(btn_out)
        root.addLayout(out_row)

        row = QHBoxLayout()
        self.monitor_combo = QComboBox(); self.monitor_combo.setMinimumWidth(320)
        self.audio_combo = QComboBox(); self.audio_combo.setMinimumWidth(420)
        self.fps_spin = QSpinBox(); self.fps_spin.setRange(5, 120); self.fps_spin.setValue(int(self.settings.get("video_fps", 30)))
        row.addWidget(QLabel("모니터:")); row.addWidget(self.monitor_combo, 1)
        row.addWidget(QLabel("오디오(dshow):")); row.addWidget(self.audio_combo, 1)
        row.addWidget(QLabel("FPS:")); row.addWidget(self.fps_spin)
        root.addLayout(row)

        preset_row = QHBoxLayout()
        self.preset_combo = QComboBox(); self.preset_combo.addItems(["ultrafast", "superfast", "veryfast", "faster", "fast", "medium"])
        self.preset_combo.setCurrentText(self.settings.get("video_preset", "veryfast"))
        preset_row.addWidget(QLabel("x264 preset:")); preset_row.addWidget(self.preset_combo)
        root.addLayout(preset_row)

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

        hint = QLabel("VB-CABLE/VoiceMeeter가 있으면 자동 선택됩니다. Alternative name(모니커)을 우선 사용합니다.")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.log_view = QPlainTextEdit(); self.log_view.setReadOnly(True)
        root.addWidget(self.log_view, 1)

        self.btn_refresh.clicked.connect(self._refresh_devices)
        self.btn_start.clicked.connect(self._start)
        self.btn_stop.clicked.connect(self._stop)
   
    def _load_initial_state(self):
        self._refresh_monitors()
        ffmpeg_path = (self.ffmpeg_edit.text() or "").strip()
        if ffmpeg_path:
            self._refresh_audio()
        else:
            self.audio_combo.clear(); self.audio_combo.addItem("(무음)")
        # 모니터 인덱스 복원
        saved_idx = int(self.settings.get("monitor_index", 0))
        if 0 <= saved_idx < self.monitor_combo.count():
            self.monitor_combo.setCurrentIndex(saved_idx)

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
        ffmpeg_path = (self.ffmpeg_edit.text() or "").strip()
        if not ffmpeg_path:
            self.audio_combo.clear(); self.audio_combo.addItem("(무음)")
            self._append_log("[diag] ffmpeg 경로 비어있음 → dshow 조회 생략")
            return
        devs = list_dshow_audio_devices(ffmpeg_path)
        self._append_log(f"[diag] dshow devs={len(devs)} from {ffmpeg_path}")  # ★ 몇 개 나왔는지

        self.audio_combo.clear()
        self.audio_combo.addItem("(무음)")
        for d in devs:
            disp, alt = d["display"], d.get("alt","")
            text = disp if not alt else f'{disp}  —  {alt}'
            arg  = choose_device_arg(d)
            idx = self.audio_combo.count()
            self.audio_combo.addItem(text)
            self.audio_combo.setItemData(idx, disp, Qt.UserRole+1)
            self.audio_combo.setItemData(idx, arg,  Qt.UserRole+2)


            vac = pick_virtual_audio(devs)
            if vac:
                arg = choose_device_arg(vac)
                for i in range(self.audio_combo.count()):
                    if self.audio_combo.itemData(i, Qt.UserRole+2) == arg:
                        self.audio_combo.setCurrentIndex(i)
                        break

    def _refresh_devices(self):
        self._refresh_monitors()
        self._refresh_audio()  # 내부에서 경로 체크
        self._append_log("장치 목록 갱신")

    def _start(self):
        if not self.monitors:
            self._on_error("모니터 정보가 없습니다.")
            return
        mon = self.monitors[self.monitor_combo.currentIndex()]

        ffmpeg_path = self.ffmpeg_edit.text().strip() or "ffmpeg"
        out_dir = Path(self.out_edit.text().strip() or Path.cwd() / "recordings")
        fps = int(self.fps_spin.value())
        preset = self.preset_combo.currentText()

        arg = self.audio_combo.itemData(self.audio_combo.currentIndex(), Qt.UserRole+2)
        audio_mode = "dshow" if arg else "none"

        opt = FFmpegOptions(
            ffmpeg_path=ffmpeg_path,
            output_dir=out_dir,
            fps=fps,
            preset=preset,
            monitor=mon,
            audio_mode=audio_mode,
            audio_arg=arg,
        )
        self.rec.start(opt)
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)

    def _stop(self):
        self.rec.stop()
        self._append_log("정지 처리 완료")
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)

    def _append_log(self, msg: str):
        if not msg:
            return
        self.log_view.appendPlainText(msg)
        self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

    def _on_error(self, msg: str):
        self._append_log("[ERROR] " + msg)
        QMessageBox.warning(self, "오류", msg)

    def _on_stopped(self, code: int):
        self._append_log(f"녹화 종료 (exit={code})")

    def _pick_ffmpeg(self):
        path, _ = QFileDialog.getOpenFileName(self, "ffmpeg.exe 선택", "", "ffmpeg (ffmpeg.exe)")
        if path:
            self.ffmpeg_edit.setText(path)
            self.settings.set("ffmpeg_path", path)
            self._refresh_audio()  # ★ 경로 설정 직후 장치 재조회

    def _pick_out_dir(self):
        d = QFileDialog.getExistingDirectory(self, "출력 폴더 선택", self.out_edit.text())
        if d:
            self.out_edit.setText(d)