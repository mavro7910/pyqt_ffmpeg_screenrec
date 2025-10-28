
# core/device_utils.py — dshow parsing + prefix strip + headerless (audio) + pick_virtual_audio
from __future__ import annotations
import subprocess, re, locale, shutil
from pathlib import Path
from typing import List, Dict, Optional

try:
    import chardet  # optional
except Exception:
    chardet = None

# Virtual/loopback-like device name candidates
VAC_CANDIDATES = [
    "CABLE Output (VB-Audio Virtual Cable)",
    "CABLE Output(VB-Audio Virtual Cable)",  # variant without space
    "VoiceMeeter Output (VB-Audio VoiceMeeter VAIO)",
    "VoiceMeeter Aux Output (VB-Audio VoiceMeeter AUX VAIO)",
    "VoiceMeeter VAIO3 Output (VB-Audio VoiceMeeter VAIO3)",
    "VoiceMeeter AUX VAIO Output (VB-Audio VoiceMeeter AUX VAIO)",
]

# "CABLE Output(VB-Audio Virtual Cable)" (audio)
#   Alternative name "@device_cm_{...}\wave_{...}"
_DEV_RE = re.compile(r'^\s*"(?P<name>[^"]+)"\s+\((?P<kind>audio|video)\)\s*$', re.I)
_ALT_RE = re.compile(r'^\s*Alternative name\s+"(?P<alt>[^"]+)"\s*$', re.I)

def _resolve_ffmpeg(ffmpeg_path: str) -> Optional[str]:
    p = (ffmpeg_path or "").strip()
    if p and Path(p).exists():
        return p
    return shutil.which(p or "ffmpeg")

def _smart_decode(b: bytes) -> str:
    # 1) utf-8
    try:
        t = b.decode("utf-8")
        if "�" not in t and "留" not in t:
            return t
    except Exception:
        pass
    # 2) system default (KR Windows: cp949/mbcs)
    enc = locale.getpreferredencoding(False) or "mbcs"
    try:
        t = b.decode(enc, errors="replace")
        if "�" not in t:
            return t
    except Exception:
        pass
    # 3) chardet guess
    if chardet:
        try:
            guess = chardet.detect(b or b"")
            enc2 = (guess or {}).get("encoding")
            if enc2:
                return b.decode(enc2, errors="replace")
        except Exception:
            pass
    # 4) fallback
    return b.decode("utf-8", errors="replace")

def _run_ffmpeg_list(ffmpeg_path: str) -> str:
    exe = _resolve_ffmpeg(ffmpeg_path)
    if not exe:
        print("[diag] ffmpeg not found for dshow list:", ffmpeg_path)
        return ""
    print("[diag] dshow list using:", exe)
    try:
        proc = subprocess.run(
            [exe, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
            capture_output=True, text=False
        )
    except Exception as e:
        print("[diag] ffmpeg dshow list failed:", e)
        return ""
    return _smart_decode(proc.stderr or b"")

def _strip_dshow_prefix(line: str) -> str:
    # e.g. '[dshow @ 000001...] "CABLE Output(...)" (audio)'
    s = line.lstrip()
    if s.startswith("[dshow"):
        r = s.find("]")
        if r != -1:
            s = s[r+1:].lstrip()
    return s

def list_dshow_audio_devices(ffmpeg_path: str = "ffmpeg") -> List[Dict[str, str]]:
    """
    Returns a list of dicts: {"display": <str>, "alt": <str or ''>}
    - Works even if the 'DirectShow audio devices' header is missing, by detecting '(audio)' lines.
    - If display parsing fails but Alternative name exists, still adds an entry using alt only.
    """
    out = _run_ffmpeg_list(ffmpeg_path)
    devices: List[Dict[str, str]] = []
    in_audio = False
    last_idx: Optional[int] = None

    for raw in out.splitlines():
        line0 = raw.rstrip("\r\n")
        line = _strip_dshow_prefix(line0)

        # (optional) section headers
        if "DirectShow audio devices" in line:
            in_audio = True
            continue
        if "DirectShow video devices" in line:
            in_audio = False
            continue

        m = _DEV_RE.match(line)
        if m:
            kind = (m.group("kind") or "").lower()
            if kind == "audio" or (not in_audio and "(audio)" in line.lower()):
                name = m.group("name")
                devices.append({"display": name, "alt": ""})
                last_idx = len(devices) - 1
            continue

        m2 = _ALT_RE.match(line)
        if m2:
            alt = m2.group("alt")
            if last_idx is not None:
                devices[last_idx]["alt"] = alt
            else:
                devices.append({"display": "", "alt": alt})
                last_idx = len(devices) - 1

    # de-duplicate
    seen = set()
    uniq: List[Dict[str, str]] = []
    for d in devices:
        key = (d.get("display",""), d.get("alt",""))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(d)
    return uniq

def choose_device_arg(dev: Dict[str, str]) -> str:
    """Return dshow input arg string: alt(moniker) preferred, fallback to display name."""
    alt = (dev.get("alt") or "").strip()
    if alt:
        return f"audio={alt}"
    disp = (dev.get("display") or "").strip()
    if disp:
        return f"audio={disp}"
    return ""

def pick_virtual_audio(devs: List[Dict[str, str]]) -> Optional[Dict[str, str]]:
    """Heuristic: prefer VB-CABLE or VoiceMeeter 'Output' devices for system routing."""
    lowers = { (d.get("display") or "").lower(): d for d in devs }
    # 1) exact candidates
    for cand in VAC_CANDIDATES:
        d = lowers.get(cand.lower())
        if d:
            return d
    # 2) keyword match in display
    for d in devs:
        s = (d.get("display") or "").lower()
        if any(k in s for k in ["vb-audio", "virtual cable", "voicemeeter"]):
            return d
    # 3) keyword match in alternative name
    for d in devs:
        s = (d.get("alt") or "").lower()
        if any(k in s for k in ["vb-audio", "virtual cable", "voicemeeter"]):
            return d
    return None

def pick_by_name(devs: List[Dict[str, str]], target: str) -> Optional[Dict[str, str]]:
    """Find device by full/partial match in display or alternative name."""
    t = (target or "").strip().lower()
    if not t:
        return None
    for d in devs:
        if (d.get("display") or "").lower() == t:
            return d
    for d in devs:
        if t in (d.get("display") or "").lower():
            return d
    for d in devs:
        alt = (d.get("alt") or "").lower()
        if alt == t or t in alt:
            return d
    return None
