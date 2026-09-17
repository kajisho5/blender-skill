"""bpy side of optimize.py: decimate, weld, recalc normals, triangulate, cap texture size,
purge unused data blocks -- each independently toggleable, plus three presets that bundle
sensible defaults for a destination.

Strips Blender's own synthesized bone-shape display widget after import (see
_compat.strip_import_helper_objects and references/pitfalls.md) -- without it, a skinned
armature input's reported triangles_before/triangles_after were inflated by the widget's own
triangles (confirmed on fox.glb: 656 instead of the real 576), and --texture-auto-resolution's
scene-bounding-box calculation would be skewed by an object that isn't really part of the asset.
"""
import re
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


def _skinned_to(armature_obj, obj) -> bool:
    """True if `obj` has an Armature modifier targeting `armature_obj` or any other object that
    shares the same armature data -- multiple objects can be linked to one shared armature
    datablock (e.g. duplicated rigs with the same skeleton), and a mesh bound to a sibling object
    is just as truly skinned to this bone data as one bound to `armature_obj` itself."""
    return any(
        m.type == "ARMATURE" and m.object is not None and m.object.data == armature_obj.data
        for m in obj.modifiers
    )


def _bone_influence(armature_obj, mesh_objs) -> dict:
    """bone name -> total vertex-weight sum across every mesh actually skinned to this armature's
    data (has an Armature modifier pointing at this object or a sibling sharing the same data) --
    how much real skinning influence that bone has, not just whether a same-named vertex group
    happens to exist. One pass over each mesh's own vertex/group membership, not one pass per
    vertex group, so cost is linear in the mesh's own weight data rather than groups x vertices.
    """
    influence = {}
    for obj in mesh_objs:
        if not _skinned_to(armature_obj, obj):
            continue
        names_by_index = {vg.index: vg.name for vg in obj.vertex_groups}
        for v in obj.data.vertices:
            for g in v.groups:
                if g.weight <= 0:
                    continue
                name = names_by_index.get(g.group)
                if name is None:
                    continue
                influence[name] = influence.get(name, 0.0) + g.weight
    return influence


def _animated_bone_names() -> set:
    """Every bone name targeted by a pose-bone F-curve in any action in the file (not just the
    active one) -- reusing the same 'pose.bones["Name"].prop' path pattern info.py's own
    root-motion detection matches, so a bone actually driving visible motion is never treated as
    safe to remove just because it happens to carry little or no skin weight. Blender escapes a
    quote or backslash inside the bone name itself (BLI_str_escape) when building the path, so the
    capture group has to treat `\\.` as one escaped unit rather than stopping at the first `"` --
    otherwise a bone name containing a literal quote would truncate the match and the bone could
    wrongly look unanimated. bpy.utils.unescape_identifier() reverses that escaping.
    """
    names = set()
    for action in bpy.data.actions:
        for fc in action.fcurves:
            m = re.match(r'pose\.bones\["((?:\\.|[^"\\])*)"\]', fc.data_path)
            if m:
                names.add(bpy.utils.unescape_identifier(m.group(1)))
    return names


def _transfer_bone_weight_to_parent(mesh_objs, armature_obj, bone_name: str, parent_name) -> None:
    """Moves a removed bone's own vertex-group weight onto its parent's vertex group (creating
    one if the parent had none) rather than just dropping it -- a vertex that was influenced by
    the removed bone stays attached to the rig, following the parent instead, rather than losing
    that influence outright. With no parent (removing a root bone), the weight is simply
    discarded -- there is nothing left in the chain to reattach it to. Callers are expected to
    keep a weighted root bone out of consideration in the first place (see _cap_bone_count) since
    this function has nowhere to send that weight.
    """
    for obj in mesh_objs:
        if not _skinned_to(armature_obj, obj):
            continue
        vg_old = obj.vertex_groups.get(bone_name)
        if vg_old is None:
            continue
        if parent_name:
            vg_new = obj.vertex_groups.get(parent_name) or obj.vertex_groups.new(name=parent_name)
            for v in obj.data.vertices:
                for g in v.groups:
                    if g.group == vg_old.index and g.weight > 0:
                        vg_new.add([v.index], g.weight, 'ADD')
        obj.vertex_groups.remove(vg_old)


