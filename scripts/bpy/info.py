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
import mathutils
from mathutils.bvhtree import BVHTree


def _self_intersecting_faces(bm) -> int:
    """Approximate count of face pairs that actually overlap in 3D space, not just at a shared
    edge/vertex. BVHTree.overlap() reports every pair of triangles whose bounding boxes touch,
    which includes every ordinary adjacent-face pair at a shared edge -- on a real asset with
    split normals/UV seams (see the non-manifold note above), two triangles that share a
    position are *different* vertex indices, so filtering "adjacent" by vertex *index* still
    lets every hard edge/UV seam through as a false positive (confirmed: 115 false positives on
    tests/fixtures/fox.glb using index comparison, 0 using position comparison). Filtering by
    vertex *position* instead correctly drops those while still catching real overlaps (a second
    cube pushed halfway into the first: 8 genuinely-intersecting triangle pairs, both ways of
    filtering agree there). This is still an approximation -- true triangle-triangle intersection
    is not computed, only bounding-box overlap between non-adjacent triangles -- so treat the
    count as "worth a manual look", not an exact defect count.
    """
    bm2 = bm.copy()
    bmesh.ops.triangulate(bm2, faces=bm2.faces[:])
    bm2.faces.ensure_lookup_table()
    tree = BVHTree.FromBMesh(bm2, epsilon=0.0)
    bad_pairs = set()
    for i1, i2 in tree.overlap(tree):
        if i1 == i2:
            continue
        f1, f2 = bm2.faces[i1], bm2.faces[i2]
        p1 = {v.co.to_tuple(5) for v in f1.verts}
        p2 = {v.co.to_tuple(5) for v in f2.verts}
        if p1 & p2:
            continue
        bad_pairs.add(tuple(sorted((i1, i2))))
    bm2.free()
    return len(bad_pairs)


def _flipped_normal_faces(bm) -> int:
    """How many faces' normals disagree with Blender's own "recalculate outside" pass on a
    scratch copy. Confirmed reliable on a fully closed/manifold mesh: a cube with every face
    normal manually inverted is flagged 6/6, an untouched cube 0/6, and a manifold sphere with a
    hole cut in it (non-manifold boundary, but otherwise a single correctly-oriented shell) is
    still correctly read as 0/118 -- holes alone don't confuse it.

    It is NOT reliable once the mesh has non-manifold edges from something other than a simple
    boundary hole (disconnected shells stitched only by non-manifold junctions, or genuinely
    non-manifold geometry) -- confirmed on tests/fixtures/fox.glb (a real rigged character mesh,
    1150 non-manifold edges): this same comparison flags 299 of 576 faces, and actually applying
    optimize.py's --recalc-normals to "fix" them visibly corrupts the model's shading (turns a
    cleanly-shaded render into a black/white patchwork -- verified by rendering before/after).
    Blender's own bpy.ops.mesh.normals_make_consistent(inside=False) does the same thing to this
    file, so this is not specific to the bmesh.ops call. The likely cause: recalc's "outside"
    flood-fill is only well-defined per connected, closed shell, and this model's body parts
    aren't all joined by shared manifold edges -- so the caller (_mesh_stats) only trusts this
    function's result when the mesh has zero non-manifold edges.
    """
    bm2 = bm.copy()
    bm2.normal_update()
    before = [tuple(f.normal) for f in bm2.faces]
    bmesh.ops.recalc_face_normals(bm2, faces=bm2.faces[:])
    bm2.normal_update()
    flipped = sum(
        1 for a, b in zip(before, bm2.faces) if a[0] * b.normal[0] + a[1] * b.normal[1] + a[2] * b.normal[2] < 0
    )
    bm2.free()
    return flipped


def _local_bbox_reference_points(obj):
    """Local-space bounding-box center and bottom-center, in the object's own coordinate frame --
    i.e. how far the object's origin sits from each, which is what `optimize.py --origin
    center|bottom` moves it to. Not itself a defect: many assets deliberately place their origin
    away from either (a door's origin at its hinge edge, a wheel's at its axle) -- info.py reports
    this as data, never a warning, unlike non-manifold/self-intersection/flipped-normal.
    """
    corners = [mathutils.Vector(c) for c in obj.bound_box]
    center = sum(corners, mathutils.Vector()) / 8
    bottom_center = mathutils.Vector((center.x, center.y, min(c.z for c in corners)))
    return center, bottom_center


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
    # Boundary edges specifically (exactly 1 face, the "hole" case) rather than every
    # non-manifold edge (which also includes edges shared by 3+ faces, not fillable as a hole) --
    # this is what optimize.py's --fill-holes acts on.
    boundary_edges = sum(1 for e in bm_welded.edges if e.is_boundary)
    self_intersecting = _self_intersecting_faces(bm)
    # Only trust the flipped-normal comparison on an already-manifold mesh -- see
    # _flipped_normal_faces' docstring for the real (not hypothetical) corruption this avoids.
    flipped_normals = _flipped_normal_faces(bm) if non_manifold == 0 else None
    bm_welded.free()
    bm.free()
    uv_layers = len(obj.data.uv_layers)
    empty_slots = sum(1 for slot in obj.material_slots if slot.material is None)
    origin_to_center, origin_to_bottom_center = _local_bbox_reference_points(obj)
    return {
        "triangles": triangles,
        "vertices": vertex_count,
        "non_manifold_edges": non_manifold,
        "boundary_edges": boundary_edges,
        "self_intersecting_faces_approx": self_intersecting,
        "flipped_normal_faces": flipped_normals,
        "duplicate_vertices": duplicate_vertices,
        "uv_maps": uv_layers,
        "has_uv": uv_layers > 0,
        "empty_material_slots": empty_slots,
        "scale": list(obj.scale),
        "origin_offset_from_center": list(origin_to_center),
        "origin_offset_from_bottom_center": list(origin_to_bottom_center),
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
            fix = ("try: optimize.py <file> -o <out> --fill-holes" if stats["boundary_edges"]
                   else "no automatic fix for this shape of non-manifold edge (not a simple hole) -- needs manual cleanup")
            warnings.append(f"{stats['name']}: {stats['non_manifold_edges']} non-manifold edge(s) (hole/gap in the surface, after welding split normals/UV seams) -- {fix}")
        if stats["flipped_normal_faces"]:
            warnings.append(f"{stats['name']}: {stats['flipped_normal_faces']} face(s) with a flipped/inward normal -- try: optimize.py <file> -o <out> --recalc-normals")
        elif stats["flipped_normal_faces"] is None:
            warnings.append(f"{stats['name']}: flipped-normal check skipped (unreliable while non-manifold edges are present -- fix those first, then re-run info.py)")
        if stats["self_intersecting_faces_approx"]:
            warnings.append(f"{stats['name']}: ~{stats['self_intersecting_faces_approx']} face pair(s) look self-intersecting (approximate, bounding-box based -- worth a manual look, no automatic fix)")
        if not stats["has_uv"]:
            warnings.append(f"{stats['name']}: no UV map")
        if stats["empty_material_slots"]:
            warnings.append(f"{stats['name']}: {stats['empty_material_slots']} empty material slot(s)")
        if any(abs(s - 1.0) > 1e-4 for s in stats["scale"]):
            warnings.append(
                f"{stats['name']}: unapplied scale {tuple(round(s, 4) for s in stats['scale'])} "
                "(a common sign of a cm/m unit mismatch) -- try: optimize.py <file> -o <out> --fix-scale"
            )

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
