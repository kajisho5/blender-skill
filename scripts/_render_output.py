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

`resolve_resolution()` turns `--resolution-preset NAME` / `--resolution W H` into the frame size
(RM-058): an explicit W H wins over a preset, a preset wins over the 512x512 default; an unknown
preset name is a SkillError(kind="input") listing the valid ones (docs/decisions.md ADR-1).

Alpha mode and render passes (docs/decisions.md ADR-2): every alpha-carrying result states how
its colour is stored, `alpha.mode` = `straight` | `premultiplied` -- the same two words
color-grading-skill reads as `project.source.alpha_mode`. Blender writes a PNG and a ProRes 4444 /
QTRLE .mov straight and a multilayer EXR premultiplied; `--alpha-mode premultiplied` on a PNG
multiplies the colour by the alpha after the render, any other mismatch is refused.
`--passes` adds shadow / ao / mist / object_index next to the beauty, as one multilayer EXR per
frame (`-o NAME.exr`) or as one PNG per pass per frame (`-o NAME.png`, `NAME.<pass>.png`).
"""
from __future__ import annotations

import json
import re
import shutil
import struct
import subprocess
import zlib
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

import _exr
from _common import SkillError

VIDEO_EXTS = (".mp4", ".mov")
ALPHA_MODES = ("straight", "premultiplied")

# Render passes `--passes` can ask for: the view-layer property that turns each on, the layer names
# Blender gives it in a multilayer EXR (4.x `AO` / `IndexOB`, 5.x `Ambient Occlusion` /
# `Object Index`), and the engines that produce it. Checked on Blender 4.5:
# Eevee writes Shadow but no IndexOB; Cycles writes IndexOB but no Shadow (Cycles' shadow catcher
# is a different pass, not this one). A pass the engine does not write is refused, not dropped.
PASSES: Dict[str, Dict[str, Any]] = {
    "beauty": {"property": None, "exr_layer": ("Combined",), "engines": ("eevee", "cycles"), "png": "display RGBA, 8-bit"},
    "shadow": {"property": "use_pass_shadow", "exr_layer": ("Shadow",), "engines": ("eevee",), "png": "linear RGB, 16-bit"},
    "ao": {"property": "use_pass_ambient_occlusion", "exr_layer": ("AO", "Ambient Occlusion"), "engines": ("eevee", "cycles"), "png": "linear RGB, 16-bit"},
    "mist": {"property": "use_pass_mist", "exr_layer": ("Mist",), "engines": ("eevee", "cycles"), "png": "linear gray, 16-bit"},
    "object_index": {"property": "use_pass_object_index", "exr_layer": ("IndexOB", "Object Index"), "engines": ("cycles",),
                     "png": "gray, 16-bit, the index itself (0 = background)"},
}
# Where each alpha-capable container stores its colour when Blender writes it. PNG is straight by
# its own specification; Blender's FFmpeg writer encodes the same straight 8-bit buffer (a ProRes
# 4444 frame decoded back matches the PNG of that frame to +-4 levels at alpha < 1, measured on
# 4.5); OpenEXR is premultiplied by convention and Blender writes it so.
NATIVE_ALPHA_MODE = {"png": "straight", "video": "straight", "exr": "premultiplied"}

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


# Named frame sizes for --resolution-preset (RM-058). Each is the size of one rendered frame: a
# thumbnail, every turntable frame/video frame, and every tile of a --sheet (which is 2x2 tiles).
# All dimensions are even, so a preset is always valid for H.264's 4:2:0 chroma subsampling.
RESOLUTION_PRESETS: Dict[str, Tuple[int, int]] = {
    "thumbnail": (512, 512),   # render.py's own default size
    "square": (1080, 1080),    # 1:1 social feed
    "720p": (1280, 720),       # HD
    "1080p": (1920, 1080),     # Full HD
    "4k": (3840, 2160),        # UHD-1 (consumer 4K), not DCI 4096x2160
    "vertical": (1080, 1920),  # 9:16 short-form / stories
}
DEFAULT_RESOLUTION_PRESET = "thumbnail"


def resolve_resolution(preset: Optional[str] = None,
                       explicit: Optional[Sequence[int]] = None) -> Dict[str, Any]:
    """The frame size for one render: `{"width", "height", "preset", "source"}`. `explicit`
    (--resolution W H) wins over `preset` (--resolution-preset NAME), which wins over the
    default; when both are given the overridden preset is reported as `overridden_preset`.
    Preset names are matched case-insensitively. Raises SkillError(kind="input") for an
    unknown preset name or a non-positive explicit size."""
    name = preset.strip().lower() if preset is not None else None
    if name is not None and name not in RESOLUTION_PRESETS:
        raise SkillError(f"unknown resolution preset: {preset!r}", kind="input",
                         hint="valid presets: " + ", ".join(RESOLUTION_PRESETS)
                         + "; or give --resolution W H")
    if explicit is not None:
        width, height = (int(v) for v in explicit)
        if width <= 0 or height <= 0:
            raise SkillError(f"--resolution needs two positive integers (got {width} {height})", kind="input")
        result: Dict[str, Any] = {"width": width, "height": height, "preset": None, "source": "explicit"}
        if name is not None:
            result["overridden_preset"] = name
        return result
    source = "preset" if name is not None else "default"
    name = name or DEFAULT_RESOLUTION_PRESET
    width, height = RESOLUTION_PRESETS[name]
    return {"width": width, "height": height, "preset": name, "source": source}


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


def parse_passes(passes: Optional[Any]) -> list:
    """`--passes` (a comma-separated string or a list) -> the pass names in PASSES order, without
    `beauty` (always written). Raises SkillError(kind="input") for an unknown name."""
    if passes is None:
        return []
    items = passes.split(",") if isinstance(passes, str) else list(passes)
    names = [str(n).strip().lower() for n in items if str(n).strip()]
    unknown = [n for n in names if n not in PASSES]
    if unknown:
        raise SkillError(f"unknown render pass: {', '.join(unknown)}", kind="input",
                         hint="valid passes: " + ", ".join(PASSES))
    return [n for n in PASSES if n in names and n != "beauty"]


def _seq_printf(blender_path: str) -> str:
    p = Path(blender_path)
    return str(Path(str(p.parent).replace("%", "%%")) / printf_pattern(p.name))


def _pass_png_path(beauty: str, name: str) -> str:
    p = Path(beauty)
    return str(p.with_name(p.stem + "." + name + p.suffix))


def output_settings(mode: str, output: str, *, transparent: bool = False, fps: int = 24,
                    frames: int = 24, alpha_mode: Optional[str] = None, passes: Optional[Any] = None,
                    engine: str = "eevee") -> Dict[str, Any]:
    """The render output settings for one render.py run: `_base_output_settings` (PNG / video),
    or a multilayer EXR for an `.exr` output, plus the alpha mode the result states and, when
    passes / an EXR / a premultiplied PNG are asked for, the per-frame `frame_plan` bpy/render.py
    follows. Raises SkillError(kind="input") for an unknown pass, a pass this engine does not
    write, passes on a sheet or a video, and an alpha mode the output cannot be written in."""
    ext = Path(output).suffix.lower()
    wanted = parse_passes(passes)
    if alpha_mode is not None and alpha_mode not in ALPHA_MODES:
        raise SkillError(f"unknown alpha mode: {alpha_mode!r}", kind="input", hint="valid: " + ", ".join(ALPHA_MODES))
    if (wanted or ext == ".exr") and mode == "sheet":
        raise SkillError("--passes and .exr output are for a thumbnail or a turntable, not a --sheet", kind="input")
    if wanted and ext not in (".exr", ".png"):
        raise SkillError(f"--passes needs an .exr (multilayer) or .png (one file per pass) output (got {ext or 'no extension'})",
                         kind="input", hint="use -o NAME.exr, or -o NAME.png / 'NAME_####.png' for a turntable")
    unsupported = [n for n in wanted if engine not in PASSES[n]["engines"]]
    if unsupported:
        other = "cycles" if engine == "eevee" else "eevee"
        raise SkillError(f"the {engine} engine does not write the {', '.join(unsupported)} pass", kind="input",
                         hint=", ".join(f"{n}: {'/'.join(PASSES[n]['engines'])}" for n in unsupported)
                         + f" -- render with {'--cycles' if other == 'cycles' else 'the Eevee default'} or drop the pass")

    if ext == ".exr":
        target = output if mode != "turntable" else sequence_path(output)
        settings: Dict[str, Any] = {"transparent": transparent, "film_transparent": True,
                                    "kind": "still" if mode != "turntable" else "image_sequence", "filepath": target,
                                    "image_settings": {"file_format": "OPEN_EXR_MULTILAYER", "color_depth": "32",
                                                       "exr_codec": "ZIP"}}
        if mode == "turntable":
            settings["sequence"] = _sequence_handoff(target, fps, frames)
            del settings["sequence"]["ffmpeg_skill_sequence_argv"]  # sequence.py encodes 8-bit H.264 frames
        container = "exr"
    else:
        settings = _base_output_settings(mode, output, transparent=transparent, fps=fps, frames=frames)
        container = "video" if settings["kind"] == "video" else "png"

    native = NATIVE_ALPHA_MODE[container]
    if alpha_mode is not None and not (transparent or container == "exr"):
        raise SkillError("--alpha-mode needs --transparent (or an .exr output, which always carries alpha)", kind="input")
    if alpha_mode is not None and alpha_mode != native and container != "png":
        hint = ("an EXR is written premultiplied; use -o NAME.png for straight alpha" if container == "exr"
                else "Blender writes a .mov straight; use -o NAME.exr or a .png sequence for premultiplied alpha")
        raise SkillError(f"a {'.exr' if container == 'exr' else '.mov'} cannot be written with {alpha_mode} alpha here",
                         kind="input", hint=hint)
    if transparent or container == "exr":
        settings["alpha_mode"] = {"mode": alpha_mode or native, "source": "request" if alpha_mode else "default",
                                  "container": container}
    premultiply = container == "png" and alpha_mode == "premultiplied"
    if wanted or container == "exr" or premultiply:
        settings["frame_plan"] = _frame_plan(mode, settings, container, wanted, premultiply, fps, frames)
    return settings


def _frame_plan(mode, settings, container, wanted, premultiply, fps, frames) -> Dict[str, Any]:
    """What bpy/render.py writes per frame when it renders frame by frame (passes, an EXR, a
    premultiplied PNG): paths as printf patterns for a sequence, plain paths for a still."""
    seq = mode == "turntable"
    beauty = settings["filepath"]
    plan: Dict[str, Any] = {
        "sequence": seq, "frames": list(range(1, frames + 1)) if seq else [1],
        "passes": wanted, "properties": {n: PASSES[n]["property"] for n in wanted},
        "premultiply_png": premultiply,
    }
    fmt = (lambda p: _seq_printf(p)) if seq else (lambda p: p)
    if container == "exr":
        plan["beauty"] = None
        plan["exr"] = {"path": fmt(beauty), "keep": True, "image_settings": dict(settings["image_settings"])}
        plan["pass_pngs"] = {}
    else:
        plan["beauty"] = {"path": fmt(beauty), "image_settings": dict(settings["image_settings"])}
        tmp = str(Path(beauty).with_suffix(".passes.exr"))
        plan["exr"] = ({"path": fmt(tmp), "keep": False,
                        "image_settings": {"file_format": "OPEN_EXR_MULTILAYER", "color_depth": "32", "exr_codec": "NONE"}}
                       if wanted else None)
        plan["pass_pngs"] = {n: fmt(_pass_png_path(beauty, n)) for n in wanted}
        if seq and wanted:
            settings["pass_sequences"] = {n: _sequence_handoff(_pass_png_path(beauty, n), fps, frames) for n in wanted}
    return plan


def _base_output_settings(mode: str, output: str, *, transparent: bool = False, fps: int = 24,
                          frames: int = 24) -> Dict[str, Any]:
    """PNG / video output settings. Raises SkillError(kind="input") when `transparent` is asked
    of a format that cannot carry alpha."""
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


def decode_png(path: str) -> Tuple[int, int, int, list]:
    """Decode a non-interlaced 8-bit gray/RGB/RGBA PNG: (width, height, channels, rows), each row
    a bytearray of width * channels samples, row 0 at the top. Stdlib only, all five filter types."""
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
    rows = []
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
        rows.append(line)
        prev = line
    return width, height, channels, rows


def png_alpha(path: str) -> Dict[str, Any]:
    """Decode an 8-bit PNG and report its alpha channel: `has_alpha` (color type 6 = RGBA),
    `min`/`max` alpha over every pixel, and `transparent_pixels` (alpha == 0). A PNG without an
    alpha channel reports has_alpha False and min/max 255."""
    width, height, channels, rows = decode_png(path)
    has_alpha = channels in (2, 4)
    lo, hi, zero = 255, 0, 0
    if has_alpha:
        for line in rows:
            alphas = line[channels - 1::channels]
            lo, hi = min(lo, min(alphas)), max(hi, max(alphas))
            zero += alphas.count(0)
    else:
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


def exr_passes_report(path: str, wanted: Sequence[str]) -> Dict[str, Any]:
    """Read a written multilayer EXR's header back and list its passes: every layer in the file
    (`layers`), each requested pass (beauty first) with the layer and channels it was found
    under, the ones `missing`, and `verified` when none is. `has_alpha` is the beauty's A
    channel."""
    header = _exr.read_header(path)
    grouped = _exr.layers(header["channels"])
    found: Dict[str, Any] = {}
    missing = []
    for name in ["beauty"] + [n for n in wanted if n != "beauty"]:
        names = PASSES[name]["exr_layer"]
        match = next((full for full in sorted(grouped) if full.rpartition(".")[2] in names), None)
        if match is None:
            missing.append(name)
        else:
            found[name] = {"layer": match, "channels": sorted(grouped[match])}
    beauty = found.get("beauty", {})
    return {"path": str(path), "width": header["width"], "height": header["height"],
            "compression": header.get("compression"), "layers": sorted(grouped), "passes": found,
            "missing": missing, "verified": not missing, "has_alpha": "A" in beauty.get("channels", [])}


def with_alpha_mode(report: Dict[str, Any], alpha_mode: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Add the stated alpha mode to an `alpha` block: `mode` (straight | premultiplied, the
    value to send as color-grading-skill's `project.source.alpha_mode`) and `mode_source`
    (request | default)."""
    if alpha_mode:
        report["mode"] = alpha_mode["mode"]
        report["mode_source"] = alpha_mode["source"]
        if alpha_mode["mode"] == "premultiplied" and alpha_mode.get("container") == "png":
            report["note"] = ("PNG readers take a PNG's alpha as straight: pass alpha_mode premultiplied "
                              "along with this file (the PNG itself cannot say so)")
    return report