def _remove_unused_bones(armature_obj, mesh_objs) -> list:
    """Iteratively prunes leaf bones with zero skin-weight influence and no animation, from the
    leaves inward -- a bone only becomes eligible once its own children are gone too, so a bone
    that's only a structural parent of a still-used descendant is never orphaned out from under
    it. Genuinely dead weight (no mesh references it, nothing animates it): safe to drop outright
    (no vertex depends on it, so there is nothing to transfer), unlike _cap_bone_count's more
    aggressive reduction. Returns the removed bone names.
    """
    animated = _animated_bone_names()
    influence = _bone_influence(armature_obj, mesh_objs)
    removed = []
    bpy.context.view_layer.objects.active = armature_obj
    bpy.ops.object.mode_set(mode='EDIT')
    changed = True
    while changed:
        changed = False
        for eb in list(armature_obj.data.edit_bones):
            if eb.children:
                continue
            if influence.get(eb.name, 0.0) > 1e-6 or eb.name in animated:
                continue
            removed.append(eb.name)
            armature_obj.data.edit_bones.remove(eb)
            changed = True
    bpy.ops.object.mode_set(mode='OBJECT')
    for name in removed:
        for obj in mesh_objs:
            vg = obj.vertex_groups.get(name)
            if vg:
                obj.vertex_groups.remove(vg)
    return removed


def _cap_bone_count(armature_obj, mesh_objs, max_bones: int):
    """Reduces this armature to at most max_bones by repeatedly removing the lowest-skin-
    influence *unanimated* leaf bone and transferring its weight to its parent (see
    _transfer_bone_weight_to_parent) -- a real, mobile-engine-style bone-budget cap, not just
    info.py-style reporting. Genuinely unused bones (zero influence) always sort first and get
    removed for free before any meaningfully-weighted bone is touched. Never removes a bone with
    its own animation, even if the cap can't be reached without one; a weighted *root* leaf (no
    parent to receive its weight) is protected the same way, since _transfer_bone_weight_to_parent
    has nowhere to send that weight -- either case stops the reduction short with a warning rather
    than silently breaking visible motion or dropping skin weight to hit an exact number.

    `animated` and the starting `influence` map are both computed once, before the loop, rather
    than re-scanning fcurves/vertex data on every single bone removed: nothing in this loop adds
    animation, and every weight change it makes (the transfer to a victim's parent) is mirrored
    into the cached map in the same step, so the cache never drifts from the real vertex data.
    """
    demoted = []
    warning = None
    animated = _animated_bone_names()
    influence = _bone_influence(armature_obj, mesh_objs)
    while len(armature_obj.data.bones) > max_bones:
        bpy.context.view_layer.objects.active = armature_obj
        bpy.ops.object.mode_set(mode='EDIT')
        candidates = [
            eb for eb in armature_obj.data.edit_bones
            if not eb.children
            and eb.name not in animated
            and not (eb.parent is None and influence.get(eb.name, 0.0) > 1e-6)
        ]
        if not candidates:
            bpy.ops.object.mode_set(mode='OBJECT')
            warning = (
                f"{armature_obj.name}: could not reduce to {max_bones} bones without removing an "
                f"animated bone or a weighted root bone -- stopped at {len(armature_obj.data.bones)}"
            )
            break
        candidates.sort(key=lambda eb: influence.get(eb.name, 0.0))
        victim = candidates[0]
        victim_name = victim.name
        parent_name = victim.parent.name if victim.parent else None
        armature_obj.data.edit_bones.remove(victim)
        bpy.ops.object.mode_set(mode='OBJECT')
        _transfer_bone_weight_to_parent(mesh_objs, armature_obj, victim_name, parent_name)
        if parent_name:
            influence[parent_name] = influence.get(parent_name, 0.0) + influence.get(victim_name, 0.0)
        influence.pop(victim_name, None)
        demoted.append({"bone": victim_name, "reassigned_to": parent_name})
    return demoted, warning


_EXACTLY_EVALUABLE_INTERPOLATIONS = {"LINEAR", "CONSTANT"}


