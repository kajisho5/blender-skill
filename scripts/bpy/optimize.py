"""bpy side of optimize.py: decimate, weld, recalc normals, triangulate, cap texture size,
purge unused data blocks -- each independently toggleable, plus three presets that bundle
sensible defaults for a destination.

Strips Blender's own synthesized bone-shape display widget after import (see
_compat.strip_import_helper_objects and references/pitfalls.md) -- without it, a skinned
armature input's reported triangles_before/triangles_after were inflated by the widget's own
triangles (confirmed on fox.glb: 656 instead of the real 576), and --texture-auto-resolution's
scene-bounding-box calculation would be skewed by an object that isn't really part of the asset.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common
import _compat

import bmesh
import bpy
import mathutils
import info as info_mod

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


def _fill_holes(obj) -> int:
    """Fills boundary loops (edges used by exactly 1 face -- an actual hole/gap, see
    info.py's non_manifold_edges/boundary_edges note) with an n-gon each. Does not touch
    non-manifold edges shared by 3+ faces (not a fillable hole shape); returns how many faces
    were added.

    Must weld coincident-position vertices first: a raw glTF/FBX/OBJ import splits a vertex per
    unique normal/UV at every hard edge (see references/pitfalls.md), so on the as-imported mesh
    nearly every edge around the hole reports as "boundary" per its own un-shared vertices (16
    for a 1-quad hole in a cube, confirmed) rather than the true 4-edge loop -- holes_fill finds
    no closed loop and silently fills nothing. Welding first (same dist=1e-6 info.py's read-only
    diagnostic already uses) collapses those to the real 8-vertex/4-edge topology without losing
    the mesh's shading: bmesh keeps per-face-corner (loop) UV/normal data on the now-shared
    vertex, so re-exporting still re-splits it the same way the input was authored. Confirmed:
    the same cube-with-one-face-deleted case goes from a no-op (0 faces added) to correctly
    restoring a closed 6-face cube once welding runs first.
    """
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-6)
    boundary_edges = [e for e in bm.edges if e.is_boundary]
    added = 0
    if boundary_edges:
        result = bmesh.ops.holes_fill(bm, edges=boundary_edges, sides=0)
        added = len(result["faces"])
    bm.normal_update()
    bm.to_mesh(obj.data)
    obj.data.update()
    bm.free()
    return added


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


def _resize_textures_per_image(caps: dict) -> list:
    """Like _resize_textures, but each image has its own max dimension (from `caps`, keyed by
    image name) instead of one global cap. An image not present in `caps` at all (not
    referenced by any mesh object's material this skill's own traversal found) is left
    untouched -- never guess a cap for something with no real usage signal."""
    resized = []
    for img in bpy.data.images:
        if img.name in ("Render Result", "Viewer Node"):
            continue
        max_size = caps.get(img.name)
        if not max_size:
            continue
        len(img.pixels)
        w, h = img.size
        if max(w, h) <= max_size:
            continue
        scale = max_size / max(w, h)
        new_w, new_h = max(1, round(w * scale)), max(1, round(h * scale))
        img.scale(new_w, new_h)
        resized.append({"name": img.name, "from": [w, h], "to": [new_w, new_h], "cap": max_size})
    return resized


def _combined_bounding_diameter(objs) -> float:
    """World-space bounding-box diagonal across every object in `objs` combined (0.0 if empty)."""
    xs, ys, zs = [], [], []
    for obj in objs:
        for c in obj.bound_box:
            world = obj.matrix_world @ mathutils.Vector(c)
            xs.append(world.x)
            ys.append(world.y)
            zs.append(world.z)
    if not xs:
        return 0.0
    return ((max(xs) - min(xs)) ** 2 + (max(ys) - min(ys)) ** 2 + (max(zs) - min(zs)) ** 2) ** 0.5


def _next_pow2(n: int) -> int:
    p = 1
    while p < n:
        p *= 2
    return p


def _image_users(mesh_objs) -> dict:
    """image name -> list of mesh objects whose material graph references it, via any Image
    Texture node (not scoped to a particular BSDF socket, unlike info.py's colorspace check --
    here every use counts, since the question is just "does this object need this texture at
    all", not "which specific input does it feed")."""
    users = {}
    for obj in mesh_objs:
        for slot in obj.material_slots:
            mat = slot.material
            if not mat or not mat.use_nodes or not mat.node_tree:
                continue
            for node in mat.node_tree.nodes:
                if node.type == "TEX_IMAGE" and node.image:
                    users.setdefault(node.image.name, []).append(obj)
    return users


def _auto_texture_max(mesh_objs, viewport_width: int) -> dict:
    """image name -> an ideal max dimension (next power of two), derived from how much of the
    *whole scene's own combined bounding box* the largest object using that texture spans --
    screen-occupancy as a data-driven proxy, not an assumed camera/FOV this skill has no way to
    know. A single-object file gets fraction 1.0 (its texture could fill the whole assumed
    viewport), which is the sensible degenerate case; a small prop sharing a scene with a much
    larger one gets a correspondingly smaller cap than a flat --texture-max value could express.
    Images with no mesh-object user found in `mesh_objs` (orphaned, or referenced some other way
    this skill doesn't walk) get no entry -- never guess a cap with no real usage signal.
    """
    scene_diameter = _combined_bounding_diameter(mesh_objs)
    caps = {}
    if not scene_diameter:
        return caps
    for image_name, users in _image_users(mesh_objs).items():
        obj_diameter = max(_combined_bounding_diameter([o]) for o in users)
        fraction = min(1.0, obj_diameter / scene_diameter)
        caps[image_name] = max(64, _next_pow2(round(viewport_width * fraction)))
    return caps


def _fix_scale(obj) -> bool:
    """Bakes obj.scale into the mesh data (bpy.ops.object.transform_apply(scale=True)), leaving
    world-space size/position unchanged -- confirmed: an object with scale (0.01, 0.01, 0.01)
    (the classic cm/m-mismatch symptom) and world dimensions (2, 2, 2) keeps those same world
    dimensions and ends up with scale (1, 1, 1) afterward. Returns whether anything changed.
    """
    if all(abs(s - 1.0) <= 1e-4 for s in obj.scale):
        return False
    bpy.context.view_layer.objects.active = obj
    for o in bpy.context.selected_objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return True


def _set_origin(obj, mode: str) -> None:
    """Moves the object's origin without moving its geometry in world space (confirmed: the
    object's world-space bounding box corners are identical before/after, only obj.location and
    the mesh's local coordinates change to compensate).

    - center: Blender's own bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY', center='BOUNDS'),
      the bounding-box center.
    - bottom: no built-in Blender op for this specifically -- placing the 3D cursor at the
      world-space bounding-box bottom-center, then origin_set(type='ORIGIN_CURSOR'), is the
      standard technique (confirmed: restores the saved cursor position afterward so this has no
      side effect on the scene beyond the target object).
    """
    bpy.context.view_layer.objects.active = obj
    for o in bpy.context.selected_objects:
        o.select_set(False)
    obj.select_set(True)
    if mode == "center":
        bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY", center="BOUNDS")
        return
    if mode == "bottom":
        corners = [obj.matrix_world @ mathutils.Vector(c) for c in obj.bound_box]
        xs = [c.x for c in corners]
        ys = [c.y for c in corners]
        zs = [c.z for c in corners]
        bottom_center = mathutils.Vector(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, min(zs)))
        saved_cursor = bpy.context.scene.cursor.location.copy()
        bpy.context.scene.cursor.location = bottom_center
        bpy.ops.object.origin_set(type="ORIGIN_CURSOR")
        bpy.context.scene.cursor.location = saved_cursor
        return
    raise ValueError(f"unknown --origin mode {mode!r} (center, bottom)")


def _has_non_manifold(obj) -> bool:
    """Same welded (dist=1e-6) non-manifold check info.py's diagnostic uses -- reused here so
    --recalc-normals can warn instead of silently corrupting a mesh it can't reliably fix. See
    info.py's _flipped_normal_faces docstring: on a real non-manifold character mesh
    (tests/fixtures/fox.glb), Blender's own normal recalculation (both bmesh.ops.
    recalc_face_normals and the edit-mode bpy.ops.mesh.normals_make_consistent) flips roughly
    half the faces of an already-correctly-shaded model, verified by rendering before/after.
    """
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-6)
    non_manifold = any(not e.is_manifold for e in bm.edges)
    bm.free()
    return non_manifold


