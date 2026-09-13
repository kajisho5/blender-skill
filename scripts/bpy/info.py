"""bpy side of info.py: load one file, measure it, report facts -- never opinions.

This is the foundation every other script leans on (`inspect -> operate -> verify`): convert.py
diffs two info() calls to validate a round-trip, check.py compares an info() result against a
target's budget, optimize.py decides what needs shrinking from these same numbers.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common
import _compat

import bmesh
import bpy


def _mesh_stats(obj):
    """Triangle/vertex counts and UV layer count as-imported (never modifies the mesh -- info.py
    only reads), plus two checks computed on a *welded* scratch copy:

    - non_manifold_edges: an edge with fewer/more than 2 connected faces once same-position
      vertices are merged. Checking this on the raw, as-imported topology would be wrong for
      almost every real asset: glTF/FBX/OBJ all split a vertex per unique normal/UV at hard
      edges and UV seams, so a perfectly watertight cube imports with 24 unshared verts and
      every edge looking like a boundary. Welding first is what makes this check mean "there is
      an actual hole/gap in the surface", not "this mesh has hard edges" (true of nearly every
      game-ready asset).
    - duplicate_vertices: how many verts the same weld merged. This is informational, not a
      defect -- it is dominated by the same intentional hard-edge/UV-seam splits, so it is
      reported in the data but never turned into a warning.
    """
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    vertex_count = len(bm.verts)
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    triangles = len(bm.faces)

    bm_welded = bm.copy()
    before = len(bm_welded.verts)
    bmesh.ops.remove_doubles(bm_welded, verts=bm_welded.verts, dist=1e-6)
    duplicate_vertices = before - len(bm_welded.verts)
    non_manifold = sum(1 for e in bm_welded.edges if not e.is_manifold)
    bm_welded.free()
    bm.free()
    uv_layers = len(obj.data.uv_layers)
    empty_slots = sum(1 for slot in obj.material_slots if slot.material is None)
    return {
        "triangles": triangles,
        "vertices": vertex_count,
        "non_manifold_edges": non_manifold,
        "duplicate_vertices": duplicate_vertices,
        "uv_maps": uv_layers,
        "has_uv": uv_layers > 0,
        "empty_material_slots": empty_slots,
    }


def _texture_info():
    textures = []
    for img in bpy.data.images:
        if img.name == "Render Result" or img.name == "Viewer Node":
            continue
        missing = False
        size_bytes = None
        if img.packed_file:
            size_bytes = img.packed_file.size
        elif img.filepath:
            p = Path(bpy.path.abspath(img.filepath))
            if p.exists():
                size_bytes = p.stat().st_size
            else:
                missing = True
        w, h = (img.size[0], img.size[1]) if img.has_data else (0, 0)
        textures.append({
            "name": img.name,
            "filepath": img.filepath or None,
            "packed": bool(img.packed_file),
            "width": w,
            "height": h,
            "missing": missing,
            "size_bytes": size_bytes,
        })
    return textures


def _animations(scene):
    fps = scene.render.fps / scene.render.fps_base if scene.render.fps_base else scene.render.fps
    out = []
    for action in bpy.data.actions:
        start, end = action.frame_range
        out.append({
            "name": action.name,
            "frame_start": start,
            "frame_end": end,
            "fps": fps,
            "duration_seconds": (end - start) / fps if fps else None,
        })
    return out


def _armatures():
    out = []
    for obj in bpy.data.objects:
        if obj.type == "ARMATURE":
            out.append({"name": obj.name, "bone_count": len(obj.data.bones)})
    return out


def run(args):
    path = args["path"]
    fmt = args["format"]
    _compat.reset_scene()
    _compat.import_file(path, fmt)

    scene = bpy.context.scene
    objects = list(bpy.data.objects)
    by_type = {}
    for o in objects:
        by_type[o.type] = by_type.get(o.type, 0) + 1

    mesh_objs = [o for o in objects if o.type == "MESH"]
    per_object = []
    total_tris = total_verts = 0
    for o in mesh_objs:
        stats = _mesh_stats(o)
        stats["name"] = o.name
        per_object.append(stats)
        total_tris += stats["triangles"]
        total_verts += stats["vertices"]

    materials = list(bpy.data.materials)

    # World-space bounding box across every mesh object, so scale/origin decisions can be made
    # from real geometry rather than each object's local-space corners.
    bbox_min = [float("inf")] * 3
    bbox_max = [float("-inf")] * 3
    for o in mesh_objs:
        for corner in o.bound_box:
            world = o.matrix_world @ __import__("mathutils").Vector(corner)
            for i in range(3):
                bbox_min[i] = min(bbox_min[i], world[i])
                bbox_max[i] = max(bbox_max[i], world[i])
    has_bbox = mesh_objs and bbox_min[0] != float("inf")
    bounding_box = {
        "min": bbox_min if has_bbox else None,
        "max": bbox_max if has_bbox else None,
        "size": [bbox_max[i] - bbox_min[i] for i in range(3)] if has_bbox else None,
    }

    origins = [{"name": o.name, "location": list(o.location)} for o in objects]

    warnings = []
    missing_tex = [t["name"] for t in _texture_info() if t["missing"]]
    if missing_tex:
        warnings.append(f"missing texture file(s): {', '.join(missing_tex)}")
    for stats in per_object:
        if stats["non_manifold_edges"]:
            warnings.append(f"{stats['name']}: {stats['non_manifold_edges']} non-manifold edge(s) (hole/gap in the surface, after welding split normals/UV seams)")
        if not stats["has_uv"]:
            warnings.append(f"{stats['name']}: no UV map")
        if stats["empty_material_slots"]:
            warnings.append(f"{stats['name']}: {stats['empty_material_slots']} empty material slot(s)")

    return {
        "file": path,
        "format": fmt,
        "blender_version": ".".join(str(v) for v in bpy.app.version),
        "objects": {"total": len(objects), "by_type": by_type},
        "meshes": {
            "count": len(mesh_objs),
            "triangles": total_tris,
            "vertices": total_verts,
            "per_object": per_object,
        },
        "materials": {"count": len(materials), "names": [m.name for m in materials]},
        "textures": _texture_info(),
        "armatures": _armatures(),
        "animations": _animations(scene),
        "unit": {
            "system": scene.unit_settings.system,
            "scale_length": scene.unit_settings.scale_length,
        },
        "bounding_box": bounding_box,
        "origins": origins,
        "warnings": warnings,
    }


if __name__ == "__main__":
    _common.main(run)
