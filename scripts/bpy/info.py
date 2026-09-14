"""bpy side of info.py: load one file, measure it, report facts -- never opinions.

This is the foundation every other script leans on (`inspect -> operate -> verify`): convert.py
diffs two info() calls to validate a round-trip, check.py compares an info() result against a
target's budget, optimize.py decides what needs shrinking from these same numbers.
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


def _unweighted_vertex_count(obj):
    """How many of this mesh's vertices have no (or all-zero) vertex-group weight, on a mesh
    that has an Armature modifier -- i.e. will not move with any bone, a real skinning defect.

    Gated on having an Armature modifier at all: a mesh with no vertex groups and no armature
    modifier isn't a skinned mesh in the first place (a static prop, an eye mesh parented
    directly to a bone by object transform rather than vertex weights) -- reporting every one of
    its vertices as "unweighted" would be noise, not a defect. Confirmed on fox.glb: the rigged
    "fox" mesh reads 0/1728 (fully weighted); the unrigged "Icosphere" eye mesh has no armature
    modifier at all and is correctly skipped (None), not reported as 42/42 unweighted.
    """
    if not any(m.type == "ARMATURE" for m in obj.modifiers):
        return None
    count = 0
    for v in obj.data.vertices:
        total = sum(g.weight for g in v.groups)
        if total <= 1e-6:
            count += 1
    return count


def _draw_call_estimate(obj) -> int:
    """One draw call per distinct material actually used by a face on this object -- refines the
    naive "material count x object count" idea (which wildly overestimates when objects share
    materials) into the real batching unit most real-time engines use: consecutive triangles
    share a draw call only while they're the same (object, material) pair. A mesh with no
    material slots at all (confirmed on fox.glb's "Icosphere" eye mesh) still reads every face's
    material_index as 0 -- still a single real draw call for the engine's default material, not
    zero. Does not model engine-specific batching/GPU instancing of identical meshes -- a
    starting-point estimate, not a guaranteed number.
    """
    return len({p.material_index for p in obj.data.polygons})


def _wall_thickness_min(bm, sample_limit=2000):
    """Local wall thickness at a sample of faces: ray-cast from each face's center along its own
    negative normal (offset by a small epsilon to avoid immediately self-hitting the origin
    face) and take the hit distance to the nearest opposing surface -- the same "thickness
    analysis" technique Blender's own 3D-Print Toolbox (an Extension as of 4.2+, not bundled, so
    not something this skill can assume is installed) uses. Returns None if not a single face
    got a hit (an open/non-manifold shape with no "inside" for a ray to reach), otherwise the
    minimum distance found across the sampled faces -- in the mesh's own local units, since STL
    (this check's real target) carries no unit information at all; check.py's 3d-print target
    interprets this against its own assumed-millimeters budget.

    Sampled (evenly strided), not exhaustive, on a mesh with more than `sample_limit` faces --
    confirmed fast enough not to need this at all on real test assets (576 triangles in ~3ms),
    but a red-flag signal on a dense real-world mesh should stay bounded rather than ray-casting
    every single face.
    """
    faces = bm.faces[:]
    if not faces:
        return None
    stride = max(1, len(faces) // sample_limit)
    bvh = BVHTree.FromBMesh(bm)
    epsilon = 1e-4
    min_thickness = None
    for face in faces[::stride]:
        origin = face.calc_center_median() - face.normal * epsilon
        hit = bvh.ray_cast(origin, -face.normal)
        if hit[0] is not None:
            dist = hit[3]
            if min_thickness is None or dist < min_thickness:
                min_thickness = dist
    return min_thickness


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
    bm.normal_update()
    wall_thickness_min = _wall_thickness_min(bm)
    bm_welded.free()
    bm.free()
    uv_layers = len(obj.data.uv_layers)
    empty_slots = sum(1 for slot in obj.material_slots if slot.material is None)
    origin_to_center, origin_to_bottom_center = _local_bbox_reference_points(obj)
    vertex_colors, custom_attributes = _attribute_lists(obj)
    unweighted_vertices = _unweighted_vertex_count(obj)
    draw_calls_estimate = _draw_call_estimate(obj)
    return {
        "triangles": triangles,
        "vertices": vertex_count,
        # A pure point cloud (PLY's other common use besides a mesh: vertices only, no faces --
        # e.g. photogrammetry/LiDAR scan data) has 0 triangles but a nonzero vertex count. Every
        # face-based check below (manifold, self-intersection, wall thickness, ...) is
        # meaningless on one and reads as a trivial 0/None rather than a real defect.
        "is_point_cloud": triangles == 0 and vertex_count > 0,
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
        "unweighted_vertices": unweighted_vertices,
        "draw_calls_estimate": draw_calls_estimate,
        "wall_thickness_min": wall_thickness_min,
    }


def _find_alpha_image(mat):
    """Walk back from a material's Principled BSDF Alpha input to a directly-connected Image
    Texture node, if any. Only follows that one direct link -- doesn't attempt full node-graph
    evaluation for a more elaborate procedural/mixed alpha setup, only the common single-texture
    case.
    """
    if not mat.use_nodes or not mat.node_tree:
        return None
    bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if not bsdf:
        return None
    alpha_input = bsdf.inputs.get("Alpha")
    if not alpha_input or not alpha_input.is_linked:
        return None
    node = alpha_input.links[0].from_node
    return node.image if node.type == "TEX_IMAGE" else None


def _alpha_is_binary_mask(image):
    """True if this image's alpha channel reads as a hard mask (values clustered at 0.0/1.0,
    like a foliage/cutout texture) rather than a smooth gradient (glass, cloth, a frosted
    panel). None if there's no alpha channel at all, or no pixel data to sample.
    """
    if not image:
        return None
    pixels = image.pixels[:]  # forces the lazy load -- has_data/size are stale until this (see
                               # references/pitfalls.md's "has_data is False until you touch
                               # .pixels" entry)
    if not image.has_data or image.channels < 4:
        return None
    w, h = image.size
    if w == 0 or h == 0:
        return None
    channels = image.channels
    total = mid = 0
    for i in range(0, len(pixels), channels):
        a = pixels[i + 3]
        total += 1
        if 0.05 < a < 0.95:
            mid += 1
    return (mid / total) < 0.02 if total else None


_COLOR_SOCKETS = {"Base Color", "Emission Color"}
_DATA_SOCKETS = {"Metallic", "Roughness", "Alpha"}


def _connected_image(socket):
    """The Image directly feeding a Principled BSDF input via an Image Texture node, or None --
    same "one direct link, not a full node-graph evaluation" scope as _find_alpha_image."""
    if socket is None or not socket.is_linked:
        return None
    node = socket.links[0].from_node
    return node.image if node.type == "TEX_IMAGE" else None


def _normal_map_image(bsdf):
    """The Image feeding a material's Normal Map node, if the BSDF's Normal input is wired
    through one (the standard way a normal-map texture reaches Principled BSDF -- a raw Image
    Texture output never plugs directly into Normal)."""
    normal_input = bsdf.inputs.get("Normal")
    if normal_input is None or not normal_input.is_linked:
        return None
    node = normal_input.links[0].from_node
    if node.type != "NORMAL_MAP":
        return None
    return _connected_image(node.inputs.get("Color"))


def _colorspace_issues(materials):
    """Textures whose colorspace tag doesn't match what the socket they feed needs to be
    correctly interpreted: Base Color/Emission need 'sRGB' (that's the perceptual space the
    texture's own pixels were authored in); Metallic/Roughness/Alpha and a normal map's texture
    need 'Non-Color' (raw data, not a display color -- tagging one of these 'sRGB' by mistake
    makes Blender gamma-decode it before use, corrupting the actual values it's supposed to
    represent -- a real, well-known pipeline mistake, most damaging on a normal map: it silently
    warps the decoded normal vectors rather than throwing any error). A same-image mistake shows
    up once per socket it's wired to, since fixing it needs re-checking each socket anyway.
    """
    issues = []
    for mat in materials:
        if not mat.use_nodes or not mat.node_tree:
            continue
        bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if not bsdf:
            continue
        for socket_name in _COLOR_SOCKETS:
            image = _connected_image(bsdf.inputs.get(socket_name))
            if image and image.colorspace_settings.name != "sRGB":
                issues.append({
                    "material": mat.name, "socket": socket_name, "image": image.name,
                    "colorspace": image.colorspace_settings.name, "expected": "sRGB",
                })
        for socket_name in _DATA_SOCKETS:
            image = _connected_image(bsdf.inputs.get(socket_name))
            if image and image.colorspace_settings.name != "Non-Color":
                issues.append({
                    "material": mat.name, "socket": socket_name, "image": image.name,
                    "colorspace": image.colorspace_settings.name, "expected": "Non-Color",
                })
        normal_image = _normal_map_image(bsdf)
        if normal_image and normal_image.colorspace_settings.name != "Non-Color":
            issues.append({
                "material": mat.name, "socket": "Normal", "image": normal_image.name,
                "colorspace": normal_image.colorspace_settings.name, "expected": "Non-Color",
            })
    return issues


def _transparency_issues(materials):
    """Materials whose alpha texture is a hard/binary mask but use real alpha blending
    (surface_render_method == 'BLENDED') instead of the cheaper, sorting-artifact-free dithered
    mode ('DITHERED') -- the real, current-Blender-version equivalent of the classic "foliage
    material mistakenly set to Blend instead of Clip" mistake.

    Blender 4.2's EEVEE Next replaced the old 4-mode Material.blend_method (OPAQUE/CLIP/HASHED/
    BLEND) with a 2-mode Material.surface_render_method (DITHERED/BLENDED) -- confirmed on real
    4.2.23, 4.5.13 and 5.2.1 that blend_method's own bl_rna enum still lists all 4 legacy values,
    but assigning 'OPAQUE' or 'CLIP' to it silently no-ops (the property keeps its previous
    value); surface_render_method is the real, current, authoritative property, so this checks
    that one directly rather than trusting blend_method's enum listing. See
    references/pitfalls.md -- this is the same "bl_rna enum isn't authoritative for real
    behavior" lesson this project has hit before, not a new kind of surprise.
    """
    issues = []
    for mat in materials:
        image = _find_alpha_image(mat)
        if image is None:
            continue
        is_binary = _alpha_is_binary_mask(image)
        if is_binary and mat.surface_render_method == "BLENDED":
            issues.append({
                "material": mat.name,
                "issue": (
                    "alpha texture looks like a hard mask, but this material uses real alpha "
                    "blending (BLENDED) -- try surface_render_method = 'DITHERED' instead "
                    "(cheaper, avoids transparency sorting-order artifacts, no visual downside "
                    "for a mask that's already just 0/1)"
                ),
            })
    return issues


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


_BONE_FCURVE_RE = re.compile(r'pose\.bones\["([^"]+)"\]\.(\w+)')


def _root_bone_names():
    # A bone with no parent, in whichever armature(s) this file has -- bone names are assumed
    # unique within a file, which glTF/FBX rigs exported from any DCC tool already require.
    roots = set()
    for obj in bpy.data.objects:
        if obj.type == "ARMATURE":
            for bone in obj.data.bones:
                if bone.parent is None:
                    roots.add(bone.name)
    return roots


def _animations(scene):
    fps = scene.render.fps / scene.render.fps_base if scene.render.fps_base else scene.render.fps
    root_bones = _root_bone_names()
    out = []
    for action in bpy.data.actions:
        start, end = action.frame_range
        bone_names = set()
        root_motion = False
        for fc in action.fcurves:
            m = _BONE_FCURVE_RE.match(fc.data_path)
            if not m:
                continue
            bone_name, prop = m.group(1), m.group(2)
            bone_names.add(bone_name)
            # Root motion == the root bone's own *location* channel actually moves (not just
            # keyframed at a constant value) -- rotation/scale on the root, or motion on any
            # non-root bone, isn't root motion, it's an in-place pose.
            if prop == "location" and bone_name in root_bones and not root_motion:
                values = [kp.co[1] for kp in fc.keyframe_points]
                if values and (max(values) - min(values)) > 1e-5:
                    root_motion = True
        out.append({
            "name": action.name,
            "frame_start": start,
            "frame_end": end,
            "fps": fps,
            "duration_seconds": (end - start) / fps if fps else None,
            "bone_count": len(bone_names),
            "root_motion": root_motion,
        })
    return out


def _bone_hierarchy(armature_data):
    # parent name (or None for a root) per bone, not a nested tree -- flat is easier for an
    # agent to query ("what's b_Hip_01's parent?") and just as complete; depth/children are
    # trivially derived from this by whoever needs them.
    return [{"name": b.name, "parent": b.parent.name if b.parent else None} for b in armature_data.bones]


def _lod_suggestions(total_triangles):
    """A conservative default LOD ladder (50%/25%/10% of the current total triangle count --
    close to Unity/Unreal/Godot's own built-in LOD-group defaults) with each level's *estimated*
    resulting triangle count and the optimize.py command to produce it. `target_triangles` is a
    goal to aim the ratio at, not a guarantee: Decimate's own ratio is itself approximate, and
    (see references/pitfalls.md, issue #111) the actual output can differ further from what
    optimize.py's own report claims for a given run -- always re-run info.py on the real output
    to confirm what a decimate actually produced, never trust the target number alone.

    Computed from the *file's total* triangle count, not per-object: optimize.py's
    --decimate-ratio is a single ratio applied uniformly across every mesh object in one run
    (see optimize.py's run()), so a per-object ratio wouldn't match what the suggested command
    actually does. Not tied to any particular delivery target's budget -- check.py already does
    target-specific PASS/WARN/FAIL; this is just the standard starting point most engines ship,
    useful even with no target in mind yet.
    """
    if total_triangles <= 0:
        return []
    out = []
    for level, ratio in (("LOD1", 0.5), ("LOD2", 0.25), ("LOD3", 0.1)):
        target = round(total_triangles * ratio)
        if target < 1:
            continue
        out.append({
            "level": level,
            "ratio": ratio,
            "target_triangles": target,
            "command": f"optimize.py <file> -o <out> --decimate-ratio {ratio}",
        })
    return out


def _mesh_signature(obj):
    """A rotation/translation-invariant, order-independent fingerprint of a mesh's local
    geometry (vertex count, triangle count, surface area, volume, sorted local bounding-box
    dimensions) -- strong enough that two genuinely different meshes matching all five by
    coincidence is vanishingly unlikely, without needing an exact vertex-by-vertex identity
    check. Used to find objects on *different* Mesh datablocks that are nevertheless
    geometrically identical, i.e. duplicated instead of shared. Bounding-box dimensions come
    from the mesh's own local-space vertices, not `obj.dimensions` (world-space, includes each
    object's own scale) -- two instances of the same shape used at different scales should still
    match.
    """
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    vert_count = len(bm.verts)
    tri_count = len(bm.faces)
    area = round(sum(f.calc_area() for f in bm.faces), 4)
    volume = round(bm.calc_volume(signed=False), 4) if bm.faces else 0.0
    bm.free()
    xs = [v.co.x for v in obj.data.vertices]
    ys = [v.co.y for v in obj.data.vertices]
    zs = [v.co.z for v in obj.data.vertices]
    dims = ((max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)) if xs else (0.0, 0.0, 0.0))
    return (vert_count, tri_count, area, volume, tuple(sorted(round(d, 4) for d in dims)))


def _instancing_report(mesh_objs):
    """Two things worth telling an agent about mesh reuse:

    - already_instanced: objects that already share one Mesh datablock -- informational, this is
      the efficient/correct state, not a defect.
    - duplicate_mesh_candidates: objects on *separate* datablocks whose geometry matches by
      `_mesh_signature` -- these could be converted to share one datablock (reassigning
      `obj.data`), saving memory and enabling GPU instancing. Not fixed automatically: merging
      datablocks can affect anything that depends on per-datablock identity (vertex colors used
      differently per "instance", per-mesh custom properties), so this is a suggestion, not an
      automatic operation.
    """
    by_data = {}
    for o in mesh_objs:
        by_data.setdefault(o.data, []).append(o.name)
    already_instanced = [{"objects": names} for names in by_data.values() if len(names) > 1]

    by_signature = {}
    for data, names in by_data.items():
        obj = next(o for o in mesh_objs if o.data is data)
        by_signature.setdefault(_mesh_signature(obj), []).append(names[0])
    duplicate_mesh_candidates = [
        {"objects": names, "vertices": sig[0], "triangles": sig[1]}
        for sig, names in by_signature.items() if len(names) > 1
    ]

    return {
        "already_instanced": already_instanced,
        "duplicate_mesh_candidates": duplicate_mesh_candidates,
    }


def _armatures():
    out = []
    for obj in bpy.data.objects:
        if obj.type == "ARMATURE":
            out.append({
                "name": obj.name,
                "bone_count": len(obj.data.bones),
                "bones": _bone_hierarchy(obj.data),
            })
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
    transparency_issues = _transparency_issues(materials)
    for issue in transparency_issues:
        warnings.append(f"material '{issue['material']}': {issue['issue']}")
    colorspace_issues = _colorspace_issues(materials)
    for issue in colorspace_issues:
        warnings.append(
            f"material '{issue['material']}': '{issue['image']}' feeding {issue['socket']} is "
            f"tagged '{issue['colorspace']}', expected '{issue['expected']}' -- try: "
            "optimize.py <file> -o <out> --fix-colorspace"
        )
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
        if not stats["has_uv"] and not stats["is_point_cloud"]:
            warnings.append(f"{stats['name']}: no UV map")
        if stats["empty_material_slots"]:
            warnings.append(f"{stats['name']}: {stats['empty_material_slots']} empty material slot(s)")
        if any(abs(s - 1.0) > 1e-4 for s in stats["scale"]):
            warnings.append(
                f"{stats['name']}: unapplied scale {tuple(round(s, 4) for s in stats['scale'])} "
                "(a common sign of a cm/m unit mismatch) -- try: optimize.py <file> -o <out> --fix-scale"
            )
        if stats["unweighted_vertices"]:
            warnings.append(
                f"{stats['name']}: {stats['unweighted_vertices']} vertex/vertices with no bone "
                "weight (won't move with the armature) -- needs manual weight painting, no "
                "automatic fix"
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
    instancing = _instancing_report(mesh_objs)
    for group in instancing["duplicate_mesh_candidates"]:
        warnings.append(
            f"{', '.join(group['objects'])}: identical geometry on separate mesh datablocks "
            f"({group['vertices']} vertices, {group['triangles']} triangles each) -- could share "
            "one datablock to save memory and enable GPU instancing, not done automatically"
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
            "lod_suggestions": _lod_suggestions(total_tris),
            "draw_calls_estimate": sum(stats["draw_calls_estimate"] for stats in per_object),
        },
        "instancing": instancing,
        "materials": {
            "count": len(materials),
            "names": [m.name for m in materials],
            "transparency_issues": transparency_issues,
            "colorspace_issues": colorspace_issues,
        },
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
