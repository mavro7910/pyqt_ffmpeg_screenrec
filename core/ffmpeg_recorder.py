# core/ffmpeg_recorder.py
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Literal, List
import subprocess
import shutil

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
State = Literal["IDLE", "RUNNING", "CLOSING", "PAUSED"]

@dataclass
class FFmpegOptions:
    ffmpeg_path: str
    output_dir: Path
    fps: int
    preset: str
    monitor: MonitorInfo
    audio_mode: AudioMode
    audio_device: Optional[str]
    encoder: VideoEncoder = "libx264"
    audio_delay_ms: int = 500  # +면 오디오 늦춤(ms)

class FFmpegRecorder(QObject):
    started = pyqtSignal()
    stopped = pyqtSignal(int)
    error = pyqtSignal(str)
    log = pyqtSignal(str)
    stateChanged = pyqtSignal(str)  # "IDLE" | "RUNNING" | "CLOSING" | "PAUSED"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.proc: Optional[QProcess] = None
        self.opt: Optional[FFmpegOptions] = None

        self.base_ts: Optional[str] = None
        self.seg_dir: Optional[Path] = None
        self.segments: List[Path] = []
        self.final_path: Optional[Path] = None

        self._state: State = "IDLE"
        self.resume_pending: bool = False
        self.suppress_next_seg_log: bool = False

    # ---- 상태 관리 ----
    def _set_state(self, s: State):
        if self._state != s:
            self._state = s
            self.stateChanged.emit(s)

    def state(self) -> State:
        return self._state

    def is_recording(self) -> bool:
        return self._state in ("RUNNING", "CLOSING")

    def is_paused(self) -> bool:
        return self._state == "PAUSED"

    # ---- 인코더 ----
    def _video_encode_args(self, opt: FFmpegOptions) -> List[str]:
        enc = opt.encoder
        if enc == "libx264":
            return ["-c:v", "libx264", "-preset", opt.preset, "-pix_fmt", "yuv420p"]
        if enc in ("h264_nvenc", "hevc_nvenc"):
            return ["-c:v", enc, "-preset", "p5", "-pix_fmt", "yuv420p"]
        if enc in ("h264_qsv", "hevc_qsv", "h264_amf", "hevc_amf"):
            return ["-c:v", enc, "-pix_fmt", "yuv420p"]
        return ["-c:v", "libx264", "-preset", opt.preset, "-pix_fmt", "yuv420p"]

    def _segment_filename(self, idx: int) -> Path:
        assert self.base_ts and self.seg_dir
        return self.seg_dir / f"seg{idx:02d}.mp4"

    def _final_filename(self) -> Path:
        assert self.base_ts and self.opt
        return self.opt.output_dir / f"record_{self.base_ts}.mp4"

    # ---- 커맨드 ----
    def build_command(self, opt: FFmpegOptions, out_file: Path) -> list[str]:
        m = opt.monitor
        video_args: List[str] = [
            "-f", "gdigrab",
            "-framerate", str(opt.fps),
            "-offset_x", str(m.x),
            "-offset_y", str(m.y),
            "-video_size", f"{m.width}x{m.height}",
            "-i", "desktop",
        ]
        audio_args: List[str] = []
        if opt.audio_mode == "dshow" and opt.audio_device:
            if opt.audio_delay_ms and opt.audio_delay_ms != 0:
                audio_args += ["-itsoffset", f"{opt.audio_delay_ms/1000:.3f}"]
            audio_args += ["-thread_queue_size", "512", "-f", "dshow", "-i", f"audio={opt.audio_device}"]

        encode_v = self._video_encode_args(opt)
        encode_a = (["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"] if opt.audio_mode != "none" else ["-an"])
        mux_misc = ["-movflags", "+faststart"]

        return [opt.ffmpeg_path, "-y", "-hide_banner", "-v", "warning"] \
               + video_args + audio_args + encode_v + encode_a + mux_misc + [str(out_file)]

    # ---- 세그먼트 라이프사이클 ----
    def start(self, opt: FFmpegOptions) -> bool:
        if self.proc is not None or self.is_recording():
            self.error.emit("이미 녹화가 실행 중입니다.")
            return False
        if not Path(opt.ffmpeg_path).exists():
            self.error.emit(f"FFmpeg 경로가 잘못되었습니다: {opt.ffmpeg_path}")
            return False

        self.opt = opt
        opt.output_dir.mkdir(parents=True, exist_ok=True)
        self.base_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.seg_dir = opt.output_dir / f".segments_{self.base_ts}"
        self.seg_dir.mkdir(parents=True, exist_ok=True)
        self.segments.clear()
        self.final_path = self._final_filename()
        self.resume_pending = False
        self.suppress_next_seg_log = False

        ok = self._start_new_segment()
        if ok:
            self.started.emit()
        return ok

    def _start_new_segment(self) -> bool:
        assert self.opt and self.base_ts and self.seg_dir
        seg_path = self._segment_filename(len(self.segments) + 1)
        cmd = self.build_command(self.opt, seg_path)

        proc = QProcess(self)
        self.proc = proc
        self._set_state("RUNNING")

        proc.setProgram(cmd[0])
        proc.setArguments(cmd[1:])
        proc.readyReadStandardError.connect(lambda p=proc: self._relay(bytes(p.readAllStandardError()).decode(errors="ignore")))
        proc.readyReadStandardOutput.connect(lambda p=proc: self._relay(bytes(p.readAllStandardOutput()).decode(errors="ignore")))
        proc.errorOccurred.connect(lambda e: self.error.emit(f"QProcess error: {e}"))
        proc.finished.connect(lambda code, _status, p=proc, s=seg_path: self._on_segment_finished(code, s, p))

        self.log.emit("실행: " + " ".join(cmd))
        proc.start()
        if not proc.waitForStarted(5000):
            self.error.emit("FFmpeg 시작 실패. ffmpeg 경로/권한을 확인하세요.")
            self.proc = None
            self._set_state("IDLE")
            return False
        return True

    def pause(self) -> bool:
        if self.state() != "RUNNING" or self.proc is None:
            return False
        try:
            self.suppress_next_seg_log = True  # 일시정지 저장 로그 숨김
            self._set_state("CLOSING")
            self.proc.write(b"q\n")
            self.log.emit("일시정지")
            return True
        except Exception as e:
            self.error.emit(f"일시정지 실패: {e}")
            self._set_state("RUNNING")
            self.suppress_next_seg_log = False
            return False

    def resume(self) -> bool:
        st = self.state()
        if st == "RUNNING":
            return True
        if st == "CLOSING":
            self.resume_pending = True
            self.log.emit("(재개 예약)")
            return True
        if st == "PAUSED":
            self.resume_pending = False
            return self._start_new_segment()
        if st == "IDLE" and self.segments:
            self.resume_pending = False
            return self._start_new_segment()
        self.error.emit("재개할 녹화가 없습니다.")
        return False

    def stop(self) -> int:
        code = 0
        try:
            self.resume_pending = False
            if self.proc is not None:
                try:
                    self.proc.write(b"q\n")
                except Exception:
                    pass
                if not self.proc.waitForFinished(5000):
                    self.proc.terminate()
                    if not self.proc.waitForFinished(3000):
                        self.proc.kill()
            code = self._finalize_concat()
        except Exception as e:
            self.error.emit(f"정지 처리 중 오류: {e}")
            code = -1
        finally:
            self.proc = None
            self._set_state("IDLE")
            self.stopped.emit(int(code))
        return code

    def _on_segment_finished(self, code: int, seg_path: Path, who: QProcess):
        if self.proc is who:
            self.proc = None

        if code == 0 and seg_path.exists() and seg_path.stat().st_size > 0:
            self.segments.append(seg_path)
            if not self.suppress_next_seg_log:
                self.log.emit(f"[segment] saved: {seg_path.name}")
        else:
            if not self.suppress_next_seg_log:
                self.log.emit(f"[segment] skipped (exit={code})")

        was_closing = (self.state() == "CLOSING")
        if was_closing:
            self._set_state("PAUSED")
        self.suppress_next_seg_log = False

        if self.resume_pending:
            self.resume_pending = False
            ok = self._start_new_segment()
            if not ok:
                self.error.emit("재개 실패(세그먼트 시작 불가).")

    def _finalize_concat(self) -> int:
        if not self.opt or not self.base_ts or not self.seg_dir:
            return -1
        if not self.segments:
            self.error.emit("저장할 세그먼트가 없습니다.")
            return -2

        final = self._final_filename()
        if len(self.segments) == 1:
            only = self.segments[0]
            try:
                if final.exists():
                    final.unlink()
                shutil.move(str(only), str(final))
                try:
                    self.seg_dir.rmdir()
                except Exception:
                    pass
                self.log.emit(f"[merge] single segment → {final.name}")
                return 0
            except Exception as e:
                self.error.emit(f"파일 이동 실패: {e}")
                return -3

        lst = self.seg_dir / "concat_list.txt"
        try:
            with open(lst, "w", encoding="utf-8") as f:
                for p in self.segments:
                    path_str = str(p).replace("\\", "/").replace("'", "'\\''")
                    f.write(f"file '{path_str}'\n")
        except Exception as e:
            self.error.emit(f"concat 리스트 작성 실패: {e}")
            return -4

        cmd = [
            self.opt.ffmpeg_path, "-hide_banner", "-y",
            "-f", "concat", "-safe", "0", "-i", str(lst),
            "-c", "copy",
            str(final),
        ]
        try:
            self.log.emit("실행: " + " ".join(cmd))
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
            if r.returncode != 0:
                self.error.emit(f"concat 실패: {r.stderr.strip()}")
                return r.returncode
            self.log.emit(f"[merge] {len(self.segments)}개 세그먼트 병합 완료 → {final.name}")
            try:
                for p in self.segments:
                    if p.exists():
                        p.unlink()
                if lst.exists():
                    lst.unlink()
                self.seg_dir.rmdir()
            except Exception:
                pass
            return 0
        except FileNotFoundError:
            self.error.emit("FFmpeg 실행 실패(경로 확인).")
            return -5

    def _relay(self, text: str):
        if text:
            self.log.emit(text.rstrip())
