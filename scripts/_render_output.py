"""render.py's output settings, decided on the host side (no bpy) so they can be unit-tested
without Blender and shown by `--dry-run`.

`output_settings()` turns (mode, output path, --transparent, fps, frames) into a plain dict
that scripts/bpy/render.py applies as-is: film_transparent, the image_settings
(file_format/color_mode/color_depth), the FFmpeg container/codec for a video, the path Blender
writes to, and -- for an image sequence -- the printf pattern ffmpeg-skill's `sequence.py`
takes. `png_alpha()` reads a written PNG back (stdlib zlib/struct only) to verify the alpha
channel on a real output frame.

Alpha-capable outputs, the only ones `--transparent` accepts:
  - a still (thumbnail/sheet): 8-bit RGBA PNG
  - a turntable as an image sequence: 8-bit RGBA PNGs, `<name>_####.png`
  - a turntable as a video: QuickTime .mov, ProRes 4444 (yuva444p10le), the standard
    alpha-carrying intermediate for compositing/NLE handoff -- or, on a Blender build without
    a ProRes encoder (4.2 LTS), QuickTime Animation (QTRLE, argb), with a warning.
H.264/MP4 has no alpha channel, so `--transparent -o x.mp4` is refused instead of silently
dropping the alpha.
"""
from __future__ import annotations

import json
import re
import shutil
import struct
import subprocess
import zlib
from pathlib import Path
from typing import Any, Dict, Optional

from _common import SkillError

VIDEO_EXTS = (".mp4", ".mov")

_PNG_RGBA = {"file_format": "PNG", "color_mode": "RGBA", "color_depth": "8"}
# ProRes 4444 in a QuickTime container. `ffmpeg_prores_profile` exists only on Blender builds
# that expose the profile choice; older ones pick 4444 (yuva444p10le) by themselves when the
# color mode is RGBA, so the bpy side sets it only if the property is there.
_PRORES_4444 = {"format": "QUICKTIME", "codec": "PRORES", "ffmpeg_prores_profile": "4444"}
_H264_MP4 = {"format": "MPEG4", "codec": "H264"}
# Blender 4.2 LTS has no PRORES codec at all (confirmed in CI: `enum "PRORES" not found`; 4.5
# has it). QuickTime Animation (QTRLE, lossless RGBA, argb) is the alpha-carrying .mov such a
# build does have; bpy/render.py moves to it only when PRORES is refused, and says so.
_QTRLE_MOV = {"format": "QUICKTIME", "codec": "QTRLE"}


def sequence_path(output: str) -> str:
    """The path Blender writes a PNG sequence to: `####` marks the frame number. A path with no
    `#` gets `_####` before its extension -- left alone, Blender would append the frame number
    after the extension (`turn.png0001.png`)."""
    p = Path(output)
    if "#" in p.name:
        return output
    return str(p.with_name(p.stem + "_####" + (p.suffix or ".png")))


def printf_pattern(blender_name: str) -> str:
    """`turn_####.png` -> `turn_%04d.png` (the last run of `#`, which is the one Blender fills)."""
    runs = list(re.finditer(r"#+", blender_name))
    if not runs:
        raise ValueError(f"no '#' frame marker in {blender_name!r}")
    last = runs[-1]
    return blender_name[:last.start()] + f"%0{len(last.group())}d" + blender_name[last.end():]


def _sequence_handoff(blender_path: str, fps: int, frames: int, start: int = 1) -> Dict[str, Any]:
    p = Path(blender_path)
    pattern = printf_pattern(p.name)
    return {
        "dir": str(p.parent), "pattern": pattern, "start_number": start, "fps": fps, "frames": frames,
        "first_frame": str(p.parent / (pattern % start)),
        # ffmpeg-skill's sequence.py interface (--dir/--pattern/--fps/--start-number); it encodes
        # H.264, so the alpha is flattened there -- composite from these frames to keep it.
        "ffmpeg_skill_sequence_argv": ["--dir", str(p.parent), "--pattern", pattern,
                                       "--fps", str(fps), "--start-number", str(start)],
    }