def _decimate_fcurve(fcurve, tolerance: float) -> tuple:
    """Reduces one fcurve's keyframe count with Ramer-Douglas-Peucker curve simplification. A
    keyframe is only ever removed from a segment whose *governing* keyframe (the one that starts
    it -- interpolation is a per-keyframe property describing the segment TO the next surviving
    keyframe) uses LINEAR or CONSTANT interpolation, the two modes where the exact value
    Blender's own curve holds at any frame after the removal is cheaply computable: a straight
    line for LINEAR, or a flat step holding the segment-start's value for CONSTANT. Any other
    interpolation (BEZIER -- Blender's own default for a hand-keyed action -- and the named
    easing curves) is left completely untouched: reproducing Blender's real Bezier evaluation
    (frame is itself a cubic function of the curve parameter, via each keyframe's own handle
    positions) exactly enough to still guarantee the requested tolerance is out of scope, and an
    approximation risks silently exceeding it -- verified: naively measuring error against the
    *straight line* between two CONSTANT-governed keyframes (rather than the real flat step) can
    accept a keyframe whose real removal error is far larger than the straight-line estimate
    (worked example in references/pitfalls.md). This is not a hobbled feature for this skill's
    primary real input, though: every fcurve Blender's own glTF importer produces uses LINEAR
    interpolation (verified on fox.glb -- 10458/10458 keyframe points, zero exceptions).

    The first and last keyframes always survive. Returns (keyframes_before, keyframes_after).
    """
    points = [(kp.co.x, kp.co.y, kp.interpolation) for kp in fcurve.keyframe_points]
    n = len(points)
    if n < 3:
        return n, n
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        start_i, end_i = stack.pop()
        if end_i - start_i < 2:
            continue
        start_frame, start_val, start_interp = points[start_i]
        if start_interp not in _EXACTLY_EVALUABLE_INTERPOLATIONS:
            for i in range(start_i + 1, end_i):
                keep[i] = True
            continue
        end_frame, end_val, _ = points[end_i]
        span = end_frame - start_frame
        max_err = -1.0
        max_idx = -1
        for i in range(start_i + 1, end_i):
            frame, val, _ = points[i]
            if start_interp == "CONSTANT":
                interp = start_val
            else:
                interp = start_val if span == 0 else start_val + (frame - start_frame) / span * (end_val - start_val)
            err = abs(val - interp)
            if err > max_err:
                max_err = err
                max_idx = i
        if max_err > tolerance:
            keep[max_idx] = True
            stack.append((start_i, max_idx))
            stack.append((max_idx, end_i))
    # Remove in descending index order: removing a higher index never shifts a lower, still-
    # unprocessed one, so every index computed above stays valid throughout.
    for i in range(n - 1, -1, -1):
        if not keep[i]:
            fcurve.keyframe_points.remove(fcurve.keyframe_points[i], fast=True)
    fcurve.update()
    return n, sum(keep)


def _decimate_keyframes(tolerance: float) -> dict:
    """Reduces keyframe count on every fcurve of every action in the file (whichever ones end up
    exported is the exporter's own choice -- see anim.py/_compat.export_multi_action -- so every
    action is decimated the same way regardless, harmless for one that isn't exported). Uses a
    from-scratch Ramer-Douglas-Peucker implementation (_decimate_fcurve) rather than Blender's own
    Graph Editor `graph.decimate` operator: that operator needs a live Graph Editor area/region to
    run against, and true headless Blender (`-b`) has no window or screen at all to provide one.
    """
    before_total = 0
    after_total = 0
    by_action = []
    for action in bpy.data.actions:
        action_before = 0
        action_after = 0
        for fc in action.fcurves:
            b, a = _decimate_fcurve(fc, tolerance)
            action_before += b
            action_after += a
        before_total += action_before
        after_total += action_after
        if action_before:
            by_action.append({
                "action": action.name,
                "keyframe_points_before": action_before,
                "keyframe_points_after": action_after,
            })
    return {
        "keyframe_points_before": before_total,
        "keyframe_points_after": after_total,
        "by_action": by_action,
    }


_SHAPE_KEY_EPSILON = 1e-5  # matches this file's own weld-doubles vertex-position precision


def _shape_key_max_displacement(key_block) -> float:
    """Max per-vertex Euclidean distance between this shape key's own point data and whatever
    it's actually defined relative to (`relative_key` -- usually Basis, but a shape key can be
    defined relative to another shape key instead) -- the real geometric contribution this key
    adds, independent of its current `.value` slider or `.mute` state or any driver pointed at
    it: a key that's geometrically identical to its relative_key does nothing no matter how it's
    driven.
    """
    base = key_block.relative_key
    max_d = 0.0
    for kp, base_kp in zip(key_block.data, base.data):
        d = (kp.co - base_kp.co).length
        if d > max_d:
            max_d = d
    return max_d


