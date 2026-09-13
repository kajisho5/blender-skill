"""bpy side of bake.py: bake a material to a texture (Cycles bake, the reliable path for
AO/normal/roughness/diffuse/combined). Confirmed on a real Blender 4.2.23: a bake writes real
values into the target image (min/max/mean reported in the result -- verify with those numbers,
not by eyeballing a thumbnail, since an AO/normal bake is mostly outside-UV black background
with the real data concentrated in a comparatively small UV-island area).

--atlas packs every target object's UVs into shared 0-1 space first (bpy.ops.uv.pack_islands
across all objects in a multi-object edit-mode selection) so more than one object's material can
share a single output image; without it, each object keeps its own UV and should really get its
own output image (baking several un-repacked objects to one image will overlap).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common
import _compat

import bpy
import numpy as np

_BAKE_TYPE = {"ao": "AO", "normal": "NORMAL", "roughness": "ROUGHNESS", "diffuse": "DIFFUSE", "combined": "COMBINED"}


def _prep_diffuse_pass_filter(scene, bake_pass: str):
    """A plain 'DIFFUSE' bake includes direct+indirect lighting by default -- fine for
    'combined', wrong for a clean albedo map. Restrict to just the color pass for 'diffuse'."""
    b = scene.render.bake
    if bake_pass == "diffuse":
        b.use_pass_direct = False
        b.use_pass_indirect = False
        b.use_pass_color = True


def run(args):
    _compat.reset_scene()
    _compat.import_file(args["path"], args["format"])

    bake_pass = args["pass"]
    if bake_pass not in _BAKE_TYPE:
        raise ValueError(f"unknown bake pass {bake_pass!r}")
    size = args.get("size", 1024)
    names = args.get("objects")
    targets = [o for o in bpy.data.objects if o.type == "MESH" and (not names or o.name in names)]
    if not targets:
        raise ValueError("no matching mesh objects to bake")
    no_uv = [o.name for o in targets if not o.data.uv_layers]
    if no_uv:
        raise ValueError(f"no UV map on: {', '.join(no_uv)} (baking needs one -- see look.py --uv or check.py's 'UV maps' row)")

    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = args.get("samples", 32)
    _prep_diffuse_pass_filter(scene, bake_pass)

    if args.get("atlas") and len(targets) > 1:
        bpy.ops.object.select_all(action="DESELECT")
        for o in targets:
            o.select_set(True)
        bpy.context.view_layer.objects.active = targets[0]
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.pack_islands(margin=0.02)
        bpy.ops.object.mode_set(mode="OBJECT")

    image = bpy.data.images.new(f"bake_{bake_pass}", width=size, height=size, alpha=False)
    image.colorspace_settings.name = "Non-Color" if bake_pass in ("normal", "roughness", "ao") else "sRGB"

    bpy.ops.object.select_all(action="DESELECT")
    for o in targets:
        o.select_set(True)
        bpy.context.view_layer.objects.active = o
        for slot in o.material_slots:
            if slot.material is None or slot.material.node_tree is None:
                continue
            nt = slot.material.node_tree
            node = nt.nodes.new("ShaderNodeTexImage")
            node.image = image
            for n in nt.nodes:
                n.select = False
            node.select = True
            nt.nodes.active = node

    bpy.ops.object.bake(type=_BAKE_TYPE[bake_pass], margin=args.get("margin", 8))

    px = np.array(image.pixels[:]).reshape(size, size, -1 if image.channels > 1 else 1)
    stats = {"min": float(px.min()), "max": float(px.max()), "mean": float(px.mean())}

    image.filepath_raw = args["output"]
    image.file_format = "PNG"
    image.save()

    return {"pass": bake_pass, "output_path": args["output"], "size": size,
            "objects": [o.name for o in targets], "pixel_stats": stats}


if __name__ == "__main__":
    _common.main(run)
