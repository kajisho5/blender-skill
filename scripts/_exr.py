"""Read an OpenEXR file's header (stdlib only): the channel list, the data window and the
compression, so render.py can list the passes of a multilayer EXR it wrote without Blender,
OpenImageIO or numpy.

A multilayer EXR written by Blender names each channel `<view layer>.<pass>.<channel>`
(`ViewLayer.AO.R`, `ViewLayer.Combined.A`, `ViewLayer.IndexOB.X` on 4.x; Blender 5.x spells
some passes out -- `ViewLayer.Ambient Occlusion.R`, `ViewLayer.Object Index.X` -- and writes one
EXR part per pass); `layers()` groups them by `<view layer>.<pass>`. Only the header is read, so every compression Blender writes (ZIP, PIZ,
DWAA, none, ...) works the same: the header itself is never compressed.

`read_scanlines()` also decodes the pixels of an *uncompressed* scanline EXR (compression NONE),
which is what render.py writes as the intermediate for per-pass PNGs; it needs numpy and so runs
only inside Blender's Python.
"""
from __future__ import annotations

import struct
from pathlib import Path
from typing import Any, Dict, List, Tuple

MAGIC = 20000630
PIXEL_TYPES = {0: "uint", 1: "half", 2: "float"}
PIXEL_SIZES = {0: 4, 1: 2, 2: 4}
COMPRESSIONS = {0: "none", 1: "rle", 2: "zips", 3: "zip", 4: "piz", 5: "pxr24", 6: "b44", 7: "b44a",
                8: "dwaa", 9: "dwab", 10: "htj2k"}


def _cstr(data: bytes, pos: int) -> Tuple[str, int]:
    end = data.index(b"\x00", pos)
    return data[pos:end].decode("utf-8", "replace"), end + 1


def _read_attributes(data: bytes, pos: int) -> Tuple[Dict[str, Any], int]:
    """One header's attributes, up to and past its terminating null byte."""
    header: Dict[str, Any] = {}
    while True:
        name, pos = _cstr(data, pos)
        if not name:
            return header, pos
        kind, pos = _cstr(data, pos)
        size = struct.unpack("<i", data[pos:pos + 4])[0]
        pos += 4
        value = data[pos:pos + size]
        pos += size
        if kind == "chlist":
            channels, cpos = [], 0
            while value[cpos:cpos + 1] != b"\x00":
                cname, cpos = _cstr(value, cpos)
                ptype = struct.unpack("<i", value[cpos:cpos + 4])[0]
                cpos += 16  # pixel type, pLinear + 3 reserved bytes, xSampling, ySampling
                channels.append({"name": cname, "type": PIXEL_TYPES.get(ptype, str(ptype))})
            header["channels"] = channels
        elif kind == "box2i" and name == "dataWindow":
            header["data_window"] = list(struct.unpack("<iiii", value))
        elif kind == "compression":
            header["compression"] = COMPRESSIONS.get(value[0], str(value[0]))
        elif kind == "int" and name == "chunkCount":
            header["chunk_count"] = struct.unpack("<i", value)[0]
        elif kind == "string" and name in ("name", "type"):
            header[name] = value.decode("utf-8", "replace")


def read_header(path: str) -> Dict[str, Any]:
    """The file's header: `channels` (list of {name, type}, every part's), `data_window`
    (xmin, ymin, xmax, ymax), `width`, `height`, `compression`, `parts` (one header per part:
    Blender 4.x writes a multilayer EXR as one part, Blender 5.x as one part per pass) and
    `header_end` (byte offset of the chunk offset tables). Raises ValueError for a file that is
    not a scanline EXR."""
    data = Path(path).read_bytes()
    if len(data) < 8 or struct.unpack("<i", data[:4])[0] != MAGIC:
        raise ValueError(f"{path} is not an OpenEXR file")
    flags = struct.unpack("<I", data[4:8])[0]
    multipart = bool(flags & 0x1000)
    if flags & 0x200 or flags & 0x800:
        raise ValueError(f"{path}: only scanline EXRs are read, not tiled or deep (version flags {flags:#x})")
    pos = 8
    parts = []
    while True:
        part, pos = _read_attributes(data, pos)
        if not part:
            break  # the empty header that ends a multi-part file's header list
        if "channels" not in part or "data_window" not in part:
            raise ValueError(f"{path}: EXR header has no channel list or data window")
        xmin, ymin, xmax, ymax = part["data_window"]
        part.update(width=xmax - xmin + 1, height=ymax - ymin + 1)
        parts.append(part)
        if not multipart:
            break
    if not parts:
        raise ValueError(f"{path}: EXR has no header")
    first = parts[0]
    return {"path": str(path), "channels": [ch for part in parts for ch in part["channels"]],
            "data_window": first["data_window"], "width": first["width"], "height": first["height"],
            "compression": first.get("compression"), "multipart": multipart, "parts": parts, "header_end": pos}


def layers(channels: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """`{"ViewLayer.AO": ["B", "G", "R"], ...}` -- each channel name split at its last dot (a
    channel with no dot, as in a single-layer EXR, belongs to the layer "")."""
    grouped: Dict[str, List[str]] = {}
    for ch in channels:
        layer, _, comp = ch["name"].rpartition(".")
        grouped.setdefault(layer, []).append(comp)
    return grouped


def read_scanlines(path: str):
    """Pixels of an uncompressed scanline EXR (single- or multi-part) as `{channel name:
    float32 array (height, width)}`, row 0 at the top. Needs numpy (Blender's Python has it)."""
    import numpy as np
    header = read_header(path)
    data = Path(path).read_bytes()
    dtypes = {"half": np.dtype("<f2"), "float": np.dtype("<f4"), "uint": np.dtype("<u4")}
    out = {}
    pos = header["header_end"]
    tables = []
    for part in header["parts"]:
        if part.get("compression") != "none":
            raise ValueError(f"{path}: only an uncompressed EXR is decoded here (got {part.get('compression')})")
        count = part.get("chunk_count", part["height"])  # NONE: one scanline per chunk
        tables.append(struct.unpack(f"<{count}Q", data[pos:pos + 8 * count]))
        pos += 8 * count
    for index, (part, offsets) in enumerate(zip(header["parts"], tables)):
        width, ymin = part["width"], part["data_window"][1]
        planes = {ch["name"]: np.zeros((part["height"], width), dtype=np.float32) for ch in part["channels"]}
        for off in offsets:
            if header["multipart"]:
                off += 4  # the chunk's part number
            y = struct.unpack("<i", data[off:off + 4])[0]
            cur = off + 8  # y, then the data size
            for ch in part["channels"]:  # stored in the channel list's (alphabetical) order
                dt = dtypes[ch["type"]]
                planes[ch["name"]][y - ymin] = np.frombuffer(data, dtype=dt, count=width, offset=cur).astype(np.float32)
                cur += width * dt.itemsize
        out.update(planes)
    return out
