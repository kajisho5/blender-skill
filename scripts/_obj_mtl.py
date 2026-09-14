#!/usr/bin/env python3
"""Parse a .obj file's referenced .mtl material library directly (stdlib only, no Blender) and
report the same Ns (Phong specular exponent) -> PBR roughness heuristic Blender's own OBJ
importer applies, so an agent can see the conversion an .obj/.mtl file is about to go through
before committing to it -- OBJ/MTL predates physically-based rendering and has no metallic or
roughness channel at all, only the old Phong/Blinn-Phong ambient/diffuse/specular/shininess
model, so any metallic/roughness a PBR pipeline ends up with is necessarily a guess, not
recovered ground truth.

Confirmed by testing on real Blender 4.2.23 (see references/pitfalls.md): `wm.obj_import`'s
resulting Principled BSDF Roughness input reads `1 - sqrt(clamp(Ns, 0, 1000) / 1000)` exactly,
across Ns = 0, 5, 90, 200, 1000. Metallic is always left at 0.0 -- Blender's own importer does
not attempt to guess metallic from Phong parameters either, and neither does this: there is no
reliable signal in Ka/Kd/Ks/Ns alone that distinguishes a metal from a very shiny dielectric, so
guessing would trade a known gap for an unverifiable, often-wrong claim.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Optional


def _find_mtllib(obj_path: Path) -> list:
    names = []
    try:
        with open(obj_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line.startswith("mtllib "):
                    names.extend(line.split()[1:])
    except OSError:
        pass
    return names


def _parse_mtl(mtl_path: Path) -> list:
    materials = []
    current = None
    try:
        with open(mtl_path, "r", encoding="utf-8", errors="replace") as f:
            for raw_line in f:
                parts = raw_line.strip().split()
                if not parts:
                    continue
                key, rest = parts[0], parts[1:]
                if key == "newmtl":
                    current = {"name": rest[0] if rest else None}
                    materials.append(current)
                    continue
                if current is None:
                    continue
                try:
                    if key in ("Ka", "Kd", "Ks") and len(rest) >= 3:
                        current[key] = [float(v) for v in rest[:3]]
                    elif key == "Ns" and rest:
                        current["Ns"] = float(rest[0])
                    elif key == "d" and rest:
                        current["d"] = float(rest[0])
                    elif key == "Tr" and rest:
                        current["Tr"] = float(rest[0])
                    elif key == "illum" and rest:
                        current["illum"] = int(float(rest[0]))
                except ValueError:
                    continue  # a malformed numeric field -- skip that field, keep the rest
    except OSError:
        return []
    return materials


def _heuristic_pbr(mat: dict) -> dict:
    ns = mat.get("Ns")
    roughness = None
    if ns is not None:
        roughness = 1.0 - math.sqrt(max(0.0, min(ns, 1000.0)) / 1000.0)
    alpha = mat.get("d")
    if alpha is None and "Tr" in mat:
        alpha = 1.0 - mat["Tr"]
    return {
        "base_color": mat.get("Kd"),
        "roughness": roughness,
        "metallic": 0.0,  # never guessed -- see this module's docstring
        "alpha": alpha,
    }


def read_materials(obj_path: str) -> Optional[list]:
    """None if the .obj has no mtllib reference or the referenced file(s) can't be read;
    otherwise a list of {"name", "source": {raw Ka/Kd/Ks/Ns/d/Tr/illum as parsed},
    "pbr_heuristic": {base_color/roughness/metallic/alpha}} per material.
    """
    obj = Path(obj_path)
    mtl_names = _find_mtllib(obj)
    if not mtl_names:
        return None
    out = []
    for name in mtl_names:
        mtl_path = obj.parent / name
        if not mtl_path.exists():
            continue
        for mat in _parse_mtl(mtl_path):
            out.append({
                "name": mat.get("name"),
                "source": {k: v for k, v in mat.items() if k != "name"},
                "pbr_heuristic": _heuristic_pbr(mat),
            })
    return out or None