def _thin_point_cloud(obj, voxel_size: float) -> int:
    """Voxel-grid downsampling for a point cloud (a PLY vertices-only mesh, no faces): bucket
    every vertex into a voxel_size-sized grid cell and keep only the first vertex encountered in
    each occupied cell, deleting the rest. This is the standard point-cloud decimation technique
    (used by PCL/Open3D/CloudCompare) -- distinct from mesh Decimate, which needs faces/edges a
    point cloud doesn't have. Returns the number of points removed; a no-op (returns 0) on a mesh
    that has any faces at all, since this is specifically for point-cloud "thinning", not mesh
    simplification.
    """
    if len(obj.data.polygons) > 0:
        return 0
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    buckets = {}
    for v in bm.verts:
        key = (round(v.co.x / voxel_size), round(v.co.y / voxel_size), round(v.co.z / voxel_size))
        buckets.setdefault(key, v)  # first vertex seen per cell is the kept representative
    keep_indices = {v.index for v in buckets.values()}
    to_remove = [v for v in bm.verts if v.index not in keep_indices]
    removed = len(to_remove)
    bmesh.ops.delete(bm, geom=to_remove, context="VERTS")
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return removed


def _fix_colorspace(materials) -> int:
    """Corrects every texture colorspace mismatch info.py's _colorspace_issues flags (Base
    Color/Emission tagged wrong instead of 'sRGB'; Metallic/Roughness/Alpha/a normal map's own
    texture tagged wrong instead of 'Non-Color') -- reuses that exact same detection so a fix
    always matches what info.py would report as wrong, rather than a second, possibly-drifting
    copy of the same logic."""
    fixed = 0
    for issue in info_mod._colorspace_issues(materials):
        image = bpy.data.images.get(issue["image"])
        if image is not None:
            image.colorspace_settings.name = issue["expected"]
            fixed += 1
    return fixed


