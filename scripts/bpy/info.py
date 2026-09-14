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


# Non-internal mesh attributes Blender itself creates that are not something a user/importer
# added -- Mesh.Attribute.is_internal is False for these too (it only flags the dot-prefixed
# bookkeeping ones like .corner_vert), so they must be excluded by name. Confirmed present on a
# plain cube with no custom data at all: position, sharp_face, UVMap (one per UV layer, already
# reported via uv_maps/has_uv); adding a second material slot, a bevel weight and an edge crease
# via ordinary edit-mode operators adds material_index, bevel_weight_edge, and crease_edge the
# same way -- confirmed on real Blender, not from documentation alone.
_BUILTIN_ATTR_NAMES = {
    "position", "sharp_face", "sharp_edge", "material_index",
    "crease_edge", "crease_vert", "bevel_weight_edge", "bevel_weight_vert", "id",
}


def _attribute_lists(obj):
    """Vertex color layers (Mesh.color_attributes) and any other genuinely custom mesh attribute
    (Mesh.attributes minus internal bookkeeping, Blender's own built-ins, UV layers already
    reported elsewhere, and color layers already listed separately). Confirmed on a real cube
    with two materials, a bevel weight, an edge crease, one vertex color layer and two custom
    attributes added: reports exactly the one color layer and the two custom attributes, nothing
    else -- and confirmed empty on tests/fixtures/box.glb and fox.glb (neither has any).
    """
    mesh = obj.data
    uv_names = {uv.name for uv in mesh.uv_layers}
    color_names = {ca.name for ca in mesh.color_attributes}
    vertex_colors = [{"name": ca.name, "domain": ca.domain, "data_type": ca.data_type} for ca in mesh.color_attributes]
    custom = []
    for a in mesh.attributes:
        if a.is_internal or a.name in _BUILTIN_ATTR_NAMES or a.name in uv_names or a.name in color_names:
            continue
        custom.append({"name": a.name, "domain": a.domain, "data_type": a.data_type})
    return vertex_colors, custom


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


def _seg_intersect(p1, p2, p3, p4) -> bool:
    def ccw(a, b, c):
        return (c[1] - a[1]) * (b[0] - a[0]) - (b[1] - a[1]) * (c[0] - a[0])
    d1, d2 = ccw(p3, p4, p1), ccw(p3, p4, p2)
    d3, d4 = ccw(p1, p2, p3), ccw(p1, p2, p4)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def _point_in_tri(p, tri) -> bool:
    def sign(a, b, c):
        return (a[0] - c[0]) * (b[1] - c[1]) - (b[0] - c[0]) * (a[1] - c[1])
    d1, d2, d3 = sign(p, tri[0], tri[1]), sign(p, tri[1], tri[2]), sign(p, tri[2], tri[0])
    has_neg, has_pos = (d1 < 0 or d2 < 0 or d3 < 0), (d1 > 0 or d2 > 0 or d3 > 0)
    return not (has_neg and has_pos)


def _tri_overlap_2d(t1, t2) -> bool:
    for i in range(3):
        for j in range(3):
            if _seg_intersect(t1[i], t1[(i + 1) % 3], t2[j], t2[(j + 1) % 3]):
                return True
    return _point_in_tri(t1[0], t2) or _point_in_tri(t2[0], t1)


