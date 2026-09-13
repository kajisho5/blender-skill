#!/usr/bin/env python3
"""PASS/WARN/FAIL a file against a delivery target's budget, each row with a suggested fix.

Built entirely on info.py's measurements (no new Blender work of its own) -- the budgets below
are this tool's own conservative defaults, not each platform's official published spec, since
those numbers are unpublished, change over time, or vary per app/importer version; treat a WARN
as "worth checking against the current docs for your target app", not as that target's authority.

Usage:
  python3 scripts/check.py model.glb --target three.js
  python3 scripts/check.py model.usdz --target ios-ar
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common
import _delegate
import _formats
import _run
from _common import SkillError

TARGETS = ["three.js", "unity", "unreal", "godot", "ios-ar", "android-ar", "webxr", "3d-print", "sketchfab"]

# (preferred formats, triangle budget, texture-px budget, require UV, require manifold, transmission-size MB budget or None)
_SPEC = {
    "three.js": (["gltf"], 300_000, 4096, False, False, 15),
    "unity": (["fbx", "gltf"], 250_000, 4096, True, False, None),
    "unreal": (["fbx"], 500_000, 4096, True, False, None),
    "godot": (["gltf"], 300_000, 4096, True, False, 20),
    "ios-ar": (["usdz"], 100_000, 4096, True, True, 25),
    "android-ar": (["gltf"], 100_000, 4096, True, True, 15),
    "webxr": (["gltf"], 150_000, 2048, False, False, 10),
    "3d-print": (["stl", "ply", "obj"], 2_000_000, None, False, True, None),
    "sketchfab": (["gltf", "fbx", "usdz"], 500_000, 8192, False, False, 100),
}


def _size_fix_command(file: str, fmt: str) -> str:
    """The transmission-size row's fix command: gltf-transform's Draco/KTX2 compression when
    it's installed, or this skill's own (coarser) texture-cap/decimate flags when it isn't --
    never silent about which one a reader is actually getting."""
    if fmt != "gltf":
        return f"python3 scripts/optimize.py {file} -o out.{fmt} --texture-max 2048"
    if _delegate.gltf_transform_available():
        return f"python3 scripts/optimize.py {file} -o out.glb --draco --ktx2  # or --meshopt instead of --draco"
    return (f"python3 scripts/optimize.py {file} -o out.glb --texture-max 2048  # "
            f"install gltf-transform ({_delegate.GLTF_TRANSFORM_INSTALL}) for --draco/--meshopt/--ktx2 compression instead")


def _check(info: dict, target: str) -> list:
    fmts, tri_budget, tex_budget, need_uv, need_manifold, size_mb_budget = _SPEC[target]
    rows = []
    fmt = info["format"]
    if fmt in fmts:
        rows.append({"check": "format", "status": "PASS", "detail": f"{fmt} is a recommended format for {target}"})
    else:
        rows.append({"check": "format", "status": "WARN", "detail": f"{fmt} is not among this tool's preferred formats for {target} ({', '.join(fmts)})",
                     "fix": f"python3 scripts/convert.py {info['file']} -o out.{fmts[0]} --verify"})

    tris = info["meshes"]["triangles"]
    if tris <= tri_budget:
        rows.append({"check": "triangle budget", "status": "PASS", "detail": f"{tris} <= {tri_budget}"})
    else:
        rows.append({"check": "triangle budget", "status": "WARN", "detail": f"{tris} > {tri_budget}",
                     "fix": f"python3 scripts/optimize.py {info['file']} -o out.{fmt} --decimate-ratio {tri_budget / tris:.2f}"})

    if tex_budget:
        big = [t for t in info["textures"] if max(t["width"], t["height"]) > tex_budget]
        if big:
            rows.append({"check": "texture size", "status": "WARN",
                         "detail": f"{len(big)} texture(s) exceed {tex_budget}px: {', '.join(t['name'] for t in big)}",
                         "fix": f"python3 scripts/optimize.py {info['file']} -o out.{fmt} --texture-max {tex_budget}"})
        else:
            rows.append({"check": "texture size", "status": "PASS", "detail": f"all textures <= {tex_budget}px"})

    missing_tex = [t["name"] for t in info["textures"] if t["missing"]]
    if missing_tex:
        rows.append({"check": "textures present", "status": "FAIL", "detail": f"missing: {', '.join(missing_tex)}"})
    else:
        rows.append({"check": "textures present", "status": "PASS", "detail": "no missing texture files"})

    if need_uv:
        no_uv = [o["name"] for o in info["meshes"]["per_object"] if not o["has_uv"]]
        if no_uv:
            rows.append({"check": "UV maps", "status": "FAIL", "detail": f"no UV map: {', '.join(no_uv)}",
                         "fix": "UV unwrapping is not automated by this skill yet; unwrap in Blender or a DCC tool"})
        else:
            rows.append({"check": "UV maps", "status": "PASS", "detail": "every mesh has a UV map"})

    if size_mb_budget:
        size_mb = Path(info["file"]).stat().st_size / (1024 * 1024)
        if size_mb <= size_mb_budget:
            rows.append({"check": "transmission size", "status": "PASS", "detail": f"{size_mb:.1f} MB <= {size_mb_budget} MB"})
        else:
            rows.append({"check": "transmission size", "status": "WARN",
                         "detail": f"{size_mb:.1f} MB > {size_mb_budget} MB",
                         "fix": _size_fix_command(info["file"], fmt)})

    if need_manifold:
        bad = [o["name"] for o in info["meshes"]["per_object"] if o["non_manifold_edges"]]
        if bad:
            rows.append({"check": "manifold", "status": "FAIL",
                         "detail": f"non-manifold: {', '.join(f'{n}' for n in bad)}",
                         "fix": "manifold repair is not automated by this skill yet; use Blender's 3D-Print toolbox or a dedicated repair tool"})
        else:
            rows.append({"check": "manifold", "status": "PASS", "detail": "no non-manifold edges"})

    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("--target", required=True, choices=TARGETS)
    _common.add_common_args(ap, dry_run=False, fast=False, progress=False)
    args = ap.parse_args()

    try:
        path = _common.require_exists(args.file, "file")
        fmt = _formats.detect_format(str(path))
        if fmt is None:
            raise SkillError(f"unrecognized file extension: {path.suffix}", kind="input")
        blender_bin = _run.find_blender(args.blender)
        result = _run.run_bpy("info.py", {"path": str(path.resolve()), "format": fmt},
                               blender_bin=blender_bin, timeout=args.timeout)
    except SkillError as err:
        return _common.fail(err, args.json)

    if not result.get("ok"):
        return _common.fail(SkillError(result["error"]["message"], kind=result["error"].get("kind", "internal")), args.json)

    rows = _check(result["data"], args.target)
    overall = "FAIL" if any(r["status"] == "FAIL" for r in rows) else ("WARN" if any(r["status"] == "WARN" for r in rows) else "PASS")

    if args.json:
        print(json.dumps({"target": args.target, "overall": overall, "checks": rows}, indent=2, sort_keys=True))
    else:
        print(f"{args.file} vs {args.target}: {overall}")
        for r in rows:
            print(f"  {r['status']:4s} {r['check']}: {r['detail']}")
            if r.get("fix"):
                print(f"       fix: {r['fix']}")
    return {"PASS": 0, "WARN": 0, "FAIL": 1}[overall]


if __name__ == "__main__":
    sys.exit(main())
