"""bpy side of lod.py: batch-generate LOD0(unmodified original)-LOD3 files at decreasing
triangle counts from one input file, reusing optimize.py's own _decimate (Blender's Decimate
modifier, Collapse mode) at a ratio per level.

Strips Blender's own synthesized bone-shape display widget after each import (see
_compat.strip_import_helper_objects and references/pitfalls.md) -- without this, a skinned
glTF's reported triangle counts are inflated by the widget's own triangles (confirmed on
fox.glb: 656 instead of the real 576) and every level's Decimate modifier runs against it too,
wasted work on an object Blender's own whole-scene export already excludes from the output file
regardless (confirmed directly: the exported files were not actually contaminated even without
this strip -- only the reported numbers and the wasted decimate pass were wrong).

Known limitation shared with optimize.py --decimate-ratio (tracked, not fixed here): the
reported post-decimate triangle count can disagree with what a multi-mesh-object file's export
actually contains for some objects (see references/pitfalls.md and issue #111) -- a limitation
of Blender's own Decimate-then-export interaction on some multi-object scenes, not something
this script special-cases around.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common
import _compat
import optimize as optimize_mod

import bpy


def run(args):
    out_dir = Path(args["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    out_fmt = args["output_format"]
    ext = args["output_ext"]

    _compat.reset_scene()
    _compat.import_file(args["path"], args["format"])
    _compat.strip_import_helper_objects()
    mesh_objs = [o for o in bpy.data.objects if o.type == "MESH"]
    lod0_tris = sum(_compat.object_triangle_count(o) for o in mesh_objs)
    lod0_path = out_dir / f"LOD0{ext}"
    _compat.export_file(str(lod0_path), out_fmt)
    levels = [{"name": "LOD0", "ratio": 1.0, "triangles_before": lod0_tris,
               "triangles_after": lod0_tris, "output": str(lod0_path)}]

    ratios = args.get("ratios")
    if ratios is None:
        # An absolute target for LOD1, scaled from the file's own real triangle count (only
        # known now, after LOD0's import above) -- LOD2/LOD3 keep the same halving relationship
        # info.py's own default LOD1/2/3 suggestions use (RM-010: 50%/25%/10% of the original).
        target = args["target_triangles"]
        ratio1 = min(1.0, max(1e-4, target / lod0_tris)) if lod0_tris else 1.0
        ratios = [ratio1, ratio1 / 2, ratio1 / 4]

    for i, ratio in enumerate(ratios, start=1):
        _compat.reset_scene()
        _compat.import_file(args["path"], args["format"])
        mesh_objs = [o for o in bpy.data.objects if o.type == "MESH"]
        for o in mesh_objs:
            if 0 < ratio < 1:
                bpy.context.view_layer.objects.active = o
                optimize_mod._decimate(o, ratio)
        tris_after = sum(_compat.object_triangle_count(o) for o in mesh_objs)
        out_path = out_dir / f"LOD{i}{ext}"
        _compat.export_file(str(out_path), out_fmt)
        levels.append({
            "name": f"LOD{i}", "ratio": ratio, "triangles_before": lod0_tris,
            "triangles_after": tris_after, "output": str(out_path),
        })

    return {
        "input": args["path"],
        "triangles_original": lod0_tris,
        "levels": levels,
    }


if __name__ == "__main__":
    _common.main(run)
