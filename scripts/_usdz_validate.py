#!/usr/bin/env python3
"""Validate a .usdz file's own binary package structure -- the real, documented requirements
Apple's USDZ spec places on the zip container itself (not on the USD scene content, which
info.py/check.py's other rows already cover): every entry must be stored, never Deflate-
compressed, and each entry's file data must start at a 64-byte-aligned offset (so an AR viewer
can mmap the archive and read USD/image data directly without an unaligned-access penalty).
Apple's own `usdzip`/Reality Converter tools reject a package that violates either rule; Quick
Look can fail to load, or fail to load efficiently, on both AR Quick Look and non-AR use.

Read directly from the file's own zip structure (stdlib `zipfile`/`struct`), independent of
Blender -- this is purely about what convert.py's own USDZ export actually wrote to disk, not
about the USD scene content.
"""
from __future__ import annotations

import struct
import zipfile
from pathlib import Path
from typing import Optional


def validate(path: str) -> Optional[dict]:
    """Returns None if this isn't even a valid zip container (corrupt / not actually a usdz);
    otherwise a dict with `valid`, `issues` (list of str), and `entries` (per-file compression/
    alignment facts) so a caller can show real numbers, not just PASS/FAIL.
    """
    p = Path(path)
    try:
        with open(p, "rb") as f:
            raw = f.read()
        with zipfile.ZipFile(p) as z:
            bad = z.testzip()
            if bad is not None:
                return None
            infos = z.infolist()
    except (OSError, zipfile.BadZipFile):
        return None

    issues = []
    entries = []
    for info in infos:
        off = info.header_offset
        # Local file header: 30 fixed bytes, then filename, then the extra field.
        try:
            fname_len, extra_len = struct.unpack("<HH", raw[off + 26:off + 30])
        except struct.error:
            issues.append(f"{info.filename}: local file header truncated/corrupt")
            continue
        data_start = off + 30 + fname_len + extra_len
        stored = info.compress_type == zipfile.ZIP_STORED
        aligned = (data_start % 64) == 0
        if not stored:
            issues.append(f"{info.filename}: compressed (Deflate), not stored -- USDZ requires uncompressed entries")
        if not aligned:
            issues.append(f"{info.filename}: file data starts at offset {data_start}, not 64-byte-aligned")
        entries.append({"name": info.filename, "stored": stored, "data_offset": data_start, "aligned": aligned})

    return {"valid": not issues, "issues": issues, "entries": entries}