def _remove_unused_shape_keys(mesh_objs) -> list:
    """Removes every non-Basis shape key whose max displacement from its own relative_key is
    below a tiny fixed epsilon -- geometrically a no-op regardless of value/mute/drivers. Runs as
    a fixpoint loop (see references/pitfalls.md's bone-pruning entry for why) since removing one
    dead key can turn a *chain* relative to it into a newly-measurable-as-dead key too: Blender's
    own obj.shape_key_remove() automatically re-points every shape key that referenced the removed
    one's `relative_key` onto *its* relative_key (verified directly against real Blender), so a
    multi-level dead chain resolves correctly without this function managing the chain itself.

    Skips any mesh using Absolute shape keys (`Key.use_relative == False`) entirely: there,
    `relative_key`/displacement isn't what actually drives the shape at all -- each key is a
    timed sequence state selected by `eval_time`, interpolated against its *neighbors in the key
    stack* (LINEAR/CARDINAL/CATMULL_ROM/BSPLINE), so a key that happens to be geometrically
    identical to its relative_key can still be a real, load-bearing waypoint in that sequence's
    shape. `shape_key_add()` sets `use_relative = True` by default (verified directly), so this
    never affects a glTF import or any file whose shape keys were authored the normal way --
    Absolute mode is an explicit, comparatively rare choice.
    """
    removed = []
    for obj in mesh_objs:
        sk = obj.data.shape_keys
        if not sk or not sk.use_relative:
            continue
        reference = sk.reference_key
        changed = True
        while changed:
            changed = False
            for kb in list(sk.key_blocks):
                if kb == reference:
                    continue
                d = _shape_key_max_displacement(kb)
                if d <= _SHAPE_KEY_EPSILON:
                    removed.append({"mesh": obj.name, "shape_key": kb.name, "max_displacement": d})
                    obj.shape_key_remove(kb)
                    changed = True
    return removed


def _mesh_is_animatable(obj) -> bool:
    """True if instancing this object's mesh data with another's could silently change
    animated/deformed behavior, regardless of what the equality check below would otherwise
    conclude: shape key *values* live on the shared Key datablock (so merging two objects with
    shape keys would collapse their potentially-different current blend state into one shared
    state), and per-vertex bone weights live on the shared mesh's own vertex data, not the
    object (so merging two skinned objects would force them to share identical weight
    assignments). Both are real Blender data-model facts, not assumptions -- excluded from
    instancing candidacy entirely.
    """
    if obj.data.shape_keys:
        return True
    return any(m.type == "ARMATURE" for m in obj.modifiers)


def _bool_attr_values(mesh, name: str, count: int) -> list:
    """A per-element boolean attribute (e.g. 'sharp_face'/'sharp_edge') exists only once some
    element has a non-default value -- absent means every element is still the default (False).
    Reading it this way, rather than requiring the attribute to exist, avoids two meshes that are
    genuinely equivalent (all-smooth, no sharp marks on either) differing only in whether Blender
    happened to have created the (all-False) attribute at all.
    """
    attr = mesh.attributes.get(name)
    if attr is None:
        return [False] * count
    return [bool(item.value) for item in attr.data]


