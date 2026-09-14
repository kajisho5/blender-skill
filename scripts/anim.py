#!/usr/bin/env python3
"""Extract animation-only data (armature + actions, no mesh) from a rigged file, or combine
several files' actions onto one base file into a single output with every action as its own
exportable clip -- a Mixamo-style workflow: download a rigged character once, then several
separate animation clips that share that character's bone names, and merge them together.

Usage:
  python3 scripts/anim.py character.glb --extract -o character_anim.glb
  python3 scripts/anim.py character.glb --extract --action Walk -o walk_only.glb
  python3 scripts/anim.py character.glb --combine walk.glb run.glb idle.glb -o all_anims.glb
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common
import _formats
import _run
from _common import SkillError

# --combine only has a tested multi-action export path for these -- see
# scripts/bpy/_compat.py's _MULTI_ACTION_KWARGS.
_MULTI_ACTION_FORMATS = {"gltf", "fbx"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("-o", "--output", required=True)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--extract", action="store_true", help="strip every non-armature object, keeping only the skeleton(s) and their actions")
    mode.add_argument("--combine", nargs="+", metavar="ANIM_FILE", help="import each file's actions (matched to this file's armature by bone name) and merge them all into one output")
    ap.add_argument("--action", action="append", dest="actions", metavar="NAME", help="with --extract, keep only this action (repeatable); default: keep every action found")
    _common.add_common_args(ap, fast=False, progress=False)
    args = ap.parse_args()

    try:
        in_path = _common.require_exists(args.input, "input")
        in_fmt = _formats.detect_format(str(in_path))
        if in_fmt is None:
            raise SkillError(f"unrecognized input extension: {in_path.suffix}", kind="input")
        out_path = Path(args.output)
        out_fmt = _formats.detect_format(str(out_path))
        if out_fmt is None:
            raise SkillError(f"unrecognized output extension: {out_path.suffix}", kind="input")

        if args.combine:
            if out_fmt not in _MULTI_ACTION_FORMATS:
                raise SkillError(
                    f"--combine only supports output formats {sorted(_MULTI_ACTION_FORMATS)} "
                    f"(glTF exports every action as its own clip; FBX bakes every action) -- got {out_fmt!r}",
                    kind="input",
                )
            anim_sources = []
            for raw in args.combine:
                p = _common.require_exists(raw, "combine source")
                fmt = _formats.detect_format(str(p))
                if fmt is None:
                    raise SkillError(f"unrecognized extension for combine source: {p.suffix}", kind="input")
                anim_sources.append([str(p.resolve()), fmt])
            bpy_args = {
                "mode": "combine",
                "path": str(in_path.resolve()), "format": in_fmt,
                "anim_sources": anim_sources,
                "output": str(out_path.resolve()), "output_format": out_fmt,
            }
        else:
            bpy_args = {
                "mode": "extract",
                "path": str(in_path.resolve()), "format": in_fmt,
                "actions": args.actions,
                "output": str(out_path.resolve()), "output_format": out_fmt,
            }

        if args.dry_run:
            print(json.dumps({"args": bpy_args}, indent=2))
            return 0

        blender_bin = _run.find_blender(args.blender)
        result = _run.run_bpy("anim.py", bpy_args, blender_bin=blender_bin, timeout=args.timeout)
    except SkillError as err:
        return _common.fail(err, args.json)

    if not result.get("ok"):
        return _common.fail(SkillError(result["error"]["message"], kind=result["error"].get("kind", "internal")), args.json)

    data = result["data"]
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    elif "combined" in data:
        print(f"{args.input} + {len(data['combined'])} animation(s) -> {args.output}")
        print(f"  actions: {', '.join(data['actions_in_output'])}")
    else:
        print(f"{args.input} -> {args.output} ({len(data['actions'])} action(s) kept, mesh stripped)")
        print(f"  actions: {', '.join(data['actions'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
