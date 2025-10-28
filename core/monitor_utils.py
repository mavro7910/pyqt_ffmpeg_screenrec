# ===== file: core/monitor_utils.py =====
# Windows 전용: 다중 모니터 정보 조회
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from typing import List

# RECT 구조체
class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

MonitorEnumProc = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(RECT), ctypes.c_double)

@dataclass
class MonitorInfo:
    index: int
    x: int
    y: int
    width: int
    height: int


def _enum_proc_factory(sink: List[MonitorInfo]):
    idx = {"i": 0}

    def _proc(hMonitor, hdc, lprcMonitor, dwData):
        r: RECT = lprcMonitor.contents
        w = r.right - r.left
        h = r.bottom - r.top
        sink.append(MonitorInfo(index=idx["i"], x=r.left, y=r.top, width=w, height=h))
        idx["i"] += 1
        return 1  # continue
    return MonitorEnumProc(_proc)


def list_monitors() -> List[MonitorInfo]:
    user32 = ctypes.windll.user32
    monitors: List[MonitorInfo] = []
    cb = _enum_proc_factory(monitors)
    if not user32.EnumDisplayMonitors(0, 0, cb, 0):
        raise OSError("EnumDisplayMonitors failed")
    return monitors