def output_settings(mode: str, output: str, *, transparent: bool = False, fps: int = 24,
                    frames: int = 24) -> Dict[str, Any]:
    """The render output settings for one render.py run. Raises SkillError(kind="input") when
    `transparent` is asked of a format that cannot carry alpha."""
    ext = Path(output).suffix.lower()
    settings: Dict[str, Any] = {"transparent": transparent, "film_transparent": True}

    if mode in ("thumbnail", "sheet"):
        if transparent and ext != ".png":
            raise SkillError(f"--transparent needs a .png output for a {mode} (got {ext or 'no extension'})",
                             kind="input", hint="PNG is the alpha-capable still format here")
        settings.update(kind="still", filepath=output,
                        image_settings=dict(_PNG_RGBA) if transparent else {"file_format": "PNG"})
        return settings

    if mode != "turntable":
        raise ValueError(f"unknown render mode {mode!r}")

    if ext in VIDEO_EXTS:
        if transparent and ext == ".mp4":
            raise SkillError("--transparent cannot write .mp4: H.264 has no alpha channel", kind="input",
                             hint="use -o NAME.mov (ProRes 4444) or -o 'NAME_####.png' (PNG sequence)")
        video = dict(_PRORES_4444) if transparent else dict(_H264_MP4)
        fallback = sequence_path(str(Path(output).with_suffix(".png")))
        settings.update(
            kind="video", filepath=output,
            image_settings={"file_format": "FFMPEG", "color_mode": "RGBA"} if transparent else {"file_format": "FFMPEG"},
            ffmpeg=video,
            ffmpeg_alternatives=[dict(_QTRLE_MOV)] if transparent else [],
            # used only if this Blender build has no FFmpeg support (see bpy/render.py)
            fallback={"filepath": fallback,
                      "image_settings": dict(_PNG_RGBA) if transparent else {"file_format": "PNG"},
                      "sequence": _sequence_handoff(fallback, fps, frames)},
        )
        return settings

    if transparent and ext not in (".png", ""):
        raise SkillError(f"--transparent turntable needs .mov or a .png sequence (got {ext})", kind="input",
                         hint="use -o NAME.mov (ProRes 4444) or -o 'NAME_####.png' (PNG sequence)")
    blender_path = sequence_path(output if ext else output + ".png")
    settings.update(kind="image_sequence", filepath=blender_path,
                    image_settings=dict(_PNG_RGBA) if transparent else {"file_format": "PNG"},
                    sequence=_sequence_handoff(blender_path, fps, frames))
    return settings


def png_alpha(path: str) -> Dict[str, Any]:
    """Decode an 8-bit PNG and report its alpha channel: `has_alpha` (color type 6 = RGBA),
    `min`/`max` alpha over every pixel, and `transparent_pixels` (alpha == 0). A PNG without an
    alpha channel reports has_alpha False and min/max 255. Stdlib only, all five filter types."""
    data = Path(path).read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} is not a PNG")
    width, height, depth, ctype, _comp, _filt, interlace = struct.unpack(">IIBBBBB", data[16:29])
    channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(ctype)
    if depth != 8 or channels is None or interlace:
        raise ValueError(f"{path}: only non-interlaced 8-bit gray/RGB/RGBA PNGs are decoded "
                         f"(bit depth {depth}, color type {ctype}, interlace {interlace})")
    pos, idat = 8, bytearray()
    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        if data[pos + 4:pos + 8] == b"IDAT":
            idat += data[pos + 8:pos + 8 + length]
        pos += 12 + length
    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    prev = bytearray(stride)
    has_alpha = ctype in (4, 6)
    lo, hi, zero = 255, 0, 0
    for y in range(height):
        start = y * (stride + 1)
        ftype, line = raw[start], bytearray(raw[start + 1:start + 1 + stride])
        for i in range(stride):
            a = line[i - channels] if i >= channels else 0
            b = prev[i]
            c = prev[i - channels] if i >= channels else 0
            if ftype == 1:
                line[i] = (line[i] + a) & 0xFF
            elif ftype == 2:
                line[i] = (line[i] + b) & 0xFF
            elif ftype == 3:
                line[i] = (line[i] + (a + b) // 2) & 0xFF
            elif ftype == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else (b if pb <= pc else c))) & 0xFF
        if has_alpha:
            alphas = line[channels - 1::channels]
            lo, hi = min(lo, min(alphas)), max(hi, max(alphas))
            zero += alphas.count(0)
        prev = line
    if not has_alpha:
        lo = hi = 255
    return {"path": str(path), "width": width, "height": height, "has_alpha": has_alpha,
            "min": lo, "max": hi, "transparent_pixels": zero}


def alpha_report(frame: Optional[str]) -> Dict[str, Any]:
    """`alpha` block of render.py's --transparent result for a PNG frame: verified when the frame
    is RGBA and actually has transparent pixels (the empty background) next to opaque ones."""
    if not frame or not Path(frame).exists():
        return {"verified": False, "reason": f"output frame not found: {frame}"}
    info = png_alpha(frame)
    info["verified"] = bool(info["has_alpha"] and info["min"] < 255)
    if not info["verified"]:
        info["reason"] = "no alpha channel" if not info["has_alpha"] else "every pixel is opaque"
    return info


def video_alpha_report(path: str) -> Dict[str, Any]:
    """`alpha` block for a video: the stream's pixel format via ffprobe (yuva*/rgba/argb... carry
    alpha). Not verified -- with the reason -- when ffprobe is not installed; this skill does not
    require FFmpeg, Blender encodes the video itself."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {"verified": False, "reason": "ffprobe not found; pixel format not checked"}
    proc = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                           "stream=codec_name,profile,pix_fmt", "-of", "json", str(path)],
                          capture_output=True, text=True)
    streams = (json.loads(proc.stdout or "{}").get("streams") or [{}]) if proc.returncode == 0 else [{}]
    stream = streams[0]
    pix_fmt = stream.get("pix_fmt", "")
    report = {"path": str(path), "codec": stream.get("codec_name"), "profile": stream.get("profile"),
              "pix_fmt": pix_fmt, "verified": bool(re.match(r"(yuva|rgba|argb|bgra|abgr|gbrap|ya)", pix_fmt))}
    if not report["verified"]:
        report["reason"] = f"pixel format {pix_fmt or 'unknown'} has no alpha channel"
    return report
