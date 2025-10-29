# core/ffmpeg_recorder.py
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Literal, List

from PyQt5.QtCore import QProcess, QObject, pyqtSignal

from .monitor_utils import MonitorInfo

AudioMode = Literal["none", "dshow"]
VideoEncoder = Literal[
    "libx264",          # 소프트웨어
    "h264_nvenc",       # NVIDIA
    "h264_qsv",         # Intel QSV
    "h264_amf",         # AMD AMF
    "hevc_nvenc",       # NVIDIA HEVC
    "hevc_qsv",         # Intel HEVC
    "hevc_amf",         # AMD HEVC
]

@dataclass
class FFmpegOptions:
    ffmpeg_path: str
    output_dir: Path
    fps: int
    preset: str
    monitor: MonitorInfo
    audio_mode: AudioMode
    audio_device: Optional[str]

    # ▼ 추가: 하드웨어 인코더 선택 (기본 libx264)
    encoder: VideoEncoder = "libx264"

class FFmpegRecorder(QObject):
    started = pyqtSignal()
    stopped = pyqtSignal(int)
    error = pyqtSignal(str)
    log = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.proc: Optional[QProcess] = None

    def _video_encode_args(self, opt: FFmpegOptions) -> List[str]:
        enc = opt.encoder
        # libx264는 preset 사용, 나머지는 각 하드웨어 기본 preset으로
        if enc == "libx264":
            return ["-c:v", "libx264", "-preset", opt.preset, "-pix_fmt", "yuv420p"]
        # NVENC/QSV/AMF/HEVC 계열
        return ["-c:v", enc, "-pix_fmt", "yuv420p"]

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

        audio_args: list[str] = []
        if opt.audio_mode == "dshow" and opt.audio_device:
            audio_args = ["-thread_queue_size", "512", "-f", "dshow", "-i", f"audio={opt.audio_device}"]

        encode_v = self._video_encode_args(opt)
        encode_a = (["-c:a", "aac", "-b:a", "192k"] if opt.audio_mode != "none" else ["-an"])

        # MP4 재생 호환성 향상
        mux_misc = ["-movflags", "+faststart"]

        return [opt.ffmpeg_path, "-y", "-hide_banner", "-v", "info"] \
               + video_args + audio_args + encode_v + encode_a + mux_misc + [str(out_file)]

    def start(self, opt: FFmpegOptions):
        if self.proc is not None:
            self.error.emit("이미 녹화가 실행 중입니다.")
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_file = opt.output_dir / f"record_{timestamp}.mp4"
        opt.output_dir.mkdir(parents=True, exist_ok=True)

        cmd = self.build_command(opt, out_file)

        proc = QProcess(self)
        self.proc = proc
        proc.setProgram(cmd[0])
        proc.setArguments(cmd[1:])

        # 표준 출력/에러 로그 연결
        proc.readyReadStandardError.connect(lambda p=proc: self._relay(bytes(p.readAllStandardError()).decode(errors="ignore")))
        proc.readyReadStandardOutput.connect(lambda p=proc: self._relay(bytes(p.readAllStandardOutput()).decode(errors="ignore")))
        proc.errorOccurred.connect(lambda e: self.error.emit(f"QProcess error: {e}"))
        proc.finished.connect(lambda code, _status: self._on_finished(code))

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

    def _on_finished(self, code: int):
        self.stopped.emit(int(code))
        self.proc = None

    def _relay(self, text: str):
        if text:
            self.log.emit(text.rstrip())