def _mesh_fully_identical(mesh_a, mesh_b) -> bool:
    """True mesh-data equality -- not info.py's own duplicate-mesh signature (a rotation/
    translation-invariant fingerprint deliberately loose enough for a human-reviewed suggestion,
    not safe grounds for an automatic merge). Checks vertex count and every vertex's exact
    position (index-for-index, within a 1e-6 floating-point tolerance -- not byte-for-byte, since
    two meshes built the same way can still differ in the last bit or two), face topology
    (identical polygon count, the same vertex-index sequence per face and the same winding
    order, the same material_index and use_smooth per face), sharp-face/sharp-edge marks and
    whether either has custom split normals (if either does, never merged at all -- comparing
    Blender's real per-corner split-normal solve exactly enough to guarantee identical shading is
    out of scope, and guessing risks a visible seam/shading change post-merge, the same reasoning
    RM-035 applied to BEZIER-interpolated keyframes), every UV layer's name/active-render flag
    and coordinates, every color attribute's name/values and the mesh's effective color-attribute
    render fallback (default_color_name/render_color_index -- not the display-only
    active_color_index, which affects nothing about how a material samples it), and that both
    meshes reference the exact same Material datablocks in the same slot order -- not materials
    that merely look the same, literally the same datablocks. A material's own node tree can look
    up a UV layer or color attribute by name (e.g. ShaderNodeUVMap.uv_map), so two meshes with
    identical UV/color *values* under different layer names or a different render-fallback
    selection could still make the same shared material sample different data for each -- caught
    by review; see references/pitfalls.md.
    """
    if len(mesh_a.vertices) != len(mesh_b.vertices) or len(mesh_a.polygons) != len(mesh_b.polygons):
        return False
    if len(mesh_a.edges) != len(mesh_b.edges):
        return False
    if list(mesh_a.materials) != list(mesh_b.materials):
        return False
    if mesh_a.has_custom_normals or mesh_b.has_custom_normals:
        return False
    for va, vb in zip(mesh_a.vertices, mesh_b.vertices):
        if (va.co - vb.co).length > 1e-6:
            return False
    for fa, fb in zip(mesh_a.polygons, mesh_b.polygons):
        if list(fa.vertices) != list(fb.vertices):
            return False
        if fa.material_index != fb.material_index or fa.use_smooth != fb.use_smooth:
            return False
    if _bool_attr_values(mesh_a, "sharp_face", len(mesh_a.polygons)) != _bool_attr_values(mesh_b, "sharp_face", len(mesh_b.polygons)):
        return False
    if _bool_attr_values(mesh_a, "sharp_edge", len(mesh_a.edges)) != _bool_attr_values(mesh_b, "sharp_edge", len(mesh_b.edges)):
        return False
    if len(mesh_a.uv_layers) != len(mesh_b.uv_layers):
        return False
    for la, lb in zip(mesh_a.uv_layers, mesh_b.uv_layers):
        if la.name != lb.name or la.active_render != lb.active_render:
            return False
        for da, db in zip(la.data, lb.data):
            if (da.uv - db.uv).length > 1e-6:
                return False
    if len(mesh_a.color_attributes) != len(mesh_b.color_attributes):
        return False
    if mesh_a.color_attributes.default_color_name != mesh_b.color_attributes.default_color_name:
        return False
    if mesh_a.color_attributes.render_color_index != mesh_b.color_attributes.render_color_index:
        return False
    for ca, cb in zip(mesh_a.color_attributes, mesh_b.color_attributes):
        if ca.name != cb.name or ca.domain != cb.domain or ca.data_type != cb.data_type:
            return False
        for da, db in zip(ca.data, cb.data):
            if tuple(da.color) != tuple(db.color):
                return False
    return True


def _instance_duplicate_meshes(mesh_objs) -> list:
    """Merges every group of mesh objects on *separate* datablocks that are fully identical
    (_mesh_fully_identical) onto one shared datablock, removing the now-orphaned duplicates --
    the automatic counterpart to info.py's own duplicate_mesh_candidates suggestion, safe to run
    unattended because it only merges what's proven identical, not just similarly shaped.
    Objects already sharing one datablock are never touched (already efficient); a datablock is
    excluded from candidacy *entirely* if ANY object using it fails _mesh_is_animatable -- a
    datablock can be shared by several objects (e.g. a linked duplicate), and one shape-keyed/
    skinned user is enough to make the whole shared datablock unsafe to reassign or remove, even
    if some of its other users would individually have been eligible (caught by review: grouping
    by pre-filtered "eligible" objects only, before grouping by datablock, could otherwise still
    remove a datablock a non-eligible object was quietly still pointing at). Buckets candidates
    by (vertex count, polygon count) first so the expensive exact-equality check only ever runs
    within a group of comparably-sized meshes, not every pair in the file.
    """
    by_data = {}
    for o in mesh_objs:
        by_data.setdefault(o.data, []).append(o)
    by_data = {data: objs for data, objs in by_data.items() if not any(_mesh_is_animatable(o) for o in objs)}

    buckets = {}
    for data, objs in by_data.items():
        key = (len(data.vertices), len(data.polygons))
        buckets.setdefault(key, []).append((data, objs))

    merged = []
    for bucket in buckets.values():
        if len(bucket) < 2:
            continue
        unassigned = list(bucket)
        while unassigned:
            data, objs = unassigned.pop(0)
            group = [(data, objs)]
            remaining = []
            for other_data, other_objs in unassigned:
                if _mesh_fully_identical(data, other_data):
                    group.append((other_data, other_objs))
                else:
                    remaining.append((other_data, other_objs))
            unassigned = remaining
            if len(group) < 2:
                continue
            canonical_data, canonical_objs = group[0]
            merged_names = []
            removed_data_names = []
            for dup_data, dup_objs in group[1:]:
                for o in dup_objs:
                    o.data = canonical_data
                    merged_names.append(o.name)
                removed_data_names.append(dup_data.name)
                bpy.data.meshes.remove(dup_data)
            merged.append({
                "canonical_mesh": canonical_data.name,
                "canonical_objects": [o.name for o in canonical_objs],
                "merged_objects": merged_names,
                "datablocks_removed": removed_data_names,
            })
    return merged


