"""bpy side of split.py: split one multi-object file into one output file per independent
object hierarchy (a root object -- no parent -- plus every descendant, recursively).

A literal "one file per bpy object" split would break any object that depends on another one it
isn't parented to via the normal parent chain (most commonly a skinned mesh's Armature modifier
target) -- confirmed on tests/fixtures/fox.glb: the "fox" mesh is parented to the "root" armature
object, so root+fox is one hierarchy and must stay together. Splitting by root-object groups, not
by individual object, is what keeps each output file self-contained and re-importable on its own.

Blender's own glTF importer also synthesizes a small "Icosphere" mesh object as a shared bone
custom-shape display widget for every skinned armature it imports (confirmed: not present in
fox.glb's own JSON at all -- purely a viewport-visualization aid Blender creates on import,
referenced by every pose bone's `custom_shape`; see _compat.strip_import_helper_objects and
references/pitfalls.md). It has no parent, so without stripping it first it would be
misidentified as its own independent hierarchy and exported as a spurious, meaningless output
file -- stripped before grouping, below.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common
import _compat

import bpy


def _hierarchy(root):
    group = [root]
    frontier = [root]
    while frontier:
        parent = frontier.pop()
        children = [o for o in bpy.data.objects if o.parent is parent]
        group.extend(children)
        frontier.extend(children)
    return group


def _safe_filename(name: str) -> str:
    keep = "-_. ()"
    return "".join(c if c.isalnum() or c in keep else "_" for c in name).strip() or "object"


def run(args):
    _compat.reset_scene()
    _compat.import_file(args["path"], args["format"])

    # Discard Blender's own synthesized bone-shape widget(s) first -- see the module docstring --
    # before it can be misidentified as an independent object hierarchy below.
    _compat.strip_import_helper_objects()

    # Blender's own glTF importer parks certain objects (confirmed: a node not reachable from
    # the file's default scene roots) in an auto-created "glTF_not_exported" collection with
    # Collection.hide_viewport = True. Unhiding that collection alone is NOT enough: even with
    # hide_viewport cleared and the object's own visible_get()/select_get() both confirmed True,
    # a use_selection=True export of that object still silently produced an empty (132-byte)
    # output -- Blender's glTF exporter excludes an object by its *collection membership*, not
    # by runtime visibility/selection state. Confirmed fix: move the object out of that
    # collection and into the scene's root collection before exporting. So every remaining
    # object is relinked into the scene's root collection here, unconditionally, rather than
    # only unhiding -- this also sidesteps any other importer-created collection with the same
    # kind of export-time exclusion.
    scene_coll = bpy.context.scene.collection
    for obj in list(bpy.data.objects):
        for coll in list(obj.users_collection):
            coll.hide_viewport = False
            if coll is not scene_coll:
                coll.objects.unlink(obj)
        if obj.name not in scene_coll.objects:
            scene_coll.objects.link(obj)

    roots = [o for o in bpy.data.objects if o.parent is None]
    out_dir = Path(args["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    out_fmt = args["output_format"]
    ext = args["output_ext"]

    groups = []
    used_names = set()
    for root in roots:
        group = _hierarchy(root)
        for o in bpy.data.objects:
            o.select_set(o in group)
        bpy.context.view_layer.objects.active = root

        base = _safe_filename(root.name)
        name = base
        n = 1
        while name in used_names:
            n += 1
            name = f"{base}_{n}"
        used_names.add(name)

        out_path = out_dir / f"{name}{ext}"
        _compat.export_selected(str(out_path), out_fmt)
        groups.append({
            "root_object": root.name,
            "objects": [o.name for o in group],
            "output": str(out_path),
        })

    return {
        "input": args["path"],
        "groups": groups,
    }


if __name__ == "__main__":
    _common.main(run)
