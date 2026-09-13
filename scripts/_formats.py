#!/usr/bin/env python3
"""Format detection by file extension. The bpy-side import/export operator for each format
lives in scripts/bpy/_compat.py (it varies by Blender version); this module only says which
logical format a path is, so host scripts can validate before ever launching Blender.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

# Logical format name -> extensions that mean it. ".blend" is Blender's own native format.
FORMATS = {
    "gltf": [".glb", ".gltf"],
    "fbx": [".fbx"],
    "obj": [".obj"],
    "stl": [".stl"],
    "usd": [".usd", ".usda", ".usdc"],
    "usdz": [".usdz"],
    "ply": [".ply"],
    "abc": [".abc"],
    "blend": [".blend"],
}

EXT_TO_FORMAT = {ext: fmt for fmt, exts in FORMATS.items() for ext in exts}

IMPORTABLE = set(FORMATS)          # every format above can be read
EXPORTABLE = set(FORMATS)          # and written


def detect_format(path: str) -> Optional[str]:
    return EXT_TO_FORMAT.get(Path(path).suffix.lower())


def known_extensions() -> list:
    return sorted(EXT_TO_FORMAT)
