"""bpy side of anim.py: extract animation-only data (armature + actions, no mesh) from a file, or
combine several files' actions onto one base file -- a Mixamo-style workflow: download a rigged
character once, then several separate animation clips that share that character's bone names, and
merge them into one file with every action exported as its own clip.

--extract strips every non-armature object, keeping only the skeleton the kept actions need to be
re-attached to later (an action's fcurves are pose-bone-name paths, not tied to a specific
object -- see _compat.export_multi_action -- but *some* armature with matching bone names has to
be present for an exporter to include it at all).

--combine imports each source file in turn, keeps only the actions it introduced (bpy.data
diffed before/after each import) via use_fake_user, and discards the objects that import
brought in -- so only the base file's own objects remain in the output, with every combined
action riding along in bpy.data for the exporter to pick up.

Both modes strip Blender's own synthesized bone custom-shape widget right after import (see
_compat.strip_import_helper_objects) -- it is not real content (confirmed: absent from the
source file's own JSON; a viewport-visualization aid Blender creates for every skinned armature
it imports), and combine would otherwise leak an extra, spurious mesh from the *base* file's
import into an otherwise-correct combined output (each anim source's own copy is already
discarded anyway, since every object an anim source's import brings in gets deleted).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common
import _compat

import bpy


def _extract(args):
    _compat.reset_scene()
    _compat.import_file(args["path"], args["format"])

    wanted = args.get("actions")
    kept = []
    for action in list(bpy.data.actions):
        if wanted and action.name not in wanted:
            bpy.data.actions.remove(action)
        else:
            action.use_fake_user = True
            kept.append(action.name)

    for obj in list(bpy.data.objects):
        if obj.type != "ARMATURE":
            bpy.data.objects.remove(obj, do_unlink=True)

    for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.images):
        for block in list(coll):
            if block.users == 0:
                coll.remove(block)

    _compat.export_file(args["output"], args["output_format"])

    return {
        "input": args["path"],
        "output": args["output"],
        "actions": kept,
        "armatures_kept": [o.name for o in bpy.data.objects if o.type == "ARMATURE"],
    }


def _combine(args):
    _compat.reset_scene()
    _compat.import_file(args["path"], args["format"])
    # Discard Blender's own synthesized bone-shape widget from the base import before it can
    # leak into the combined output as a spurious extra mesh -- see _compat's docstring and
    # references/pitfalls.md. Each anim source's own copy is caught below by the new-object diff
    # instead, since (unlike the base's objects) every one of an anim source's imported objects
    # gets discarded anyway.
    _compat.strip_import_helper_objects()
    base_actions = sorted(a.name for a in bpy.data.actions)

    combined = []
    for anim_path, anim_fmt in args["anim_sources"]:
        before_objects = set(bpy.data.objects.keys())
        before_actions = set(bpy.data.actions.keys())
        _compat.import_file(anim_path, anim_fmt)
        new_objects = [o for o in bpy.data.objects if o.name not in before_objects]
        new_actions = [a for a in bpy.data.actions if a.name not in before_actions]

        for action in new_actions:
            action.use_fake_user = True
            combined.append({"source": anim_path, "action": action.name})
        for obj in new_objects:
            bpy.data.objects.remove(obj, do_unlink=True)

    for coll in (bpy.data.meshes, bpy.data.armatures, bpy.data.materials, bpy.data.images):
        for block in list(coll):
            if block.users == 0:
                coll.remove(block)

    _compat.export_multi_action(args["output"], args["output_format"])

    return {
        "input": args["path"],
        "output": args["output"],
        "base_actions": base_actions,
        "combined": combined,
        "actions_in_output": sorted(base_actions + [c["action"] for c in combined]),
    }


def run(args):
    if args["mode"] == "extract":
        return _extract(args)
    return _combine(args)


if __name__ == "__main__":
    _common.main(run)