def _uv_checks(bm):
    """UV out-of-[0,1]-range, zero-area (never meaningfully unwrapped), and overlapping faces,
    on the *active* UV layer of an already-triangulated bmesh. Returns None if there's no UV
    layer (nothing to check).

    - out_of_bounds is informational, not a defect: a texture using UV wrap/repeat tiling
      legitimately places UVs outside [0,1] (confirmed: scaling a normal unwrap by 3x for tiling
      reads 10/12 faces out of bounds, with 0 zero-area and 0 overlap -- a real, common,
      non-broken pattern).
    - zero_area faces are a real defect: their UV triangle has ~zero area, meaning the face was
      never meaningfully unwrapped (confirmed: collapsing every UV to a single point reads
      12/12 faces zero-area, 0 out-of-bounds).
    - overlapping is approximate and informational, like self_intersecting_faces_approx --
      overlapping UV islands are a deliberate, common technique (mirrored left/right halves of a
      symmetric character sharing one UV island) as often as they're a mistake, so this is
      reported as data, never a warning with a fix command.

    Two things had to be gotten right to avoid this being noisy garbage on a real asset:
    1. Filtering candidate pairs by UV-space bounding-box overlap alone (matching the 3D
       self-intersection check's approach) is nowhere near precise enough here: a real,
       efficiently-packed UV layout has many faces whose *bounding boxes* touch or overlap
       without the *triangles* actually overlapping (confirmed: naive bbox-only counting reported
       273/576 false "overlaps" on tests/fixtures/fox.glb, a cleanly laid-out real character UV
       map, and 33/80 on its eye mesh). Exact 2D triangle-triangle overlap (edge-crossing plus
       point-in-triangle containment) on the bbox-filtered candidates brings both to the correct
       0/0.
    2. Excluding "adjacent" pairs by shared UV vertex position must only exclude a shared *edge*
       (1-2 shared vertices) -- excluding on *any* shared vertex incorrectly waves through a fully
       duplicated/stacked island (all 3 vertices coincide) as "just adjacent", missing the most
       severe overlap case entirely (confirmed: a synthetic mirrored/stacked-UV cube read 0
       overlapping faces with that bug, 12 (the true count) once fixed to only exclude 1-2 shared
       vertices, never 3).
    Degenerate (zero-area) faces are excluded from the overlap scan entirely -- a point has no
    meaningful "overlap" and would otherwise register against everything near it, double-counting
    the same defect zero_area already reports.
    """
    uv_layer = bm.loops.layers.uv.active
    if uv_layer is None:
        return None
    out_of_bounds = 0
    zero_area = 0
    candidates = []
    for f in bm.faces:
        uvs = [(loop[uv_layer].uv.x, loop[uv_layer].uv.y) for loop in f.loops]
        if any(u < 0 or u > 1 for uv in uvs for u in uv):
            out_of_bounds += 1
        a, b, c = uvs
        area = abs((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])) / 2
        degenerate = area < 1e-8
        if degenerate:
            zero_area += 1
        xs, ys = [u[0] for u in uvs], [u[1] for u in uvs]
        bbox = (min(xs), min(ys), max(xs), max(ys))
        positions = {(round(x, 5), round(y, 5)) for x, y in uvs}
        candidates.append((bbox, positions, uvs, degenerate))

    overlapping = 0
    n = len(candidates)
    for i in range(n):
        bi, pi, ti, di = candidates[i]
        if di:
            continue
        for j in range(i + 1, n):
            bj, pj, tj, dj = candidates[j]
            if dj:
                continue
            if bi[2] < bj[0] or bj[2] < bi[0] or bi[3] < bj[1] or bj[3] < bi[1]:
                continue
            if 0 < len(pi & pj) < 3:
                continue
            if _tri_overlap_2d(ti, tj):
                overlapping += 1

    return {
        "out_of_bounds_faces": out_of_bounds,
        "zero_area_faces": zero_area,
        "overlapping_faces_approx": overlapping,
    }


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
    uv_checks = _uv_checks(bm)
    bm_welded.free()
    bm.free()
    uv_layers = len(obj.data.uv_layers)
    empty_slots = sum(1 for slot in obj.material_slots if slot.material is None)
    origin_to_center, origin_to_bottom_center = _local_bbox_reference_points(obj)
    vertex_colors, custom_attributes = _attribute_lists(obj)
    return {
        "triangles": triangles,
        "vertices": vertex_count,
        "non_manifold_edges": non_manifold,
        "boundary_edges": boundary_edges,
        "self_intersecting_faces_approx": self_intersecting,
        "flipped_normal_faces": flipped_normals,
        "duplicate_vertices": duplicate_vertices,
        "uv_checks": uv_checks,
        "uv_maps": uv_layers,
        "has_uv": uv_layers > 0,
        "empty_material_slots": empty_slots,
        "scale": list(obj.scale),
        "origin_offset_from_center": list(origin_to_center),
        "origin_offset_from_bottom_center": list(origin_to_bottom_center),
        "vertex_colors": vertex_colors,
        "custom_attributes": custom_attributes,
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
        if stats["uv_checks"]:
            uv = stats["uv_checks"]
            if uv["zero_area_faces"]:
                warnings.append(
                    f"{stats['name']}: {uv['zero_area_faces']} face(s) with zero-area UVs "
                    "(never meaningfully unwrapped) -- no automatic fix, needs a real UV unwrap"
                )
            if uv["overlapping_faces_approx"]:
                warnings.append(
                    f"{stats['name']}: ~{uv['overlapping_faces_approx']} face pair(s) have overlapping UVs "
                    "(approximate -- may be deliberate, e.g. mirrored islands; worth a manual look, no automatic fix)"
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
