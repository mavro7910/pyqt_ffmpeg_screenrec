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
    "libx264",          # Software (기본/안정)
    "h264_nvenc",       # NVIDIA
    "h264_qsv",         # Intel QSV
    "h264_amf",         # AMD AMF
    "hevc_nvenc",       # NVIDIA HEVC
    "hevc_qsv",         # Intel HEVC
    "hevc_amf",         # AMD HEVC
]
SyncFilter = Literal["none", "aresample"]

@dataclass
class FFmpegOptions:
    ffmpeg_path: str
    output_dir: Path
    fps: int
    preset: str
    monitor: MonitorInfo
    audio_mode: AudioMode
    audio_device: Optional[str]

    # 인코더 선택
    encoder: VideoEncoder = "libx264"

    # ▼ 추가: 싱크 조절 관련 옵션 (기본값은 영향 없음)
    audio_delay_ms: int = 500      # 오디오 입력 지연 (ms). +면 오디오 늦춤
    video_delay_ms: int = 0      # 비디오 입력 지연 (ms). +면 비디오 늦춤
    sync_filter: SyncFilter = "none"   # "none" | "aresample" (소폭 드리프트 보정)

class FFmpegRecorder(QObject):
    started = pyqtSignal()
    stopped = pyqtSignal(int)
    error = pyqtSignal(str)
    log = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.proc: Optional[QProcess] = None

    # -----------------------
    # Encoder argument presets (안정 위주)
    # -----------------------
    def _video_encode_args(self, opt: FFmpegOptions) -> List[str]:
        enc = opt.encoder
        if enc == "libx264":
            return [
                "-c:v", "libx264",
                "-preset", opt.preset,      # veryfast 권장
                "-pix_fmt", "yuv420p",
            ]
        if enc in ("h264_nvenc", "hevc_nvenc"):
            return [
                "-c:v", enc,
                "-preset", "p5",            # 문제가 있으면 p3~p5 조정
                "-pix_fmt", "yuv420p",
            ]
        if enc in ("h264_qsv", "hevc_qsv", "h264_amf", "hevc_amf"):
            return [
                "-c:v", enc,
                "-pix_fmt", "yuv420p",
            ]
        # fallback (안정)
        return ["-c:v", "libx264", "-preset", opt.preset, "-pix_fmt", "yuv420p"]

    # -----------------------
    # Command builder (안정 프로필 + 선택적 싱크조절)
    # -----------------------
    def build_command(self, opt: FFmpegOptions, out_file: Path) -> list[str]:
        m = opt.monitor

        # (A) 비디오 입력: gdigrab 고정(폭넓은 호환)
        video_args: List[str] = []
        if opt.video_delay_ms and opt.video_delay_ms != 0:
            # 비디오를 지연시키고 싶으면, 해당 입력 -i 앞에 -itsoffset을 둔다.
            video_args += ["-itsoffset", f"{opt.video_delay_ms/1000:.3f}"]

        video_args += [
            "-f", "gdigrab",
            "-framerate", str(opt.fps),
            "-offset_x", str(m.x),
            "-offset_y", str(m.y),
            "-video_size", f"{m.width}x{m.height}",
            "-i", "desktop",
        ]

        # (B) 오디오 입력: dshow
        audio_args: List[str] = []
        if opt.audio_mode == "dshow" and opt.audio_device:
            if opt.audio_delay_ms and opt.audio_delay_ms != 0:
                # 오디오를 지연시키고 싶으면, 해당 입력 -i 앞에 -itsoffset을 둔다.
                audio_args += ["-itsoffset", f"{opt.audio_delay_ms/1000:.3f}"]
            audio_args += [
                "-thread_queue_size", "512",
                "-f", "dshow",
                "-i", f"audio={opt.audio_device}",
            ]

        # (C) 인코더
        encode_v = self._video_encode_args(opt)

        # (D) 오디오 인코딩 & (선택) 드리프트 보정 필터
        if opt.audio_mode == "none":
            encode_a = ["-an"]
            audio_fix: List[str] = []
        else:
            encode_a = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
            if opt.sync_filter == "aresample":
                # 가벼운 동기화 보정: 과도한 재타이밍은 피하면서 소폭 드리프트만 흡수
                # (필요 시 first_pts=0 제거/조정 가능)
                audio_fix = ["-af", "aresample=async=1:min_hard_comp=0.100:first_pts=0"]
            else:
                audio_fix = []

        # (E) MP4 재생 호환성 향상
        mux_misc = ["-movflags", "+faststart"]

        # (F) 전체 명령 (로그는 warning으로, UI 부하 낮춤)
        return [opt.ffmpeg_path, "-y", "-hide_banner", "-v", "warning"] \
               + video_args + audio_args \
               + encode_v + encode_a + audio_fix \
               + mux_misc + [str(out_file)]

    # -----------------------
    # Lifecycle
    # -----------------------
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

        # 필요 시 main_window 측에서 로그 쓰로틀링 권장
        proc.readyReadStandardError.connect(
            lambda p=proc: self._relay(bytes(p.readAllStandardError()).decode(errors="ignore"))
        )
        proc.readyReadStandardOutput.connect(
            lambda p=proc: self._relay(bytes(p.readAllStandardOutput()).decode(errors="ignore"))
        )
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
