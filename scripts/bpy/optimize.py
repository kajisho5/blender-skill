"""bpy side of optimize.py: decimate, weld, recalc normals, triangulate, cap texture size,
purge unused data blocks -- each independently toggleable, plus three presets that bundle
sensible defaults for a destination.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common
import _compat

import bmesh
import bpy

# Opinionated defaults, not a spec any external authority publishes -- texture caps and decimate
# ratios a reasonable default for that destination, meant to be overridden with the specific
# flags (--texture-max, --decimate-ratio) when they don't fit an asset.
PRESETS = {
    "web": {"texture_max": 2048, "decimate_ratio": None},
    "mobile": {"texture_max": 1024, "decimate_ratio": 0.5},
    "ar": {"texture_max": 2048, "decimate_ratio": 0.7},
}


def _apply_modifier(obj, mod):
    ctx_override = {"object": obj}
    with bpy.context.temp_override(**ctx_override):
        bpy.ops.object.modifier_apply(modifier=mod.name)


def _weld_and_triangulate(obj, weld: bool, triangulate: bool, recalc_normals: bool) -> int:
    """Returns the number of vertices removed by welding (0 if not requested)."""
    if not (weld or triangulate or recalc_normals):
        return 0
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    removed = 0
    if weld:
        before = len(bm.verts)
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
        removed = before - len(bm.verts)
    if recalc_normals:
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    if triangulate:
        bmesh.ops.triangulate(bm, faces=bm.faces[:])
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return removed


def _decimate(obj, ratio: float) -> None:
    mod = obj.modifiers.new("optimize_decimate", "DECIMATE")
    mod.ratio = ratio
    _apply_modifier(obj, mod)


def _resize_textures(max_size: int) -> list:
    resized = []
    for img in bpy.data.images:
        if img.name in ("Render Result", "Viewer Node"):
            continue
        len(img.pixels)  # force the lazy load (see look.py) before reading/changing size
        w, h = img.size
        if max(w, h) <= max_size:
            continue
        scale = max_size / max(w, h)
        new_w, new_h = max(1, round(w * scale)), max(1, round(h * scale))
        img.scale(new_w, new_h)
        resized.append({"name": img.name, "from": [w, h], "to": [new_w, new_h]})
    return resized


def _purge_unused() -> int:
    before = sum(len(getattr(bpy.data, coll)) for coll in
                 ("meshes", "materials", "images", "actions", "armatures", "cameras", "lights"))
    bpy.ops.outliner.orphans_purge(do_recursive=True)
    after = sum(len(getattr(bpy.data, coll)) for coll in
                ("meshes", "materials", "images", "actions", "armatures", "cameras", "lights"))
    return before - after


def run(args):
    preset = PRESETS.get(args.get("target")) if args.get("target") else None
    texture_max = args.get("texture_max") or (preset or {}).get("texture_max")
    decimate_ratio = args.get("decimate_ratio")
    if decimate_ratio is None and preset:
        decimate_ratio = preset.get("decimate_ratio")

    _compat.reset_scene()
    _compat.import_file(args["path"], args["format"])

    mesh_objs = [o for o in bpy.data.objects if o.type == "MESH"]
    before_tris = sum(_compat.object_triangle_count(o) for o in mesh_objs)

    welded_total = 0
    for o in mesh_objs:
        welded_total += _weld_and_triangulate(
            o, weld=bool(args.get("weld_doubles")), triangulate=bool(args.get("triangulate")),
            recalc_normals=bool(args.get("recalc_normals")),
        )
        if decimate_ratio and 0 < decimate_ratio < 1:
            bpy.context.view_layer.objects.active = o
            _decimate(o, decimate_ratio)

    resized = _resize_textures(texture_max) if texture_max else []
    purged = _purge_unused() if args.get("purge_unused") else 0

    after_tris = sum(_compat.object_triangle_count(o) for o in mesh_objs)
    _compat.export_file(args["output"], args["output_format"])

    return {
        "triangles_before": before_tris,
        "triangles_after": after_tris,
        "vertices_welded": welded_total,
        "textures_resized": resized,
        "orphan_data_purged": purged,
        "output_path": args["output"],
    }


if __name__ == "__main__":
    _common.main(run)