def png_header(path: str) -> Dict[str, Any]:
    """Width, height, bit depth and channel count of a PNG, from its IHDR chunk."""
    data = Path(path).read_bytes()[:29]
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} is not a PNG")
    width, height, depth, ctype = struct.unpack(">IIBB", data[16:26])
    return {"width": width, "height": height, "bit_depth": depth,
            "channels": {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(ctype, 0)}


def png_passes_report(beauty_frame: str, wanted: Sequence[str], exr_layers: Sequence[str]) -> Dict[str, Any]:
    """`passes` block for per-pass PNGs: each requested pass's first-frame file read back
    (size, bit depth, channels) next to the beauty, its encoding, and `verified` when every file
    is there at the beauty's size. `exr_layers` are the layers of the intermediate EXR the PNGs
    were cut from (bpy/render.py reads them before deleting it)."""
    found: Dict[str, Any] = {}
    missing = []
    try:
        size = png_header(beauty_frame)
        found["beauty"] = dict(size, path=str(beauty_frame), encoding=PASSES["beauty"]["png"])
    except (OSError, ValueError):
        size = None
        missing.append("beauty")
    for name in wanted:
        path = _pass_png_path(beauty_frame, name)
        try:
            info = png_header(path)
        except (OSError, ValueError):
            missing.append(name)
            continue
        if size and (info["width"], info["height"]) != (size["width"], size["height"]):
            missing.append(name)
        found[name] = dict(info, path=path, encoding=PASSES[name]["png"])
    return {"passes": found, "missing": missing, "verified": not missing, "layers": list(exr_layers)}