def _set_all_colorspace(name: str) -> list:
    """Force every image's colorspace tag to `name`, regardless of which socket it feeds --
    the blunt override for a deliberate choice (e.g. 'ACEScg'/'ACES2065-1' for an ACES pipeline,
    both real, available colorspace tags in Blender's own bundled OCIO config; ACES itself is
    only ever a *view transform* for rendering, and Blender's stock/bundled OCIO config has no
    ACES view transform at all -- confirmed via its own view_transform enum, which lists only
    Standard/Khronos PBR Neutral/AgX/Filmic/Filmic Log/False Color/Raw -- so this only ever
    covers the texture-tagging half of "ACES", not a full ACES render pipeline). Not validated
    against a hardcoded list: Blender's own error on an unrecognized name already names every
    real option for the OCIO config actually loaded, which can vary by Blender build/version.
    """
    changed = []
    for img in bpy.data.images:
        if img.name in ("Render Result", "Viewer Node"):
            continue
        if img.colorspace_settings.name == name:
            continue
        try:
            img.colorspace_settings.name = name
        except TypeError as e:
            raise ValueError(f"{name!r} is not a valid colorspace in this Blender: {e}") from e
        changed.append(img.name)
    return changed


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
    _compat.strip_import_helper_objects()

    mesh_objs = [o for o in bpy.data.objects if o.type == "MESH"]
    before_tris = sum(_compat.object_triangle_count(o) for o in mesh_objs)

    welded_total = 0
    holes_filled_total = 0
    scales_fixed_total = 0
    points_thinned_total = 0
    warnings = []
    for o in mesh_objs:
        point_thin_voxel = args.get("point_thin_voxel")
        if point_thin_voxel and point_thin_voxel > 0:
            points_thinned_total += _thin_point_cloud(o, point_thin_voxel)
        if args.get("fix_scale") and _fix_scale(o):
            scales_fixed_total += 1
        if args.get("fill_holes"):
            holes_filled_total += _fill_holes(o)
        if args.get("recalc_normals") and _has_non_manifold(o):
            warnings.append(
                f"{o.name}: --recalc-normals was applied to a non-manifold mesh -- this can produce "
                "wrong results (verified: it visibly corrupted a real correctly-shaded character mesh "
                "in testing) rather than fixing anything; --fill-holes first, then re-check with "
                "info.py before trusting this output"
            )
        welded_total += _weld_and_triangulate(
            o, weld=bool(args.get("weld_doubles")), triangulate=bool(args.get("triangulate")),
            recalc_normals=bool(args.get("recalc_normals")),
        )
        if decimate_ratio and 0 < decimate_ratio < 1:
            bpy.context.view_layer.objects.active = o
            _decimate(o, decimate_ratio)
        if args.get("origin") and args["origin"] != "keep":
            _set_origin(o, args["origin"])

    auto_caps = {}
    if args.get("texture_auto_resolution"):
        auto_caps = _auto_texture_max(mesh_objs, args.get("viewport_width") or 1920)
    if texture_max:
        resized = _resize_textures(texture_max)
    elif auto_caps:
        resized = _resize_textures_per_image(auto_caps)
    else:
        resized = []
    purged = _purge_unused() if args.get("purge_unused") else 0

    colorspace_fixed = 0
    if args.get("fix_colorspace"):
        colorspace_fixed = _fix_colorspace(list(bpy.data.materials))
    texture_colorspace_changed = []
    if args.get("texture_colorspace"):
        texture_colorspace_changed = _set_all_colorspace(args["texture_colorspace"])

    after_tris = sum(_compat.object_triangle_count(o) for o in mesh_objs)
    export_kwargs = {}
    if args.get("webp") and args["output_format"] == "gltf":
        # export_image_add_webp/export_image_webp_fallback both default to False already (a
        # WebP-only image, no PNG/JPEG fallback copy also embedded) -- exactly what a format
        # *conversion* wants, so left at their defaults rather than set explicitly.
        export_kwargs["export_image_format"] = "WEBP"
        if args.get("webp_quality"):
            export_kwargs["export_image_quality"] = args["webp_quality"]
    _compat.export_file(args["output"], args["output_format"], **export_kwargs)

    return {
        "triangles_before": before_tris,
        "triangles_after": after_tris,
        "vertices_welded": welded_total,
        "holes_filled": holes_filled_total,
        "scales_fixed": scales_fixed_total,
        "points_thinned": points_thinned_total,
        "textures_resized": resized,
        "texture_auto_caps": auto_caps,
        "orphan_data_purged": purged,
        "colorspace_fixed": colorspace_fixed,
        "texture_colorspace_changed": texture_colorspace_changed,
        "output_path": args["output"],
        "warnings": warnings,
    }


if __name__ == "__main__":
    _common.main(run)
