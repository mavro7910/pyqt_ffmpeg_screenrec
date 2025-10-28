# ===== file: core/settings.py =====
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict

_DEFAULT = {
    "ffmpeg_path": "ffmpeg",  # PATH에 있으면 그대로 사용
    "output_dir": str(Path.cwd() / "recordings"),
    "video_fps": 30,
    "video_preset": "veryfast",
    "audio_device": None,
    "monitor_index": 0,
}

class Settings:
    def __init__(self, path: Path | None = None):
        self.path = path or Path("settings.json")
        self.data: Dict[str, Any] = {}
        self.load()

    def load(self):
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self.data = {}
        for k, v in _DEFAULT.items():
            self.data.setdefault(k, v)

        # ensure output directory
        Path(self.data["output_dir"]).mkdir(parents=True, exist_ok=True)

    def save(self):
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def set(self, key: str, value):
        self.data[key] = value
        self.save()
