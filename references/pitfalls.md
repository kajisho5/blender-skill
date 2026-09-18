# Gotchas

Every entry here was hit for real while building and testing this skill against a real Blender
(4.2.23 / 4.5.13 / 5.2.1) -- not a theoretical list.

## GPU offscreen rendering is unavailable under `-b`

`bpy.ops.uv.export_layout(mode='PNG')` raises `SystemError: GPU functions for drawing are not
available in background mode` -- it calls `gpu.types.GPUOffScreen`, which needs a real (even
software) GL context that plain `-b` doesn't set up the same way a final render does.
`mode='SVG'` works headlessly (pure vector math, no GPU) -- that's why `look.py --uv` always
writes SVG, never PNG.

A real render (`bpy.ops.render.render`) is a different code path and does work headlessly on
Linux once Mesa's software GL/EGL is installed (`libegl1 libgl1 libgles2 libegl-mesa0`; no real
GPU needed). macOS/Windows headless rendering was not verified by this project -- see ci.yml's
`render` job, which only runs on `ubuntu-latest` for exactly this reason.

## The Eevee engine identifier was renamed between Blender versions

Blender 4.2-4.5 shipped the new Eevee as `BLENDER_EEVEE_NEXT` (`scene.render.engine`). Blender
5.0 renamed it back to `BLENDER_EEVEE` once there was only one Eevee again. Setting the wrong one
raises `bpy_struct: item.attr = val: enum "..." not found in (...)`. Use
`scripts/bpy/_compat.py`'s `eevee_engine_id()`, which checks
`bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items` at runtime instead of
hardcoding either string.

## A watertight mesh is NOT what `bmesh` calls manifold, if you check it before welding

glTF/FBX/OBJ all split a vertex per unique normal/UV at a hard edge or UV seam, so a perfectly
closed cube imports with 24 unshared vertices (6 faces x 4 verts) instead of 8. Checking
`edge.is_manifold` on that raw topology says every edge is a boundary -- true of almost every
real, correctly-modeled asset, not a defect. `info.py` welds a *scratch copy* (`bmesh.ops.
remove_doubles`, not the real mesh) before counting non-manifold edges, so the check means "there
is an actual hole/gap", not "this mesh has hard edges". The raw duplicate-vertex count from that
same weld is reported as data, never turned into a warning, for the same reason.

## An image's `has_data` is False until you touch `.pixels`, even for a packed/embedded texture

`bpy.data.images` entries from a glTF/FBX import report `has_data = False` and `size = (0, 0)`
lazily -- `img.size` alone *does* report the real dimensions without forcing a load, but reading
`img.pixels` is what actually decodes the packed data, and only then does `has_data` flip to
`True`. `look.py --textures` and `bake.py` both filter on `has_data` *after* touching `.pixels`
once; filtering first (the "obvious" way to write it) silently finds nothing.

## Round-tripping a format is not guaranteed byte-identical, even to itself

- STL has no index buffer: every triangle stores 3 independent vertices. A `glb -> stl -> glb`
  roundtrip changes the reported vertex count (a cube: 24 -> 36) -- not a bug, an inherent
  property of the format. `convert.py --verify` will report this as a diff; that's it working,
  not failing.
- Re-exporting a glTF you just imported can change the vertex count by a small amount (a real
  helmet asset: 14556 -> 14588, about 0.2%) from the exporter's own vertex-splitting decisions
  differing slightly from the importer's. Always confirm fidelity for your specific asset with
  `--verify` rather than assuming a same-format roundtrip is lossless.
- OBJ/STL/PLY have no concept of an empty/null object or parent hierarchy -- an asset with a
  helper Empty (common in glTF exports) loses that object on export to any of the three; only
  its mesh data survives.

## USD/USDZ export drops animation and hidden objects by default

`bpy.ops.wm.usd_export`'s `export_animation` defaults to `False` (glTF and FBX both default to
exporting animation) and `visible_objects_only` defaults to `True` (a hidden helper object is
silently excluded). `scripts/bpy/_compat.py`'s per-format export defaults set
`export_animation=True` for usd/usdz; the visibility default is left as Blender's own, since
overriding it has its own tradeoffs (e.g. exporting hidden helper/collision geometry the user
may not want) -- pass `--verify` on `convert.py` to see whether it mattered for your file.

## Baking needs a UV map; the error if there isn't one is not obvious

`bpy.ops.object.bake()` on a mesh with no UV layer at all raises `Error: No active UV layer found
in the object "<name>"` -- easy to hit on procedurally-generated or CAD-exported meshes. `bake.py`
checks for this up front and raises a clearer message pointing at `look.py --uv` / `check.py`'s
"UV maps" row instead of surfacing Blender's own error text.

## `bl_rna` enum_items can list a value this Blender build cannot actually assign

`scene.render.image_settings.file_format = 'FFMPEG'` raised `enum "FFMPEG" not found in (...)`
in CI on a Blender 5.2.1 that had rendered a PNG thumbnail successfully moments before. The
first fix attempt checked `bpy.types.ImageFormatSettings.bl_rna.properties["file_format"]
.enum_items` ahead of time and trusted its answer -- wrong: that static, structural enum
definition lists every value the *property type* can ever hold, not what a specific build with
or without ffmpeg support compiled in can actually assign right now, so it said "yes" even on
the build that then failed. The reliable check is the assignment itself: `render.py --turntable`
now wraps `scene.render.image_settings.file_format = "FFMPEG"` in `try/except TypeError` and
falls back to a PNG sequence (`<name>_####.png`, with a warning in the result) only if that
actual assignment raises. General lesson: for an enum whose valid values can depend on runtime
build configuration, `bl_rna`'s `enum_items` is not authoritative -- try the assignment.

## gltf-transform's Meshopt compression produces a file Blender itself cannot re-import