_MATERIAL_IGNORED_PROPS = {
    "rna_type", "name", "name_full", "node_tree", "original", "id_data", "library",
    "library_weak_reference", "override_library", "preview", "users", "use_fake_user",
    "is_embedded_data", "is_evaluated", "session_uid", "tag", "asset_data",
}

_NODE_IGNORED_PROPS = {
    "rna_type", "name", "label", "select", "location", "location_absolute", "width",
    "width_hidden", "height", "dimensions", "show_expanded", "show_options", "show_preview",
    "show_texture", "hide", "parent", "internal_links", "color", "use_custom_color", "type",
    "inputs", "outputs", "bl_idname", "bl_label", "bl_description", "bl_icon", "bl_static_type",
    "bl_width_default", "bl_width_min", "bl_width_max", "bl_height_default", "bl_height_min",
    "bl_height_max",
}


_MAX_STRUCT_DEPTH = 8  # guards against a pathological/cyclic property graph; never hit by any
                        # real node or material property structure, which is always tree-shaped


def _is_nested_value(value) -> bool:
    """True for anything `_value_key` needs to walk INTO rather than compare directly with `==`
    -- an ID datablock, a non-ID struct (ImageUser, ColorRamp, ...), or a collection."""
    return isinstance(value, bpy.types.bpy_struct) or (hasattr(value, "__len__") and not isinstance(value, (str, bytes)))


def _value_key(value, depth=0):
    """Turns an arbitrary RNA property value into something plain `==` compares correctly for
    the purpose of "would this look/render identically". An ID datablock (Image, NodeTree, Text,
    ...) compares by its own identity (Blender's own bpy_struct `==` is a pointer compare for an
    ID, which is exactly right here -- two different Image datablocks are different textures even
    if they happen to hold the same pixels, so two materials that sample them are not safe to
    treat as interchangeable). A *non-ID* nested struct (ImageUser, ColorRamp, CurveMapping, ...)
    is walked recursively instead of compared by `==` directly: two separate node instances always
    own two separate struct instances, so a plain `==` there is a pointer compare between objects
    that can never be the same object even when every field inside genuinely matches -- caught
    during development, since it would have silently blocked merging any material with an Image
    Texture node at all (every one owns its own distinct ImageUser struct).
    """
    if isinstance(value, bpy.types.ID):
        return value
    if depth >= _MAX_STRUCT_DEPTH:
        return value
    if isinstance(value, bpy.types.bpy_struct):
        return _struct_signature(value, depth + 1)
    if hasattr(value, "__len__") and not isinstance(value, (str, bytes)):
        try:
            return tuple(_value_key(v, depth + 1) for v in value)
        except TypeError:
            return value
    if isinstance(value, float):
        return round(value, 6)
    return value


def _struct_signature(struct, depth=0) -> tuple:
    sig = []
    for prop in struct.bl_rna.properties:
        if prop.identifier == "rna_type":
            continue
        value = getattr(struct, prop.identifier, None)
        if prop.is_readonly and not _is_nested_value(value):
            continue
        sig.append((prop.identifier, _value_key(value, depth)))
    return tuple(sig)


