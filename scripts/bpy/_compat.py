"""bpy API surface that differs by format or Blender version, in one place.

Every operator name/kwarg below was confirmed against a real Blender 4.2.23 LTS (the floor
this skill supports) round-tripping a glTF sample through every format: see
tests/test_all.py::test_roundtrip_all_formats. Blender 4.0 replaced the old addon-based
OBJ/STL/PLY importers (`import_scene.obj`, etc.) with native `wm.*_import`/`wm.*_export`
operators; since 4.2 is the floor here, only the native ones are used -- no version branch
needed for that. If a future Blender major renames an operator, add the branch here (guarded
on `bpy.app.version`), not in the calling script.
"""
from __future__ import annotations

import bpy

# format -> (import operator, export operator), as (module, name) pairs on bpy.ops.
_OPS = {
    "gltf": (("import_scene", "gltf"), ("export_scene", "gltf")),
    "fbx": (("import_scene", "fbx"), ("export_scene", "fbx")),
    "obj": (("wm", "obj_import"), ("wm", "obj_export")),
    "stl": (("wm", "stl_import"), ("wm", "stl_export")),
    "ply": (("wm", "ply_import"), ("wm", "ply_export")),
    "usd": (("wm", "usd_import"), ("wm", "usd_export")),
    "usdz": (("wm", "usd_import"), ("wm", "usd_export")),  # same operators; extension picks the variant
    "abc": (("wm", "alembic_import"), ("wm", "alembic_export")),
}


# format -> the export operator's own "only the selected objects" boolean parameter name --
# every exporter spells this differently. Confirmed against each operator's own bl_rna on real
# Blender 4.2.23. blend/ has no such concept (a .blend always saves the whole file).
_SELECTION_PARAM = {
    "gltf": "use_selection",
    "fbx": "use_selection",
    "obj": "export_selected_objects",
    "stl": "export_selected_objects",
    "ply": "export_selected_objects",
    "usd": "selected_objects_only",
    "usdz": "selected_objects_only",
    "abc": "selected",
}


def _op(pair):
    mod, name = pair
    return getattr(getattr(bpy.ops, mod), name)


def reset_scene() -> None:
    """A clean, empty scene -- the bpy-side equivalent of `--factory-startup` for the *data*,
    not just the UI/prefs. Called at the top of every bpy/*.py before importing anything, so
    two calls in the same process (there aren't any today, but a future batch mode might) never
    see each other's leftover data."""
    bpy.ops.wm.read_factory_settings(use_empty=True)


def import_file(path: str, fmt: str) -> None:
    if fmt == "blend":
        # Loading a .blend replaces the whole session; load_ui=False keeps this headless run
        # from pulling in the file's saved screen layout/workspaces (irrelevant with no UI, and
        # a leftover 3D cursor/viewport state has caused a stray override warning in some builds).
        bpy.ops.wm.open_mainfile(filepath=path, load_ui=False)
        return
    if fmt not in _OPS:
        raise ValueError(f"no importer for format {fmt!r}")
    _op(_OPS[fmt][0])(filepath=path)


# Per-format defaults that make "convert" actually preserve what a user expects it to (embedded
# textures, kept animation), overriding operators whose own default is the leaner option.
# Confirmed against a real Blender 4.2.23 (tests/test_all.py::test_roundtrip_all_formats):
# glTF exports animation by default already; FBX bakes animation by default but does NOT embed
# textures by default; USD/USDZ do NOT export animation by default.
_EXPORT_DEFAULTS = {
    "fbx": {"embed_textures": True, "path_mode": "COPY"},
    "usd": {"export_animation": True},
    "usdz": {"export_animation": True},
}


def export_file(path: str, fmt: str, **kwargs) -> None:
    if fmt == "blend":
        bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
        return
    if fmt == "gltf":
        # export_scene.gltf infers .glb vs .gltf from export_format, not the filepath extension.
        kwargs.setdefault("export_format", "GLB" if path.lower().endswith(".glb") else "GLTF_SEPARATE")
    if fmt not in _OPS:
        raise ValueError(f"no exporter for format {fmt!r}")
    merged = dict(_EXPORT_DEFAULTS.get(fmt, {}))
    merged.update(kwargs)
    _op(_OPS[fmt][1])(filepath=path, **merged)


def export_selected(path: str, fmt: str, **kwargs) -> None:
    """Like export_file, but only the currently-selected objects -- used by split.py to write
    one object hierarchy at a time out of a multi-object scene. Raises ValueError for a format
    with no such concept (.blend always saves the whole file). The caller is responsible for
    selecting exactly the objects it wants first (obj.select_set(...)).
    """
    if fmt not in _SELECTION_PARAM:
        raise ValueError(f"{fmt!r} has no selection-only export option")
    kwargs[_SELECTION_PARAM[fmt]] = True
    export_file(path, fmt, **kwargs)


def eevee_engine_id() -> str:
    """The real-time engine's RNA identifier renamed between versions: Blender 4.2-4.5 shipped
    the new Eevee under 'BLENDER_EEVEE_NEXT' (with the legacy engine removed); Blender 5.0
    renamed it back to 'BLENDER_EEVEE' now that there is only one Eevee again. Confirmed on
    real Blender 4.2.23, 4.5.13 and 5.2.1 -- render.py and look.py call this instead of
    hardcoding either string."""
    available = {e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
    if "BLENDER_EEVEE_NEXT" in available:
        return "BLENDER_EEVEE_NEXT"
    return "BLENDER_EEVEE"


def object_triangle_count(obj) -> int:
    """Triangle count as the renderer sees it (quads/ngons counted as their triangulation),
    without permanently triangulating the mesh. bmesh avoids the destructive
    mesh.calc_loop_triangles() side effects on a mesh that might be shared by other objects."""
    import bmesh
    if obj.type != "MESH":
        return 0
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    count = len(bm.faces)
    bm.free()
    return count
