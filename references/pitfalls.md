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

## Blender's bundled Python includes numpy

Not stdlib in the usual sense, but it ships with Blender itself (confirmed: `numpy 1.24.3` in
4.2.23's bundled interpreter) -- `scripts/bpy/*.py` (running inside Blender, not the host
Python) uses it for pixel-array work (`look.py`'s texture grid/compare, `render.py`'s sheet
mode, `bake.py`'s pixel-stat verification). The stdlib-only guarantee is about the *host*
scripts and about not requiring `pip install` anything; Blender's own bundled capabilities are
part of "depends on Blender", not an extra dependency.