def _rna_props_equal(a, b, ignored: set) -> bool:
    """Compares every non-ignored RNA property of `a` and `b`. A property Blender marks
    `is_readonly` is skipped only when its value is a plain scalar/array -- genuinely derived,
    computed info that can't drift out of sync (e.g. a node's `dimensions`). Blender also marks a
    POINTER property `is_readonly` whenever the *pointer itself* can't be reassigned, which is
    just as true for a still-freely-mutable non-ID struct (ImageUser, ColorRamp, CurveMapping,
    Mapping, ...) as for an ID reference -- naively skipping every readonly property would treat
    two Color Ramp nodes with completely different ramps as identical (`color_ramp` is one such
    readonly-pointer-to-mutable-struct property); caught during development and reproduced
    directly before this guard was added.
    """
    for prop in a.bl_rna.properties:
        pid = prop.identifier
        if pid in ignored:
            continue
        va = getattr(a, pid, None)
        vb = getattr(b, pid, None)
        if prop.is_readonly and not _is_nested_value(va) and not _is_nested_value(vb):
            continue
        if isinstance(va, bpy.types.ID) or isinstance(vb, bpy.types.ID):
            if va != vb:
                return False
            continue
        if _value_key(va) != _value_key(vb):
            return False
    return True


def _socket_default_equal(sock_a, sock_b) -> bool:
    if sock_a.is_linked != sock_b.is_linked:
        return False
    if sock_a.is_linked:
        return True  # the driving link itself is part of the node tree's own link-set comparison
    if not hasattr(sock_a, "default_value"):
        return True  # a socket type with nothing to hold constant (e.g. NodeSocketShader)
    return _value_key(sock_a.default_value) == _value_key(sock_b.default_value)


def _node_fully_identical(node_a, node_b) -> bool:
    if node_a.bl_idname != node_b.bl_idname:
        return False
    if node_a.mute != node_b.mute:
        return False
    if len(node_a.inputs) != len(node_b.inputs) or len(node_a.outputs) != len(node_b.outputs):
        return False
    if not _rna_props_equal(node_a, node_b, _NODE_IGNORED_PROPS):
        return False
    return all(_socket_default_equal(sa, sb) for sa, sb in zip(node_a.inputs, node_b.inputs))


def _link_signature(link) -> tuple:
    return (link.from_node.name, link.from_socket.identifier, link.to_node.name, link.to_socket.identifier)


def _node_tree_fully_identical(tree_a, tree_b) -> bool:
    """True structural equality: the same set of node *names* (Blender auto-assigns identical
    default names -- "Principled BSDF", "Image Texture", "Image Texture.001", ... -- to two node
    trees built the same way, e.g. by the same importer/generator run twice, which is exactly the
    real-world "duplicate material" case this is for), each matched pair of same-named nodes
    identical per _node_fully_identical, and the exact same set of links between them. Two node
    trees built by hand with genuinely different node names for equivalent graphs won't match --
    conservative, not wrong: never merges two materials whose renders could actually differ.
    """
    if tree_a is tree_b:
        return True
    if len(tree_a.nodes) != len(tree_b.nodes) or len(tree_a.links) != len(tree_b.links):
        return False
    names_a = {n.name for n in tree_a.nodes}
    if names_a != {n.name for n in tree_b.nodes}:
        return False
    for name in names_a:
        if not _node_fully_identical(tree_a.nodes[name], tree_b.nodes[name]):
            return False
    links_a = {_link_signature(link) for link in tree_a.links}
    links_b = {_link_signature(link) for link in tree_b.links}
    return links_a == links_b


def _material_fully_identical(mat_a, mat_b) -> bool:
    """True material equality, not just "looks similar": every non-identity RNA property on the
    Material datablock itself (blend_method, use_backface_culling, the legacy diffuse_color/
    metallic/roughness fallback used when use_nodes is False, any add-on-registered settings such
    as `.cycles`, ...), plus -- when use_nodes is True -- full node-tree structural equality
    (_node_tree_fully_identical). A ShaderNodeGroup's own `node_tree` is an ID pointer compared by
    identity like any other ID property: two materials referencing the *same* node-group
    datablock are equal on that node; two referencing separately-authored node groups are not,
    even if those groups are themselves structurally identical -- recursing into a referenced
    node group's own internal graph is out of scope, the same "exact but not exhaustive" scope
    boundary RM-035 drew at BEZIER interpolation and RM-037 drew at custom split normals.
    """
    if mat_a is mat_b:
        return True
    if not _rna_props_equal(mat_a, mat_b, _MATERIAL_IGNORED_PROPS):
        return False
    if mat_a.use_nodes:
        if not mat_a.node_tree or not mat_b.node_tree:
            return False
        return _node_tree_fully_identical(mat_a.node_tree, mat_b.node_tree)
    return True