`gltf-transform meshopt in.glb out.glb` succeeds and produces a valid, smaller glb (three.js/
Babylon.js/other EXT_meshopt_compression-aware runtimes read it fine), but re-importing it into
Blender fails: `Error: Extension EXT_meshopt_compression is not available on this addon version`.
So `optimize.py --meshopt`'s output cannot be round-tripped through `convert.py --verify` or
re-opened by any other script in this skill -- that's an inherent limitation of Blender's glTF
importer, not a bug in the meshopt step itself. Draco-compressed output does not have this
problem (Blender's importer supports `KHR_draco_mesh_compression` natively).

## Draco compression changes the reported vertex count

`gltf-transform draco` re-indexes geometry as part of encoding; a cube-like mesh that reads as
1770 vertices before compression can read as 476 after -- real data, not data loss (triangle
count and visual result are unaffected). Compare triangle counts, not vertex counts, when judging
whether a Draco pass changed anything meaningful.

## gltf-transform's `uastc`/`etc1s` (KTX2/Basis) commands need a separate `ktx` CLI

`gltf-transform` itself doesn't bundle a KTX2 encoder -- its `uastc`/`etc1s` commands shell out to
a `ktx` binary from KTX-Software (the current unified CLI; older docs may reference the same
project's now-superseded standalone `toktx`). Having `gltf-transform` on PATH is not enough to
assume KTX2 support is available; check for `ktx` separately (`_delegate.ktx_available()`).

## Self-intersection detection must compare face adjacency by position, not vertex index

`mathutils.bvhtree.BVHTree.overlap(tree, tree)` finds every pair of triangles whose bounding
boxes touch, which includes every ordinary adjacent-face pair sharing an edge -- the obvious
filter is "skip pairs that share a vertex", but on a real glTF/FBX import a vertex is split per
unique normal/UV at every hard edge (same root cause as the non-manifold note above), so two
genuinely-adjacent triangles almost never share a vertex *index* even though they share a
position. Filtering by index alone produced 4 false "self-intersections" on
`tests/fixtures/box.glb` (a plain watertight cube) and 115 on `tests/fixtures/fox.glb` -- filtering
by rounded vertex *position* instead correctly reads 0 for both while still catching a real case
(two overlapping cubes joined into one object: 8 genuinely-intersecting triangle pairs, agreed on
by both filtering methods). `info.py`'s `self_intersecting_faces_approx` uses position filtering;
treat the count as "worth a manual look" regardless -- it's still bounding-box overlap, not exact
triangle-triangle intersection.

## Recalculating normals can corrupt an already-correct non-manifold mesh

`bmesh.ops.recalc_face_normals()` (what `optimize.py --recalc-normals` uses) and the edit-mode
`bpy.ops.mesh.normals_make_consistent(inside=False)` (Blender's own "Recalculate Normals" menu
command) both rely on a flood-fill across shared manifold edges to propagate a consistent
"outside" direction. On a fully closed, single-shell mesh this is reliable (confirmed: a cube with
every normal manually inverted is fixed 6/6, an unmodified cube is untouched, and a sphere with a
chunk of faces removed -- non-manifold, but still one shell -- is correctly left alone, 0/118
false positives). It is NOT reliable once separate parts of the mesh are joined only by
non-manifold junctions rather than shared manifold edges, which is common in real rigged
character meshes: confirmed on `tests/fixtures/fox.glb` (1150 non-manifold edges) -- both
recalculation methods flip roughly half the mesh's faces (299 of 576), and actually applying the
"fix" and re-rendering turns a cleanly, correctly-shaded fox into a black/white patchwork (i.e.
the *original* normals were already correct; recalculation broke them). General lesson: only
trust a normal-recalculation *comparison* (info.py's `flipped_normal_faces`) -- and only apply
`--recalc-normals` as an actual fix -- when the mesh has zero non-manifold edges; `info.py`
reports `flipped_normal_faces: null` and `optimize.py --recalc-normals` prints an explicit warning
instead of guessing when that's not the case. Fill real holes first (`--fill-holes`) and re-check.

## Filling holes needs the same weld-before-detect fix as non-manifold detection

`bmesh.ops.holes_fill()` finds boundary loops via `edge.is_boundary`, which has the identical
split-vertex problem as the non-manifold check above: on a raw glTF import, a single missing quad
face reads as *16* boundary edges (one per unrelated triangle corner) instead of the real 4-edge
loop, because no two of those edges' vertices are the same BMVert object -- `holes_fill` finds no
closed loop and silently fills nothing (confirmed: 0 faces added, hole still open). Welding
coincident-position vertices first (same `remove_doubles(dist=1e-6)` info.py's diagnostic already
uses) collapses it to the true 8-vertex/4-edge topology and `holes_fill` then works correctly (1
face added, mesh fully closed). This does not flatten the mesh's hard-edge shading elsewhere:
bmesh keeps UV/custom-normal data per face-corner (loop), not per vertex, so a re-export still
re-splits vertices the same way the original file was authored. `optimize.py --fill-holes` always
welds before scanning for holes, whether or not one is actually present.

## "Units not set" can't actually happen in this skill's own pipeline

RM-002 asked for detecting both an unapplied cm/m scale *and* a scene with no unit system set
(`scene.unit_settings.system == 'NONE'`). The first is real and common (see the next entry) --
the second is not reachable here: `_compat.reset_scene()` calls
`bpy.ops.wm.read_factory_settings(use_empty=True)` before every import, and confirmed on a real
Blender, factory-startup's default unit system is already `'METRIC'`, not `'NONE'` -- and none of
this skill's importers (glTF forces `METRIC` itself; OBJ/STL/PLY don't touch unit settings at all)
ever change it away from that. So `info.py` only implements the scale-mismatch half; a
"units not set" check would be dead code that can never fire given how this skill resets state
before every operation.

## An unapplied object scale is a real, glTF-roundtrip-faithful defect signal

An object with `scale` left at e.g. `(0.01, 0.01, 0.01)` instead of baked into the mesh (the
classic symptom of importing a cm-authored asset into an m-scene, or vice versa, without applying
the resulting scale) round-trips through glTF export/import exactly as-is -- confirmed: Blender's
glTF exporter writes it as a node-level scale, not baked-in vertex positions, so `info.py` can
reliably read it back off `obj.scale` without needing to infer anything from geometry.
`bpy.ops.object.transform_apply(scale=True)` bakes it into the mesh data while leaving
world-space dimensions/position unchanged (confirmed: an object with scale `(0.01, 0.01, 0.01)`
and world dimensions `(2, 2, 2)` keeps those same world dimensions and ends up with scale
`(1, 1, 1)` afterward) -- that's `optimize.py --fix-scale`.

## An off-center object origin is not itself a defect

Unlike non-manifold/self-intersecting/flipped-normal, `info.py`'s `origin_offset_from_center` /
`origin_offset_from_bottom_center` (how far the object's origin sits from its own local
bounding-box center/bottom-center) is reported as plain data, never a warning: many real assets
deliberately put their origin somewhere other than the geometric center for correct behavior in a
game engine or rig (a door's origin at its hinge edge, a wheel's at its axle, a character's at its
feet) -- flagging every one of those as "wrong" would be actively incorrect advice.
`optimize.py --origin center|bottom` moves the origin without moving the geometry in world space
(confirmed: the object's world-space bounding box is bit-identical before and after) -- there when
an agent or user has decided that specific convention is what they want, never applied on info.py's
own initiative.

## UV overlap detection needs exact 2D triangle overlap, not just bounding-box overlap

The same bounding-box-overlap shortcut that works acceptably for the 3D self-intersection check
(see above) is far too imprecise for UV space: a real, efficiently-packed UV layout has many
faces whose UV *bounding boxes* touch or overlap without the *triangles* actually overlapping.
Confirmed: a naive bbox-only count reported 273 of 576 false "overlapping" faces on
`tests/fixtures/fox.glb` (a clean, real character UV map) and 33/80 on its eye mesh -- close to
half the mesh, useless as a signal. Exact 2D triangle-triangle overlap (edge-crossing tests plus
point-in-triangle containment) on the bbox-prefiltered candidates reads the correct 0/0.
Also: the "skip pairs that share a UV vertex, they're just adjacent" filter must only exclude a
shared *edge* (1-2 shared vertices) -- excluding on *any* shared vertex incorrectly waves through
a fully duplicated/stacked UV island (all 3 vertices coincide, the most severe overlap there is)
as "adjacent" and misses it completely; confirmed both ways on a synthetic stacked-UV fixture (0
detected with the bug, the correct 12 once fixed).

## Custom mesh attributes are not just the ones on `Mesh.attributes` that look custom

`Mesh.attributes` includes plenty of entries Blender itself creates that a user never added --
and their `is_internal` property, which correctly flags the dot-prefixed bookkeeping ones
(`.corner_vert`, `.select_edge`, ...) as internal, is `False` for several more of Blender's own
built-ins: `position` (every mesh has this), `sharp_face`, `UVMap` (one per UV layer, already
reported via `uv_maps`), and, confirmed by actually adding them via ordinary edit-mode operators,
`material_index` (second material slot used), `bevel_weight_edge`/`crease_edge` (edge bevel
weight/crease set on any edge). `info.py`'s `custom_attributes` excludes `is_internal` attributes
*and* a known-name set of these non-internal built-ins, confirmed against a real cube with two
materials, a bevel weight, a crease, one vertex color layer and two real custom attributes: only
the two real ones are reported, matching zero for `tests/fixtures/box.glb`/`fox.glb` (neither has
any).

## glTF round-trips vertex colors but drops generic custom mesh attributes entirely

Exporting a cube with a vertex color layer *and* two arbitrary custom attributes (a per-vertex
float, a per-face int) to glb and re-importing keeps the vertex color (renamed `Col` -> `Color`,
following glTF's own `COLOR_0` convention) but the two custom attributes are simply gone --
glTF has no generic concept of an arbitrary named custom mesh attribute without a vendor
extension, and Blender's default exporter doesn't add one. `tests/fixtures/vertex_colors_cube.blend`
(not a `.glb`) is the fixture for `custom_attributes`, specifically because the format needed to
be one that actually preserves them.

## Root motion means the root bone's own location channel moves, not "any bone is animated"

`bpy.data.actions[].fcurves[].data_path` for a pose-bone channel matches
`pose.bones["<name>"].<prop>`; the bone's *name* alone doesn't say whether it's a root -- that
needs cross-referencing against the armature's own `bone.parent is None`. And a bone being
animated at all is not root motion: fox.glb's 3 actions each animate 20 of 24 bones (confirmed
real numbers) and correctly read `root_motion: False` for all three, because none of them ever
keyframes the root bone's own `location` -- they're in-place cycles meant to be driven externally
by a character controller. Root motion specifically means the root bone's `location` fcurve
values actually vary (`max - min > 1e-5` across its keyframes, not just "has keyframes" -- a
channel keyframed at a constant value is not motion). Verified against a synthetic 2-bone rig
(`tests/fixtures/root_motion_rig.blend`) with one action keying the root's location (reads
`root_motion: True`) and one keying only the child bone's rotation (reads `root_motion: False`
despite being just as "animated").

Building that fixture surfaced a separate, unrelated gotcha: an action assigned to
`armature.animation_data.action = None` after creation has zero users and no fake user, so
`bpy.data.actions` silently drops it by the time the file is saved and reloaded -- it must have
`action.use_fake_user = True` set *before* unlinking it, or a test fixture meant to hold multiple
actions on one armature will silently end up with only the most recently active one.

## An "unweighted vertex" check needs gating on having an armature modifier at all

`vertex.groups` sums to 0 for *any* vertex with no bone weight -- but a mesh that was never
meant to be skinned (a static prop, an eye mesh moved by its own object transform rather than
vertex weights) has *zero* vertex groups and no Armature modifier, so every one of its vertices
reads "unweighted" too. Confirmed on fox.glb: the rigged "fox" mesh correctly reads 0/1728
unweighted; its unrigged "Icosphere" eye mesh has 42/42 vertices with no group at all -- gating
the check on `any(m.type == "ARMATURE" for m in obj.modifiers)` and reporting `None` (not 0, not
every vertex) for a non-skinned mesh is what makes this a real defect signal instead of noise on
every asset that mixes rigged and static parts.

Building the positive-case test fixture surfaced a separate gotcha:
`bpy.ops.object.parent_set(type='ARMATURE_AUTO')` bone-heat-weights *every* vertex of the mesh
into the new vertex group as part of parenting -- so `vertex_group.add([target_indices], 1.0,
'REPLACE')` on a *subset* of vertices does not leave the others unweighted, it just re-adds them
at the same weight they already had. Getting a genuinely unweighted vertex requires explicitly
`vertex_group.remove([index])` after auto-parenting, not merely omitting that index from a
weight-assignment call.

## `optimize.py --decimate-ratio`'s own triangle count can disagree with the real output file

Discovered while sanity-checking RM-010's LOD ratio suggestions against a real decimate run:
`optimize.py fox.glb -o out.glb --decimate-ratio 0.5` reported `triangles_after: 328` (576->288
for the `fox` mesh, 80->40 for the `Icosphere` eye mesh -- both correct, measured on `obj.data`
right before export) but re-running `info.py` on the actual `out.glb` shows 368: `fox` decimated
correctly, `Icosphere` silently reverted to its original 80. Confirmed in isolation (decimating
only `Icosphere`, `fox` untouched) that this is real, not a fixture artifact: the in-memory mesh
genuinely is 40 polys immediately before export (both `obj.data` and a fresh
`evaluated_depsgraph_get()` agree), an explicit `view_layer.update()` before export doesn't fix
it, and `export_apply=True` makes it worse (both objects revert). A plain no-op export/reimport
with no modification at all round-trips perfectly, so this is specific to the decimate+apply
step for at least this object, not a general glTF round-trip issue -- and counter-intuitively,
`fox` (whose added Decimate modifier isn't first in the stack, behind the pre-existing Armature
modifier, triggering Blender's own "Applied modifier was not first" warning) is the one that
exports *correctly*; the "clean" object with no other modifiers is the one that silently loses
its decimation. Root cause not yet isolated -- tracked as
[#111](https://github.com/kajisho5/blender-skill/issues/111), not blocking `info.py`'s LOD
suggestions (RM-010), which only compute ratios from the current triangle count and never call
`optimize.py` or trust its output.

## `polygon.material_index` reads 0 even on a mesh with zero material slots

Confirmed on fox.glb's "Icosphere" eye mesh (0 material slots): every one of its faces still
reads `material_index == 0`, the same index a *real* first material slot would use. Naively
treating "no material slots" as "not rendered" would be wrong -- Blender (and every real-time
engine) still draws it, just with a default/fallback material, so it's still one real draw call.
`_draw_call_estimate` relies on this: `len({p.material_index for p in obj.data.polygons})` reads
1 for this mesh, correctly, with no special-casing needed for the zero-material-slots case.

## Blender 4.2's EEVEE Next dropped OPAQUE/CLIP from the real transparency-mode property

`Material.blend_method`'s own `bl_rna` enum still lists all 4 legacy values (`OPAQUE`, `CLIP`,
`HASHED`, `BLEND`) on real 4.2.23/4.5.13/5.2.1 -- but assigning `'OPAQUE'` or `'CLIP'` to it
silently no-ops (the property keeps whatever value it already had); only `'HASHED'` and
`'BLEND'` actually take. The real, current, authoritative property is `Material.
surface_render_method`, a 2-value enum (`DITHERED`, `BLENDED`) that EEVEE Next introduced to
replace the old 4-mode system -- `blend_method` reads back as a compatibility view onto it
(`DITHERED` -> `HASHED`, `BLENDED` -> `BLEND`) but is not itself settable to the two modes it no
longer has. Confirmed identical on all three CI-tested Blender versions. This directly affects
RM-013 (originally scoped as "alpha blend vs clip"): that literal CLIP mode doesn't exist as a
settable state on any currently-supported Blender version, so `info.py`'s transparency-mode
check reads `surface_render_method` directly rather than `blend_method`, and is framed as
"real blending (BLENDED) vs. dithered (DITHERED)" -- the same underlying artist mistake
(a binary alpha mask needlessly costing real, sorting-order-fragile alpha blending), just
tracked through the property that's actually real today. Same "bl_rna enum isn't authoritative
for real behavior" lesson this project has hit before (the FFMPEG format enum, `Attribute.
is_internal`), not a new kind of surprise.

## An image's `.pixels` needs the lazy-load trigger before checking transparency, too

`_alpha_is_binary_mask` (RM-013) hit the same lazy-load gotcha this file already documents for
`look.py`/`bake.py`: on a freshly-loaded `.blend`, a packed/embedded image's `has_data` reads
`False` and `size` reads `(0, 0)` until `.pixels` is actually touched once. Checking `has_data`
*before* reading `.pixels` silently skipped every material's alpha texture (`transparency_
issues` read `[]` even on `misconfigured_transparency.blend`'s deliberately-broken fixture) --
fixed by reading `image.pixels[:]` first (forcing the decode) and only then checking `has_data`/
`size`.

## Blender's glTF importer doesn't expose which KHR_*/EXT_* extensions a file declared

`extensionsUsed`/`extensionsRequired` are top-level fields in a glTF file's own JSON -- Blender's
importer reads and *applies* them (Draco-compressed meshes decode transparently, `KHR_materials_
unlit` becomes a shadeless material node setup, etc.) but there is no `bpy` property anywhere
that lists which extensions the source file actually declared; by the time the scene is built,
that information is gone. RM-014's `gltf_extensions` is read directly from the file's own JSON
instead -- for `.glb`, by parsing the 12-byte binary header and the first ('JSON'-type) chunk by
hand (`struct`, stdlib only); for `.gltf`, just `json.loads()` the file -- entirely independent
of Blender, in the host script (`scripts/_gltf_extensions.py`), not `scripts/bpy/info.py`.
Verified against a real Draco-compressed export (`optimize.py --draco` on `box.glb`, confirmed
`extensionsUsed == extensionsRequired == ['KHR_draco_mesh_compression']`) and against fox.glb/
box.glb (both `[]`, no false positives).

## A USDZ's own zip container has real, enforced structural rules -- and Blender enforces them too

Apple's USDZ spec requires every entry in the package to be stored uncompressed (never Deflate)
and to start at a 64-byte-aligned file offset (so a viewer can mmap the archive directly).
Confirmed this isn't just an Apple-only nicety: Blender's own USD importer refuses to even open a
usdz re-zipped with ordinary Deflate compression ("Error: Could not open USD archive for
reading"), on a file whose USD content is otherwise byte-identical. Confirmed the *positive*
side too: real `convert.py` USDZ output (both a plain cube and a textured, multi-entry export of
fox.glb) is already fully compliant -- every entry stored, every entry's data starting at a
64-byte-aligned offset -- Blender's own exporter gets this right without any extra work from
this skill. `check.py`'s new USDZ-package row (RM-016) reads this directly from the file's own
zip structure (`zipfile`/`struct`, stdlib only, no Blender) rather than assuming compliance;
because a broken package fails to even *import* into Blender, the negative case can only be
exercised by testing `scripts/_usdz_validate.py` directly, not through `check.py`'s CLI
end-to-end (info.py, which check.py always runs first, fails before this row would run).

## Blender's OBJ importer's Ns->roughness conversion is a fixed, testable formula

OBJ/MTL predates physically-based rendering entirely -- it only has the old Phong/Blinn-Phong
ambient/diffuse/specular/shininess model (`Ka`/`Kd`/`Ks`/`Ns`), no metallic or roughness channel.
Confirmed on real Blender 4.2.23 (`wm.obj_import`) across `Ns` = 0, 5, 90, 200, 1000: the
resulting Principled BSDF's Roughness input reads exactly `1 - sqrt(clamp(Ns, 0, 1000) / 1000)`
every time (Ns=0 -> roughness 1.0, Ns=1000 -> roughness 0.0, Ns=200 -> 0.5527864..., matching to
float32 precision). Metallic is always left at 0.0 -- Blender's own importer never attempts to
guess it from Phong parameters, and RM-018's own `scripts/_obj_mtl.py` deliberately doesn't
either: there's no reliable signal in `Ka`/`Kd`/`Ks`/`Ns` alone that separates a metal from a
very shiny dielectric, so guessing would trade a known, honestly-reported gap for an unverifiable
claim. Confirmed end-to-end too: converting a `.obj`/`.mtl` fixture through `convert.py` to
`.glb` and reading back the exported `pbrMetallicRoughness.roughnessFactor` matches this same
formula exactly -- the heuristic `info.py` reports isn't just a formula in isolation, it's what
this skill's own pipeline actually produces.

## Blender's "3D-Print Toolbox" is no longer bundled as of 4.2

Confirmed via `addon_utils.modules()` on real 4.2.23 with `--factory-startup`: only 14 add-ons
are discoverable at all (fbx/gltf/svg/bvh importers, Cycles, Rigify, Node Wrangler, ...) --
`object_print3d_utils` ("3D-Print Toolbox"), the add-on that historically shipped the exact
"per-face thickness via ray-casting" analysis RM-019 needed, isn't among them. Blender 4.2 moved
many formerly-bundled add-ons to the separately-installable Extensions platform
(extensions.blender.org), which this skill's no-cloud/no-extra-install constraint rules out
depending on. Implemented the same ray-cast-thickness technique directly instead (`info.py`'s
`wall_thickness_min`): cast a ray from each face's center along its own negative normal, take the
hit distance to the nearest opposing surface. Verified against a real boolean-differenced
hollow cube with a deliberate 0.3-unit wall (reads ~0.2999, matching to within the ray's small
origin-offset epsilon) and a solid cube (reads ~9.9999 for a size-10 cube -- the full
face-to-opposite-face distance, not a false "thin" reading).

## WebP is not always smaller than the PNG it replaces

`optimize.py --webp` (RM-024) converts textures via Blender's own glTF exporter
(`export_image_format='WEBP'`, which adds `EXT_texture_webp`) -- a real, native capability, no
external tool involved. But confirmed on a real asset, in a fair controlled A/B (same import,
same export call, only `export_image_format` differing): fox.glb's one PNG texture is 26764
bytes; the same texture re-encoded as WebP at Blender's own default quality (75) is **30060
bytes** -- larger, not smaller. This isn't a fluke of an unfair comparison (an earlier, naive
before/after used the committed `fox.glb` as "before" and a fresh full re-export as "after",
which conflated the format change with ~39KB of unrelated JSON-chunk differences between however
the original fixture was produced and this Blender version's own export defaults -- redone as a
true A/B once that discrepancy surfaced). Lowering `--webp-quality` (e.g. to 30) does shrink it
below the original (19530 bytes) -- so the tool works correctly, it just isn't a guaranteed win
at default settings for every texture, especially a small/already-well-compressed one. `optimize.
py --webp` reports the real measured `texture_bytes_before`/`texture_bytes_after` (read directly
from each file's own glTF JSON, `scripts/_gltf_extensions.py`'s `total_image_bytes`) rather than
claiming a reduction happened.

## A selection-only export can silently skip an object even when it's visible and selected

`split.py` (RM-027) groups a multi-object file into independent root-object hierarchies and
exports each group via a `use_selection=True`-style call (`_compat.export_selected`). While
building this, an early test on fox.glb exported the "Icosphere" object (no parent, its own
hierarchy at the time) as an empty 132-byte file while "root"+"fox" exported correctly. Root
cause, found in stages -- object identity corrected below, but the underlying mechanism is real:

1. Blender's own glTF importer places any node not reachable from the file's default scene roots
   into an auto-created **"glTF_not_exported"** collection, with `Collection.hide_viewport =
   True` (confirmed: `Icosphere.users_collection == ["glTF_not_exported"]` after import).
2. An object inside a viewport-hidden collection can't be selected at all --
   `obj.select_set(True)` silently no-ops and `obj.select_get()` stays `False`. Unhiding the
   collection (`coll.hide_viewport = False`) fixes *this* symptom in isolation.
3. But fixing only the selection state wasn't enough: even after unhiding the collection and
   confirming `obj.visible_get() == True` and `obj.select_get() == True` right before the export
   call, `bpy.ops.export_scene.gltf(..., use_selection=True)` still logged "Finished glTF 2.0
   export" and still wrote an empty 132-byte file. Selection/visibility state was never the real
   blocker -- Blender's glTF exporter excludes an object by its **collection membership**
   (specifically, being in a collection literally named `glTF_not_exported`), independent of
   whether that object is currently selected or visible.

Confirmed fix: relink every object into the scene's root collection
(`bpy.context.scene.collection`) before selecting/exporting, unlinking it from any other
collection first -- not just toggling `hide_viewport`. `split.py` does this relink
unconditionally for every object right after import, so it also sidesteps any other
importer-created collection with the same kind of export-time exclusion, not just this one named
collection. This is a real, general Blender export behavior worth keeping the fix for even
though, as the next entry explains, this specific "Icosphere" object turned out not to be
genuine file content at all -- some other file's real orphaned node could still land in the same
collection and hit the same export-time exclusion.

## fox.glb's "Icosphere" isn't in the file -- it's a bone custom-shape widget Blender synthesizes

The entry above (and the original RM-027 PR) assumed "Icosphere" was an independent object
genuinely authored in fox.glb, unreachable from the file's default scene roots. That was wrong,
discovered while building anim.py (RM-028) and confirmed directly against the file's own data:

- fox.glb's raw JSON (`struct`-parsed .glb JSON chunk, not Blender's import of it) lists exactly
  one mesh, `"fox1"`, and no "Icosphere" node or mesh anywhere in the file.
- Importing **any** skinned armature -- fox.glb's own real armature, a bare
  `bpy.ops.object.armature_add()` with no mesh at all, and a separately-tested mesh+armature
  file with all skin joints actually in use -- always produces an extra small mesh object named
  "Icosphere" in `bpy.data.objects`, placed in "glTF_not_exported".
- That object is assigned as **every pose bone's `custom_shape`** on the armature it was created
  for (confirmed: iterating `armature.pose.bones`, all 24 of fox.glb's bones point their
  `custom_shape` at this one object). It is Blender's own bone-display convenience (spheres
  instead of the default stick shape in the viewport) synthesized fresh on every import, not
  file content -- FBX import of the same armature does not do this at all (confirmed on the same
  content re-exported as .fbx and reimported: no extra object), only glTF import does.

Consequence: `split.py` (before this was found) treated this synthetic widget as its own
independent hierarchy and exported it as a spurious, meaningless output file for *any* skinned
glTF character, not just fox.glb -- a real defect, not just a documentation error, now fixed by
detecting and discarding it (`_compat.strip_import_helper_objects`, matched by "referenced as
some armature's pose-bone `custom_shape`", the one property that's actually true of it) before
grouping objects into hierarchies. `anim.py --combine` (RM-028) has the same exposure -- the
*base* file's own import synthesizes this widget too, and would otherwise leak it into an
otherwise-clean combined character file as a spurious extra mesh -- fixed the same way,
immediately after importing the base.

Deliberately not "fixed" elsewhere: `info.py`'s own object/mesh counts for a skinned glTF file
(fox.glb included) still include this widget, because `info.py`'s stated job is to report what
Blender's own import actually produces -- which is genuinely what you'd see opening the file in
Blender for further work -- not to second-guess which of those objects trace back to the
original authored content. Only split.py and anim.py, whose whole point is reasoning about
"independent content to re-export," needed the strip.

## Blender's bundled Python includes numpy

Not stdlib in the usual sense, but it ships with Blender itself (confirmed: `numpy 1.24.3` in
4.2.23's bundled interpreter) -- `scripts/bpy/*.py` (running inside Blender, not the host
Python) uses it for pixel-array work (`look.py`'s texture grid/compare, `render.py`'s sheet
mode, `bake.py`'s pixel-stat verification). The stdlib-only guarantee is about the *host*
scripts and about not requiring `pip install` anything; Blender's own bundled capabilities are
part of "depends on Blender", not an extra dependency.

## USD's axis-orientation export needs an extra master switch, and rotates a root wrapper, not each object

`convert.py --up-axis`/`--forward-axis` (RM-029) translates a forward/up axis request into each
format's own export kwargs (`scripts/bpy/_compat.py`'s `axis_export_kwargs`). For USD/USDZ,
setting `export_global_up_selection`/`export_global_forward_selection` alone has **no effect** --
confirmed by exporting the same off-origin object with only those two set vs. also setting
`convert_orientation=True`: the first export's `upAxis` stage metadata still read the untouched
default and no rotation appeared anywhere in the file; only with `convert_orientation=True` did
`upAxis` change and a rotation appear. That rotation is applied as a single `rotateXYZ` on one
root-level `Xform` prim wrapping the whole scene, not by rotating each object's own
`xformOp:translate` -- confirmed by reading the exported `.usda`'s own text directly (not
Blender's reimport of it, which resolves the rotation transparently and would hide this). Also
confirmed: USD's importer (`wm.usd_import`) has no equivalent override at all -- it always reads
`upAxis` from the file's own stage metadata; axis control is export-only here.

## OBJ's own default axis convention isn't Blender's native axes

OBJ's native import/export operators (`wm.obj_import`/`wm.obj_export`) default to
`forward_axis=NEGATIVE_Z, up_axis=Y` (confirmed via their own `bl_rna` defaults) -- OBJ's
traditional Y-up/-Z-forward authoring convention, not Blender's own Z-up axes. This is not a
bug: import and export share the same default, so this skill's own OBJ round trips (and any
OBJ<->OBJ conversion) stay self-consistent without needing `--up-axis`/`--forward-axis` at all.
It only becomes visible if one side of a conversion requests an explicit, non-default axis
(e.g. testing with a "no conversion, trust Blender's own axes" OBJ export) while the other side
still uses the default -- discovered exactly that way while verifying RM-029's `--up-axis`
against a fresh OBJ fixture, before switching to a `.blend` source (which has no import-axis
step to introduce this kind of ambiguity) to get a clean, unconfounded result.

## Stock Blender has no ACES view transform, only ACES texture colorspaces

RM-030's `optimize.py --texture-colorspace`/`--fix-colorspace` was scoped down after checking
what "ACES" actually means in real Blender 4.2.23: its `scene.view_settings.view_transform` --
the tone-mapping/display transform applied when *rendering* -- only accepts `Standard`, `Khronos
PBR Neutral`, `AgX`, `Filmic`, `Filmic Log`, `False Color`, `Raw` (confirmed by trying to assign
`'ACES'` and reading Blender's own error, which lists every real option). There is no ACES view
transform at all in Blender's bundled/default OCIO config -- a full ACES *rendering* pipeline
needs a separate, non-bundled OCIO config this skill's no-extra-install constraint rules out.

Texture colorspace *tagging* is a different, separate thing (what raw pixel data in an Image
datablock means, for correct interpretation during shading -- not a render output transform),
and Blender's bundled config genuinely does include real ACES color spaces there: `image.
colorspace_settings.name` accepts `ACES2065-1` and `ACEScg` alongside `sRGB`/`Non-Color`/etc.
(confirmed via the property's own real enum). So `--texture-colorspace ACEScg` is a real, working
command -- it just tags textures for an ACES-aware shading setup, not a claim that this skill can
render through a full ACES view transform, which it honestly can't without an extra install.

## The synthesized bone-shape widget inflates triangle counts wherever it isn't stripped

`lod.py` (RM-031) hit a lighter version of the same defect `split.py` (RM-027) found: Blender's
own synthesized bone custom-shape widget (see the earlier "fox.glb's 'Icosphere'..." entry) got
counted as real triangles on any skinned-armature input, since `lod.py`'s import path didn't
call `_compat.strip_import_helper_objects()`. On fox.glb the reported "original" triangle count
was 656 (576 real + the widget's own 80) instead of 576, and every LOD level's Decimate modifier
ran against it too -- wasted work, since it's a tiny object either way. Checked directly (by
temporarily disabling the strip and reading each output's raw glTF JSON): the *exported files*
were not actually contaminated even without the strip -- Blender's own whole-scene export
already excludes anything in the "glTF_not_exported" collection the widget lives in, unlike the
*selection*-based export `split.py` uses, which is what made RM-027's version a real deliverable
bug rather than just a reporting one. `optimize.py`'s existing `--decimate-ratio` had this same
lighter version too (`triangles_before`/`triangles_after` for fox.glb inflated by the same 80,
output file unaffected) -- fixed in RM-033's PR once back in that file for other reasons, since
the fix is the same one-line strip and by then two separately-shipped scripts had the same gap.

## Screen-occupancy without a real camera: scene-relative bounding-box size as the proxy

`optimize.py --texture-auto-resolution` (RM-033) needed a "how much of the screen would this
texture's object occupy" signal with no actual camera, FOV, or viewport this skill has any way
to know (headless Blender here never renders through a specific shot). The proxy used: each
object's own world-space bounding-box diagonal, divided by the *whole file's combined* bounding
box diagonal across every mesh object -- i.e., how big this object is relative to everything
else in the same file, which is a real, data-driven number straight from the geometry, not a
guess. That fraction times an assumed `--viewport-width` (default 1920), rounded up to the next
power of two, becomes that texture's cap. Verified on a fixture with a 10-unit and a 1-unit cube
20 units apart, each with its own 512x512 texture: the small one's texture got downscaled to a
correspondingly small cap (128 at the default viewport width) while the big one's cap (2048)
came out above its actual size and was correctly left untouched. A file with exactly one mesh
object degenerates cleanly to fraction 1.0 (that object's own bounding box IS the whole scene's),
capping its textures at the full assumed viewport width -- the sensible default for a single
hero asset with no other objects to be smaller than.

## Pruning dead leaf bones needs a fixpoint loop, not one pass

`optimize.py --remove-unused-bones` (RM-034) only ever removes a bone with no children (a leaf)
and zero skin-weight influence and no fcurve animation -- a bone with a still-present child is
never touched, even if that bone itself carries no weight, since a structural ancestor might
still be needed to position a used descendant. That means a *chain* of two or more genuinely dead
bones (e.g. `Used -> Dead1 -> Dead2`, both unweighted and unanimated) can't be fully pruned in a
single pass: on pass one only `Dead2` is a leaf and qualifies; only after it's gone does `Dead1`
become a leaf itself. `_remove_unused_bones()` re-scans the influence map and re-collects the
leaf set after every removal (`while changed: ...`) rather than computing candidates once up
front, so a chain of any depth is fully cleared in one call. `sparse_rig.blend`'s own fixture
(`Head -> HeadTip`, `Arm1 -> Arm1Tip`, each dead bone one level deep and already a leaf on pass
one) doesn't exercise the multi-level case -- it just confirms the base leaf+zero-influence+
unanimated check. `--max-bones N`'s `_cap_bone_count()` needs the same per-removal re-scan of the
*structural* candidate set (which bones are currently childless), since capping to a hard count
means every subsequent victim has to be picked from whatever the tree currently looks like, not a
stale leaf list computed before earlier removals changed its shape -- but not of the *influence*
values themselves (see the next entry: those are cached and updated in place instead).

## Root-vs-leaf bugs found by review: a weighted root bone loses its weight silently, and rescanning vertex data per removed bone doesn't scale

A CodeRabbit review on RM-034's own PR caught three real bugs in the first version of
`--remove-unused-bones`/`--max-bones` that neither the unit tests nor manual testing had
exercised, because the sparse_rig.blend fixture happened not to hit any of these shapes:

- **A weighted root bone (no parent) chosen as a `--max-bones` victim silently lost its weight.**
  `_transfer_bone_weight_to_parent()` has always discarded a removed bone's weight when it has no
  parent to receive it (documented as intentional -- there's nothing to reattach to). But
  `_cap_bone_count()`'s candidate list only checked "is this bone a leaf and unanimated", not
  "does this bone have a parent to transfer to" -- so a rig with an isolated, weighted root bone
  (no parent, no children: a root *and* a leaf at once) could have that bone picked as the
  lowest-influence victim and its vertex-group weight dropped with no warning, reporting
  `reassigned_to: null` as if that were a normal outcome rather than data loss. Fixed by excluding
  a weighted (`influence > 1e-6`) parentless bone from the candidate list entirely, the same way an
  animated bone is excluded -- if that leaves no valid candidate, `_cap_bone_count` now stops and
  warns ("...animated bone or a weighted root bone...") instead of silently dropping weight.
  Verified two ways on a purpose-built `root_leaf_rig.blend` fixture (an isolated weighted root
  bone plus a separate animated-child chain, `--max-bones 1`): with the fix, all three bones
  survive and one warning is reported; reverting just the new exclusion check reproduces the old
  bug exactly -- `RootA` gets demoted with `reassigned_to: null`, its weight gone.

- **`_bone_influence()` matched a mesh's Armature modifier target by object identity
  (`m.object == armature_obj`)**, which misses a mesh skinned to a *different* object that happens
  to share the same armature *datablock* (Blender allows several ARMATURE objects to point at one
  `bpy.types.Armature`, e.g. linked-duplicate rigs). `run()`'s own per-armature-object loop could
  also visit the same shared datablock twice, editing already-removed bones on the second pass.
  Fixed by matching on `m.object.data == armature_obj.data` instead of object identity throughout
  (`_bone_influence`, `_transfer_bone_weight_to_parent`), and by deduping `run()`'s loop on
  `arm.data` so each shared datablock is processed exactly once, with every mesh skinned to *any*
  object sharing that data considered together.

- **`_bone_influence()` re-scanned every mesh's full vertex/vertex-group data on every single
  pruning round or capped-bone removal**, since both `_remove_unused_bones()`'s fixpoint loop and
  `_cap_bone_count()`'s per-victim loop called it fresh each time. Neither function's own weight
  *changes* actually require a fresh scan: `_remove_unused_bones` never transfers weight at all
  (a bone it removes is dead by definition), and `_cap_bone_count`'s only weight change per
  iteration -- moving a victim's cached influence onto its parent -- is a single dict update, not
  a reason to re-read mesh data. Both functions now compute `_bone_influence` once up front and
  (for the capping path) keep it in sync incrementally as bones are removed, rather than
  rescanning. `_bone_influence` itself was also doing one pass per vertex group per mesh
  (`for vg: for v: for g: if g.group == vg.index`) instead of one pass per mesh regardless of
  group count -- switched to a single `for v: for g` pass keyed by group index.

- **`_animated_bone_names()`'s regex (`r'pose\.bones\["([^"]+)"\]'`) stopped at the first `"`**,
  but Blender escapes a literal quote or backslash inside a bone name (`BLI_str_escape`) when it
  builds an fcurve's `data_path`, e.g. a bone literally named `Fin"Left` produces the path
  `pose.bones["Fin\"Left"].location`. The old regex's capture group ended at that escaped quote,
  so the matched "bone name" was truncated and never equaled the real `Bone.name` -- an animated
  bone with a quote or backslash in its name would silently fail the `in animated` check and
  become removable. Fixed with an escape-aware capture (`(?:\\.|[^"\\])*`) followed by
  `bpy.utils.unescape_identifier()` to reverse Blender's own escaping before comparing.

## A fractional last keyframe can get truncated to an integer frame on glTF export, with no decimation involved

Verified on fox.glb's own "Run" animation, whose real last keyframe sits at frame 27.8 (not a
round number -- Khronos's sample assets aren't always authored on whole frames). A plain
`optimize.py fox.glb -o out.glb` with **no** flags at all (`--keyframe-decimate` included)
re-exports it with `frame_end` truncated to 27.0 -- `info.py`'s reported animation duration
shrinks accordingly. This is `optimize.py`'s own default (non-multi-action) glTF export path
rounding/clamping frame bounds, not a bug introduced by `--keyframe-decimate` (RM-035): a test
comparing `--keyframe-decimate`'s before/after animation bounds has to diff against a plain
pass-through export of the same file through the same export path, not against the original
source file, or it flags this pre-existing, unrelated quirk as a regression. Not yet root-caused
further (which exact export kwarg governs it) or fixed -- filed here so it isn't rediscovered
from scratch and isn't confused with a `--keyframe-decimate` correctness bug.

## Measuring keyframe-removal error against a straight line is only exact for LINEAR interpolation

`--keyframe-decimate`'s first version (RM-035) measured every candidate keyframe's removal error
against the *straight line* between its two surviving neighbors, regardless of which
interpolation mode actually governs that segment in Blender. That's exact for `LINEAR`, but
wrong for `CONSTANT`: a CONSTANT-governed segment holds its *starting* keyframe's value flat
right up to the next keyframe, not a ramp. Caught by review with this exact counterexample: a
CONSTANT curve `(0, 0.0)`, `(5, 1.0)`, `(10, 1.0)` and `--keyframe-decimate 0.75`. The
straight-line estimate at frame 5 is `|1.0 - 0.5| = 0.5` (under the 0.75 tolerance, so the old
code removed it) -- but the *real* post-removal curve holds `0.0` from frame 0 to frame 10 under
CONSTANT interpolation, so the actual error at frame 5 is `|1.0 - 0.0| = 1.0`, nearly double what
was promised. Fixed by computing the exact value for `LINEAR` and `CONSTANT` segments (a
straight line and a flat step, respectively -- both cheap, closed-form) and, for every other
interpolation mode (`BEZIER` -- Blender's own default for a hand-keyed action -- and the named
easing curves), never removing anything in that segment at all: reproducing Blender's real
Bezier evaluation (frame is itself a cubic function of the curve parameter, via each keyframe's
own handle positions) precisely enough to still guarantee the requested tolerance is out of
scope, and guessing risks silently exceeding it the same way the straight-line estimate did for
CONSTANT. Not a hobbled feature for this skill's primary real input, though: every fcurve
Blender's own glTF importer produces uses LINEAR interpolation (verified on fox.glb, all 10458
keyframe points) -- the `keyframe_curve.blend` test fixture was switched from Blender's default
BEZIER to explicit LINEAR for the same reason, so its hand-computed expected RDP output stays
exact rather than an approximation over curved segments.

## Removing a shape key automatically re-points its dependents onto its own relative_key

A shape key can be defined relative to another shape key instead of Basis (`key_block.
relative_key`), so `--remove-unused-shape-keys` (RM-036) needed to know what happens to a shape
key B that's relative to a shape key A, once A is removed for being geometrically dead (zero
displacement from *its own* relative_key). Verified directly: `obj.shape_key_remove(A)` does not
leave B's `relative_key` dangling or null -- Blender automatically re-points B onto A's own
`relative_key` (in the simple case, Basis). This is exactly the "splice out a dead link and land
its dependents on the next one up" behavior a hand-rolled implementation would otherwise need to
build itself (compare `_remove_unused_bones`'s and `_cap_bone_count`'s own weight-transfer code
for bones, which have no such built-in operator support). Because a *dead* key is by definition
geometrically identical to whatever it's relative to, this re-pointing is always lossless here --
B's absolute shape (its displacement from Basis, following the whole chain) is unchanged, only
which key it's *directly* relative to moves up one link. `_remove_unused_shape_keys` still runs
as a fixpoint loop (removing one link at a time and re-measuring), the same pattern as
`_remove_unused_bones`, so a multi-level dead chain (A dead relative to Basis, B dead relative to
A, C real relative to B) fully resolves in one call rather than leaving inner links behind.

## A shape key's `relative_key` displacement only means "geometrically inert" in Relative mode

`Key.use_relative` (default `True` for anything made via `shape_key_add()`, so never an issue on
a glTF import or normally-authored file) can be set `False` to make a mesh's shape keys play as
an Absolute, timed sequence instead of a relative blend -- each key is a state at its own
`eval_time`, interpolated against its *neighbors in the key stack* (LINEAR/CARDINAL/
CATMULL_ROM/BSPLINE), not blended from `relative_key` at all. `_remove_unused_shape_keys`'s
first version measured every key's displacement from `relative_key` regardless of this flag --
in Absolute mode that number doesn't describe how the key actually functions: a key that happens
to be geometrically identical to its relative_key (a genuinely dead link in Relative mode) can
still be a real, load-bearing waypoint in an Absolute sequence, and removing it changes how
Blender interpolates between the states around it. Caught by review; fixed by skipping any mesh
whose `Key.use_relative` is `False` entirely -- verified on a purpose-built fixture
(`absolute_shape_key_rig.blend`: Basis, a geometrically-identical-to-Basis `StateA`, and a real
`StateB`, with `use_relative = False`) two ways: with the guard, nothing is removed; reverting
just the guard reproduces the bug exactly, removing `StateA`.

## Sharing mesh data means sharing more than geometry: shape-key state and vertex weights too

`--instance-duplicate-meshes` (RM-037) merges mesh objects onto one shared datablock when
proven fully identical -- but "fully identical geometry" isn't the same question as "safe to
share data". Two real Blender data-model facts made this feature need to *exclude* certain
objects outright, not just check them for equality:

- A shape key's *value* (the blend-weight slider) is a property of the `ShapeKey`, which lives
  in the mesh's shared `Key` datablock -- not the object. Two objects that share one mesh
  therefore share one Key datablock, and so share the *same current shape-key values* too. Two
  geometrically-identical props that happen to have their shape keys posed differently right now
  would have that difference silently erased if merged.
- A vertex's bone-weight assignments (`MeshVertex.groups`) live on the mesh's own vertex data,
  not the object -- only the *definitions* (`Object.vertex_groups`, the named groups themselves)
  are per-object. Two skinned objects sharing one mesh would be forced to carry identical weight
  assignments even if their `vertex_groups` lists differ.

Both are excluded from instancing candidacy entirely (`_mesh_is_animatable`), regardless of what
a geometry-equality check would otherwise conclude, rather than trying to detect "would this
particular merge actually change anything" case by case.

Separately: info.py's own `duplicate_mesh_candidates` (RM-015) uses a deliberately loose,
rotation/translation-invariant fingerprint (vertex/triangle count, area, volume, sorted bbox
dims) appropriate for a human-reviewed *suggestion* -- it's not safe grounds for an *automatic*
merge, since two meshes can match all five of those numbers while differing in UVs, vertex
colors, or which Material datablocks they reference. `_mesh_fully_identical` checks real
equality within a tight 1e-6 floating-point tolerance -- not a lossy geometric fingerprint, but
not literal bit-for-bit either, since two meshes built the same way can still differ in the last
bit or two (indexed vertex positions, face topology and per-face material_index/use_smooth,
sharp-face/sharp-edge marks, every UV layer's name/active-render flag and coordinates, every
color attribute's name/values and the mesh's render-fallback selection, and the same Material
datablock references) instead. Verified on a purpose-built fixture
(`instancing_rig.blend`): two cubes matching the loose signature exactly, differing only by a
shifted UV layer, are correctly left unmerged -- the loose fingerprint alone would have flagged
them as a merge candidate.

## A shared mesh datablock can have one animatable user and one that isn't -- filter datablocks, not just objects

`_instance_duplicate_meshes`'s first version filtered *objects* by `_mesh_is_animatable()` before
grouping by `obj.data`: `[o for o in mesh_objs if not _mesh_is_animatable(o)]`, then
`by_data.setdefault(o.data, []).append(o)`. That's wrong when a datablock already has *more than
one* user, which is a real, normal Blender scenario (a linked duplicate, or a shape-keyed/
Armature-modified copy added to only one of two objects that started out sharing data): the
excluded animatable object's own reference to that datablock never showed up in `by_data` at
all, so if the datablock got picked as a *non-canonical* duplicate in some other identical-mesh
group, `bpy.data.meshes.remove()` would free a datablock the excluded object was still silently
pointing at. Caught by review. Fixed by grouping *every* mesh object by its datablock first, then
dropping a datablock from candidacy entirely if *any* of its users fails `_mesh_is_animatable` --
one animatable user is enough to make the whole shared datablock unsafe to touch, even if its
other users would have been individually eligible.

## A shared Material's node tree can look up a UV layer or color attribute by name, not just position

`_mesh_fully_identical` originally compared UV layers and color attributes by matching *position*
in each mesh's list and comparing only their coordinate/color values -- not their names, active-
render flags, or (for color attributes) the mesh's own render-fallback selection
(`color_attributes.default_color_name`/`render_color_index`). That's not enough: a material's own
node tree can reference a specific layer by name (`ShaderNodeUVMap.uv_map`,
`ShaderNodeVertexColor.layer_name`), and an empty name falls back to whichever layer the *mesh*
currently marks as its active-render UV layer or default color attribute. Two meshes could have
identical UV/color values under different layer names, or the same names but a different
render-fallback selection, and still match every check that only looked at values -- yet the
exact same shared Material could sample different data (or nothing) for each once merged. Caught
by review; fixed by also comparing each UV layer's `name`/`active_render` and each color
attribute's `name`, plus the mesh-level `default_color_name`/`render_color_index` fallback
(display-only `active_color_index` deliberately excluded -- it affects only what's shown in the
UI's active-attribute indicator, nothing about how a material samples the mesh).

## `bl_rna` marks a POINTER property "read-only" for a mutable nested struct too, not just for a fixed value

`_merge_materials` (RM-038) needs true node-tree equality, so it walks every node's own RNA
properties generically instead of hand-listing one comparison per node type. The first version
skipped any property Blender reports `is_readonly` on the assumption that "read-only" means "not
worth comparing, since it can't meaningfully differ between two otherwise-identical nodes" --
true for a computed value like a node's `dimensions`, but not for `ShaderNodeValToRGB.color_ramp`
or `ShaderNodeTexImage.image_user`: Blender marks the *property* read-only because you can't
reassign which `ColorRamp`/`ImageUser` struct it points to, not because the struct's own fields
(a Color Ramp's stops, an Image User's frame settings) are fixed -- those are freely mutable
through the struct itself. Naively skipping every read-only property meant two Color Ramp nodes
with completely different ramps compared as identical. Reproduced directly: two `ShaderNodeValToRGB`
nodes, one with its first stop moved and recolored, still returned `True` from
`_node_fully_identical` before the fix, and `False` after it -- confirmed both directions. Fixed
by only skipping a read-only property when its *value* is a plain scalar/array, and walking a
read-only pointer to a struct (recursively, since that struct's own properties can nest the same
way) or a collection the same as any other nested value instead of skipping it.

## Two separate nodes' own struct-typed properties are never `==`, even with identical field values

A closely related trap in the same equality check: comparing `node_a.image_user == node_b.image_user`
directly (rather than walking their fields) would *always* return `False` for two different
nodes, even when every field inside matches -- Blender's `bpy_struct.__eq__` for a non-ID struct
is a pointer/identity compare, and two separate nodes always own two separate struct instances.
Handled by the same recursive walk as above: a nested non-ID struct's own value is its *fields'*
values, not the Python object's identity -- unlike an ID datablock such as an `Image` or a
`NodeTree`, where identity genuinely is the right comparison (two texture nodes pointing at
separately-created but pixel-identical images are correctly left unmerged, since editing one
would never reach the other).

## A material's own "users" are more than mesh.materials and OBJECT-linked object.material_slots -- use ID.user_remap, not a hand-enumerated walk

`_reassign_material_users`'s first version walked `bpy.data.meshes` (each mesh's own
`.materials` slot list) plus `bpy.data.objects`' OBJECT-linked `material_slots` overrides, then
called `bpy.data.materials.remove(dup)`. That misses any *other* real material user Blender's
own data model has: a `Curve`/`Text`/`MetaBall`/`GreasePencil`/`Volume` datablock has its own
separate `.materials` slot list too, exactly like `Mesh.materials`, just never enumerated.
Reproduced directly: a Curve object's material, structurally identical to a Mesh object's,
still showed up in `curve_data.materials` completely unchanged after calling the old
`_reassign_material_users` -- so `bpy.data.materials.remove(dup, do_unlink=True)` (the default)
would have silently dropped the curve's material slot to `None` rather than reassigning it,
losing a real material assignment on export. Caught by review; fixed by calling Blender's own
`dup.user_remap(canonical)` instead of a hand-enumerated walk -- it redirects every real ID user
in one call, verified to also correctly handle the OBJECT-linked slot-override case the old code
special-cased, leaving `dup.users == 0` afterward either way.

## A platform's real triangle cap can be per-mesh, not per-scene -- don't blindly sum before comparing

RM-039's `--target-roblox` preset's first version reused the same "sum every mesh object's
triangles, then derive one shared decimate ratio from that total" logic `--target-vrchat`/
`--target-quicklook` use -- correct for those two (VRChat's own Performance Rank system evaluates
an avatar's *total* triangle count across every one of its mesh renderers combined, and Apple's
AR Quick Look guidance is a whole-scene/object budget for the thing actually being previewed), but
wrong for Roblox: its real published limit ("individual meshes cannot exceed 20,000 triangles",
create.roblox.com/docs/art/modeling/specifications) is explicitly *per mesh*, not a scene total.
Applying it as an aggregate could needlessly decimate two already-individually-compliant meshes
just because their *combined* total crossed 20,000 -- a case Roblox's real limit never actually
restricts. Caught by review. Fixed by giving each `triangle_budget` preset an explicit
`triangle_budget_scope` ("aggregate", the default, or "per_mesh"), and clamping each mesh object
independently against the same absolute number when it's "per_mesh" -- never assume every
per-object platform budget generalizes to "sum the whole file and derive one ratio" just because
that's what two out of three real examples happened to want.

## A PSNR score of +inf is textbook-correct but not valid JSON -- cap it at a finite sentinel

`_psnr`'s textbook definition sends identical images (MSE=0) to `+inf`. Python's own `json.dump`
happily serializes `float('inf')` by default (`allow_nan=True`) as the bare token `Infinity`,
which `json.load` round-trips fine within this project's own Python-to-Python bpy<->host pipe --
but `Infinity` is not valid JSON per RFC 8259, so a coding agent parsing `look.py --compare`'s
`--json` output with a strict parser (e.g. `JSON.parse` in Node, or most non-Python JSON
libraries) would throw on exactly the "the two images are identical" case, the most common one in
practice (comparing a render against itself, or verifying a lossless conversion changed nothing).
Fixed by reporting the finite sentinel `_PSNR_IDENTICAL_DB = 100.0` instead of `float('inf')` --
well above any PSNR a real lossy difference would produce, so it stays distinguishable in
practice, while keeping the field a normal JSON number every consumer can parse.

## SSIM's box-window simplification is a real, deliberate deviation from the reference formula

`_ssim`'s local mean/variance/covariance statistics use a uniform box window (`_box_local_mean`,
via `numpy.pad` + `numpy.lib.stride_tricks.sliding_window_view`) rather than the SSIM literature's
standard Gaussian-weighted window, to avoid adding a scipy dependency to a script that otherwise
only needs numpy (already bundled with Blender's own Python, unlike scipy which is not). Scores
still trend the same direction (near 1.0 for near-identical images, near 0 for very different
ones, confirmed against solid-color images' closed-form values and a real before/after decimation
render pair), but do not expect them to match `skimage.metrics.structural_similarity`'s output
bit-for-bit on the same two images -- treat this tool's SSIM as a self-consistent relative
similarity signal, not an interoperable implementation of the reference algorithm.

## A NaN/inf pixel can reach PSNR/SSIM from a real file, not just a contrived array -- and math.log10(0.0) crashes, it doesn't return inf

`Image.pixels` places no restriction on the float values it holds -- `_psnr`'s and `_ssim`'s
first versions assumed every pixel was a normal finite float in [0, 1], which a real HDR/EXR
render (a blown-out specular highlight, or a NaN from a compositing divide-by-zero) can violate.
Confirmed two distinct real failure modes on a real Blender 4.2.23, not just theoretical: (1) a
literal NaN pixel survives Blender's own OPEN_EXR writer/reader round trip unchanged (a fixture
loaded back this way, `tests/fixtures/nan_pixel.exr`, still has `isnan().any() == True`), making
`_psnr`/`_ssim` return NaN, which `json.dump` serializes as the bare token `NaN` -- invalid JSON
per RFC 8259; (2) a literal `inf` pixel does *not* survive that same EXR round trip (Blender's own
writer clamps it to a large finite value on save), but an in-memory array can still hold one
(confirmed via direct `.pixels.foreach_set()`, no file involved) -- and if it ever reaches `_psnr`
with an infinite MSE, `math.log10(1.0 / mse)` becomes `math.log10(0.0)`, which *raises*
`ValueError: math domain error` rather than returning `inf` (Python's `math.log10` only special-
cases NaN input, not the zero-argument case that an infinite MSE produces). Caught by review;
fixed by short-circuiting `_psnr` on a non-finite MSE (returns NaN instead of calling `log10`),
and by having `_mode_compare` check `math.isfinite()` on both scores before including them,
falling back to `score_skipped` otherwise -- never assume image pixel data is well-behaved just
because it loaded without an error.
