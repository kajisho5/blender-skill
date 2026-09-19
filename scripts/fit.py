#!/usr/bin/env python3
"""Automatically re-optimize a file in escalating stages until it fits one of check.py's own
named targets (three.js, unity, unreal, godot, ios-ar, android-ar, webxr, 3d-print, sketchfab) --
or reports honestly, with a non-zero exit code, that it still doesn't after a bounded number of
attempts. Never silently ships an over-budget file as if it passed.

Reuses check.py's own target/budget definitions directly (the one real definition of "budget"
for each named target this skill has) rather than inventing a second one, and always builds each
stage fresh from the *original* input (never chaining off a previous stage's own output), so
repeated attempts never compound decimation error onto an already-lossy result.

Only three of check.py's rows have a real, automatable lever in this skill at all -- triangle
budget (--decimate-ratio), texture size (--texture-max), and transmission size (compression +
texture size): every other row (missing textures, no UV map, non-manifold geometry, USDZ
packaging, wall thickness) check.py's own fix text already says isn't automated by this skill,
so no amount of staging here would ever fix one -- this tool only loops on the three it actually
can act on.

Triangle and texture-size budgets get an exact, one-shot fix at stage 0, read straight back from
optimize.py's own reported triangles_after (decimate ratio has no need to be guessed
iteratively -- confirmed precise directly against real Blender) -- never by re-importing the
stage's own output through info.py to re-measure it: when a stage adds --meshopt/--ktx2
compression (needed for the transmission-size budget), Blender itself cannot re-import the
result at all (a real, already-documented limitation -- see references/pitfalls.md), so this
tool never attempts to. Transmission size is read directly off the output file's own bytes on
disk, which works regardless of compression -- and is the one genuinely unpredictable dimension
(compression ratio can't be computed in advance, only measured after the fact), so it's the one
that actually needs staged escalation: each further stage halves the texture cap again (the most
direct real lever for shrinking file size) and/or tightens the decimate ratio, only for whichever
dimension a stage's own real, measured result is still over.

Usage:
  python3 scripts/fit.py model.glb -o model_fit.glb --target ios-ar
  python3 scripts/fit.py model.glb -o model_fit.glb --target webxr --max-stages 3
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common
import _delegate
import _formats
import _run
import check
from _common import SkillError

HERE = Path(__file__).resolve().parent
_DEFAULT_MAX_STAGES = 5


def _next_stage_params(row_status: dict, texture_max, decimate_ratio):
    """(texture_max, decimate_ratio) for the *next* stage, tightening whichever lever a still-
    failing row's own dimension maps to -- never touching a lever whose own dimension already
    passed, so a stage never re-degrades something that's already fine."""
    if row_status.get("triangle budget") == "WARN":
        decimate_ratio = max(0.01, (decimate_ratio or 1.0) * 0.7)
    if row_status.get("transmission size") == "WARN":
        texture_max = max(64, (texture_max or 4096) // 2)
    return texture_max, decimate_ratio


def _optimize_argv(input_path: str, output_path: str, texture_max, decimate_ratio, try_compress: bool, out_fmt: str) -> list:
    argv = [sys.executable, str(HERE / "optimize.py"), input_path, "-o", output_path, "--json"]
    if texture_max:
        argv += ["--texture-max", str(texture_max)]
    if decimate_ratio is not None:
        argv += ["--decimate-ratio", f"{decimate_ratio:.6f}"]
    if try_compress and out_fmt == "gltf" and _delegate.gltf_transform_available():
        argv.append("--meshopt")
        if _delegate.ktx_available():
            argv.append("--ktx2")
    return argv


def _stage_rows(tris_after: int, tri_budget, size_mb_budget, stage_out: str) -> list:
    """The subset of check.py's own row shape this tool can build without ever re-importing the
    stage's own output (see the module docstring) -- triangle budget straight from optimize.py's
    own report, transmission size from the file's own real bytes on disk. Texture size is never
    included: --texture-max is an exact cap (confirmed elsewhere in this codebase), so once
    applied it cannot fail, and is not worth a row that could only ever read PASS."""
    rows = []
    if tri_budget:
        rows.append({"check": "triangle budget", "status": "PASS" if tris_after <= tri_budget else "WARN",
                      "detail": f"{tris_after} {'<=' if tris_after <= tri_budget else '>'} {tri_budget}"})
    if size_mb_budget:
        size_mb = Path(stage_out).stat().st_size / (1024 * 1024)
        rows.append({"check": "transmission size", "status": "PASS" if size_mb <= size_mb_budget else "WARN",
                      "detail": f"{size_mb:.1f} MB {'<=' if size_mb <= size_mb_budget else '>'} {size_mb_budget} MB"})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--target", required=True, choices=check.TARGETS)
    ap.add_argument("--max-stages", type=int, default=_DEFAULT_MAX_STAGES, metavar="N",
                     help=f"maximum optimization attempts before giving up honestly (default {_DEFAULT_MAX_STAGES})")
    _common.add_common_args(ap, dry_run=False, fast=False, progress=False)
    args = ap.parse_args()

    try:
        in_path = _common.require_exists(args.file, "file")
        in_fmt = _formats.detect_format(str(in_path))
        out_fmt = _formats.detect_format(args.out)
        if in_fmt is None or out_fmt is None:
            raise SkillError("unrecognized file extension on input or output", kind="input")
        if args.max_stages < 1:
            raise SkillError("--max-stages must be at least 1", kind="input")

        _fmts, tri_budget, tex_budget, _need_uv, _need_manifold, size_mb_budget = check._SPEC[args.target]
        blender_bin = _run.find_blender(args.blender)
        out_path = Path(args.out).resolve()
        in_resolved = str(in_path.resolve())

        # A single upfront, cheap measurement (skip_uv_overlap_check -- see info.py's own
        # help/references/pitfalls.md for why: an uncapped O(n^2) scan this tool never reads
        # the result of anyway) purely to plan stage 0's own decimate ratio; never re-run on a
        # stage's own output afterward.
        info_result = _run.run_bpy("info.py", {"path": in_resolved, "format": in_fmt, "skip_uv_overlap_check": True},
                                    blender_bin=blender_bin, timeout=args.timeout)
        if not info_result.get("ok"):
            raise SkillError(info_result["error"]["message"], kind=info_result["error"].get("kind", "internal"))
        tris = info_result["data"]["meshes"]["triangles"]

        decimate_ratio = max(0.01, tri_budget / tris) if (tri_budget and tris > tri_budget) else None
        texture_max = tex_budget

        stages = []
        for stage_index in range(args.max_stages):
            stage_out = str(out_path) if stage_index == args.max_stages - 1 else str(
                out_path.with_name(f"{out_path.stem}_fitstage{stage_index}{out_path.suffix}"))
            argv = _optimize_argv(in_resolved, stage_out, texture_max, decimate_ratio,
                                   try_compress=bool(size_mb_budget), out_fmt=out_fmt)
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=args.timeout)
            if proc.returncode != 0:
                raise SkillError(f"optimize.py failed at stage {stage_index}: {(proc.stderr or proc.stdout).strip()[-500:]}", kind="internal")
            stage_result = json.loads(proc.stdout)

            rows = _stage_rows(stage_result["triangles_after"], tri_budget, size_mb_budget, stage_out)
            row_status = {r["check"]: r["status"] for r in rows}
            met = all(status == "PASS" for status in row_status.values())
            stages.append({"stage": stage_index, "texture_max": texture_max, "decimate_ratio": decimate_ratio,
                            "met_budget": met, "checks": rows})

            if stage_out != str(out_path):
                if met or stage_index == args.max_stages - 1:
                    shutil.move(stage_out, str(out_path))
                else:
                    Path(stage_out).unlink(missing_ok=True)

            if met:
                break
            texture_max, decimate_ratio = _next_stage_params(row_status, texture_max, decimate_ratio)
    except SkillError as err:
        return _common.fail(err, args.json)

    final = stages[-1]
    data = {
        "target": args.target, "stages_used": len(stages), "met_budget": final["met_budget"],
        "checks": final["checks"], "history": stages, "output_path": str(out_path),
    }
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        verdict = "fits" if final["met_budget"] else "still exceeds"
        print(f"{args.file} -> {args.out}: {verdict} the {args.target} budget after {len(stages)} stage(s)")
        for r in final["checks"]:
            print(f"  {r['status']:4s} {r['check']}: {r['detail']}")
        print(f"  (run check.py on {args.out} --target {args.target} for the full PASS/WARN/FAIL report, including rows this tool can't fix)")
    return 0 if final["met_budget"] else 1


if __name__ == "__main__":
    sys.exit(main())
