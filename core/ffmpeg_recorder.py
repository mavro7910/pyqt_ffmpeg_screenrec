
# core/ffmpeg_recorder.py — clean separation of audio input vs -an, full cmd log
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Literal

from PyQt5.QtCore import QProcess, QObject, pyqtSignal

from .monitor_utils import MonitorInfo

AudioMode = Literal["none", "dshow"]

@dataclass
class FFmpegOptions:
    ffmpeg_path: str
    output_dir: Path
    fps: int
    preset: str
    monitor: MonitorInfo
    audio_mode: AudioMode            # "none" | "dshow"
    audio_arg: Optional[str] = None  # e.g. 'audio=@device_cm_{...}\wave_{...}'

class FFmpegRecorder(QObject):
    started = pyqtSignal()
    stopped = pyqtSignal(int)
    error = pyqtSignal(str)
    log = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.proc: Optional[QProcess] = None

    def build_command(self, opt: FFmpegOptions, out_file: Path) -> list[str]:
        mon = opt.monitor
        video_args = [
            "-f", "gdigrab",
            "-framerate", str(opt.fps),
            "-offset_x", str(mon.x),
            "-offset_y", str(mon.y),
            "-video_size", f"{mon.width}x{mon.height}",
            "-i", "desktop",
        ]

        has_audio = (opt.audio_mode == "dshow" and bool(opt.audio_arg))
        audio_args = ["-thread_queue_size", "1024", "-f", "dshow", "-i", opt.audio_arg] if has_audio else []

        encode_args = ["-c:v", "libx264", "-preset", opt.preset, "-pix_fmt", "yuv420p"]
        encode_args += (["-c:a", "aac", "-b:a", "192k"] if has_audio else ["-an"])

        cmd = [opt.ffmpeg_path, "-y", "-hide_banner", "-v", "info"] \
              + video_args + audio_args + encode_args + [str(out_file)]
        return cmd

    def start(self, opt: FFmpegOptions):
        if self.proc is not None:
            self.error.emit("이미 녹화가 실행 중입니다.")
            return

        if not Path(opt.ffmpeg_path).exists():
            self.error.emit(f"FFmpeg 경로가 잘못되었습니다: {opt.ffmpeg_path}")
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_file = opt.output_dir / f"record_{timestamp}.mp4"
        opt.output_dir.mkdir(parents=True, exist_ok=True)

        cmd = self.build_command(opt, out_file)
        proc = QProcess(self)
        self.proc = proc
        proc.setProgram(cmd[0])
        proc.setArguments(cmd[1:])

        proc.readyReadStandardError.connect(lambda p=proc: self._relay(bytes(p.readAllStandardError()).decode(errors="ignore")))
        proc.readyReadStandardOutput.connect(lambda p=proc: self._relay(bytes(p.readAllStandardOutput()).decode(errors="ignore")))
        proc.errorOccurred.connect(lambda e: self.error.emit(f"QProcess error: {e}"))
        proc.finished.connect(lambda code, status, p=proc: self._on_finished(p, code, status))

        # full command log
        self.log.emit("실행: " + " ".join(cmd))
        proc.start()
        if not proc.waitForStarted(5000):
            self.error.emit("FFmpeg 시작 실패. ffmpeg 경로/권한을 확인하세요.")
            self.proc = None
            return
        self.started.emit()

    def stop(self):
        if self.proc is None:
            return
        try:
            self.proc.write(b"q\n")
            if not self.proc.waitForFinished(3000):
                self.proc.terminate()
                if not self.proc.waitForFinished(3000):
                    self.proc.kill()
        finally:
            self.proc = None

    def _on_finished(self, p, code, _status):
        self.stopped.emit(int(code))
        if self.proc is p:
            self.proc = None

    def _relay(self, text: str):
        if text:
            self.log.emit(text.rstrip())
