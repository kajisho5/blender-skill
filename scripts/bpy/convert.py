"""bpy side of convert.py: import one format, export another, optionally re-import the result
and diff it against the source (--verify) so a conversion is checked, not assumed.

Reuses info.py's run() for both halves of the diff -- info.py already does
reset scene -> import -> measure, which is exactly "load the file and tell me what's in it".
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common  # noqa: F401  (imported for side effects consistency with other bpy scripts)
import _compat
import info as info_mod


def _compare(before: dict, after: dict) -> dict:
    diffs = []
    bm, am = before["meshes"], after["meshes"]
    if bm["count"] != am["count"]:
        diffs.append(f"mesh count: {bm['count']} -> {am['count']}")
    if bm["triangles"] != am["triangles"]:
        diffs.append(f"triangles: {bm['triangles']} -> {am['triangles']}")
    if bm["vertices"] != am["vertices"]:
        diffs.append(f"vertices: {bm['vertices']} -> {am['vertices']}")
    if before["materials"]["count"] != after["materials"]["count"]:
        diffs.append(f"materials: {before['materials']['count']} -> {after['materials']['count']}")
    if len(before["animations"]) != len(after["animations"]):
        diffs.append(f"animation clips: {len(before['animations'])} -> {len(after['animations'])}")
    return {"matches": not diffs, "diffs": diffs}


def run(args):
    before = info_mod.run({"path": args["input"], "format": args["input_format"]})
    # The scene from info_mod.run()'s import is still loaded -- export it directly rather than
    # reloading, so the exported file is exactly what was just measured.
    _compat.export_file(args["output"], args["output_format"])
    result = {"input": before, "output_path": args["output"]}
    if args.get("verify"):
        after = info_mod.run({"path": args["output"], "format": args["output_format"]})
        result["output"] = after
        result["verify"] = _compare(before, after)
    return result


if __name__ == "__main__":
    _common.main(run)