def _reassign_material_users(dup, canonical) -> None:
    """Redirects every reference to `dup` onto `canonical` via Blender's own `ID.user_remap` --
    not a hand-enumerated walk of mesh.materials and OBJECT-linked object.material_slots (this
    function's first version), which misses any *other* material user Blender's own data model
    has (a Curve/Text/MetaBall/GreasePencil/Volume object's own `.materials` list, a node group
    referencing this material, ...): `user_remap` covers every real ID user in one call, verified
    directly to also correctly redirect an OBJECT-linked slot override (not just the common
    DATA-linked case), leaving `dup.users == 0` afterward. Caught by review.
    """
    dup.user_remap(canonical)


def _merge_identical_materials() -> list:
    """Merges every group of separate Material datablocks that are fully identical
    (_material_fully_identical) onto one canonical datablock, reassigning every mesh-data material
    slot and every per-object material-slot override (`link == 'OBJECT'`) that pointed at a
    duplicate before removing it. Buckets first by (use_nodes, node count, link count) so the
    expensive structural check only ever runs within a group of comparably-shaped materials, not
    every pair in the file -- the same bucket-then-verify shape _instance_duplicate_meshes (RM-037)
    uses for meshes.
    """
    materials = [m for m in bpy.data.materials if m.users > 0]
    buckets = {}
    for mat in materials:
        node_count = len(mat.node_tree.nodes) if mat.use_nodes and mat.node_tree else 0
        link_count = len(mat.node_tree.links) if mat.use_nodes and mat.node_tree else 0
        buckets.setdefault((mat.use_nodes, node_count, link_count), []).append(mat)

    merged = []
    for bucket in buckets.values():
        if len(bucket) < 2:
            continue
        unassigned = list(bucket)
        while unassigned:
            mat = unassigned.pop(0)
            group = [mat]
            remaining = []
            for other in unassigned:
                if _material_fully_identical(mat, other):
                    group.append(other)
                else:
                    remaining.append(other)
            unassigned = remaining
            if len(group) < 2:
                continue
            canonical = group[0]
            merged_names = []
            for dup in group[1:]:
                _reassign_material_users(dup, canonical)
                merged_names.append(dup.name)
                bpy.data.materials.remove(dup)
            merged.append({"canonical_material": canonical.name, "merged_materials": merged_names})
    return merged


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

    bones_removed = []
    bones_demoted = []
    if args.get("remove_unused_bones") or args.get("max_bones"):
        # Two ARMATURE objects can share one armature datablock (a linked duplicate rig) --
        # process each shared datablock once, not once per object pointing at it, so the second
        # object's own skinned meshes are never missed and its bones never edited twice.
        seen_arm_data = set()
        for arm in [o for o in bpy.data.objects if o.type == "ARMATURE"]:
            if arm.data in seen_arm_data:
                continue
            seen_arm_data.add(arm.data)
            if args.get("remove_unused_bones"):
                bones_removed.extend(_remove_unused_bones(arm, mesh_objs))
            if args.get("max_bones"):
                demoted, bone_warning = _cap_bone_count(arm, mesh_objs, args["max_bones"])
                bones_demoted.extend(demoted)
                if bone_warning:
                    warnings.append(bone_warning)

    keyframes_decimated = {"keyframe_points_before": 0, "keyframe_points_after": 0, "by_action": []}
    if args.get("keyframe_decimate"):
        keyframes_decimated = _decimate_keyframes(args["keyframe_decimate"])

    shape_keys_removed = []
    if args.get("remove_unused_shape_keys"):
        shape_keys_removed = _remove_unused_shape_keys(mesh_objs)

    meshes_instanced = []
    if args.get("instance_duplicate_meshes"):
        meshes_instanced = _instance_duplicate_meshes(mesh_objs)

    materials_merged = []
    if args.get("merge_materials"):
        materials_merged = _merge_identical_materials()

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
        "bones_removed": bones_removed,
        "bones_demoted": bones_demoted,
        "keyframe_points_before": keyframes_decimated["keyframe_points_before"],
        "keyframe_points_after": keyframes_decimated["keyframe_points_after"],
        "keyframes_decimated_by_action": keyframes_decimated["by_action"],
        "shape_keys_removed": shape_keys_removed,
        "meshes_instanced": meshes_instanced,
        "materials_merged": materials_merged,
        "output_path": args["output"],
        "warnings": warnings,
    }


if __name__ == "__main__":
    _common.main(run)
