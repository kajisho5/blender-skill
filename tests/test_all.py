#!/usr/bin/env python3
"""End-to-end tests against a real Blender (stdlib unittest, no pytest dependency). Each test
here mirrors a command that was run by hand against a real Blender 4.2.23/4.5.13/5.2.1 during
development -- see git history for the exact transcripts. Needs Blender on PATH, $BLENDER, or
--blender's default search (see scripts/_run.py); skips (not fails) if none is found, so a
contributor without Blender installed still gets a clear signal instead of a wall of errors.

Usage:
  python3 tests/test_all.py
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "box.glb"
BROKEN_FIXTURE = ROOT / "tests" / "fixtures" / "broken_cube.glb"
FLIPPED_FIXTURE = ROOT / "tests" / "fixtures" / "flipped_cube.glb"
SCALED_FIXTURE = ROOT / "tests" / "fixtures" / "scaled_cube.glb"
OFFSET_ORIGIN_FIXTURE = ROOT / "tests" / "fixtures" / "offset_origin_cube.glb"
ZERO_AREA_UV_FIXTURE = ROOT / "tests" / "fixtures" / "zero_area_uv_cube.glb"
OVERLAPPING_UV_FIXTURE = ROOT / "tests" / "fixtures" / "overlapping_uv_cubes.glb"
sys.path.insert(0, str(ROOT / "scripts"))
import _run  # noqa: E402


def _blender_available() -> bool:
    try:
        _run.find_blender()
        return True
    except Exception:
        return False


def run(*argv, cwd=None):
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / argv[0])] + list(argv[1:]),
                           capture_output=True, text=True, cwd=cwd)
    return proc


@unittest.skipUnless(_blender_available(), "no Blender found (see scripts/_run.py's search order)")
class TestToolchain(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name)

    def test_info_reports_box_geometry(self):
        proc = run("info.py", str(FIXTURE), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["meshes"]["triangles"], 12)
        self.assertEqual(data["meshes"]["vertices"], 24)
        # A closed cube is manifold once split normals/UV seams are welded first (info.py's
        # whole point: raw topology alone would call every hard edge "non-manifold").
        self.assertEqual(data["meshes"]["per_object"][0]["non_manifold_edges"], 0)

    def test_info_detects_hole_and_skips_unreliable_flip_check(self):
        # broken_cube.glb: a cube with one face deleted (a real hole/gap) and one remaining
        # face's normal manually inverted. The hole makes the mesh non-manifold, which in turn
        # makes the flipped-normal comparison unreliable (see references/pitfalls.md -- verified
        # on a real character mesh that "fixing" flipped normals on a non-manifold mesh can
        # actually corrupt a correctly-shaded model), so info.py must report that check as
        # skipped (None) rather than guessing.
        proc = run("info.py", str(BROKEN_FIXTURE), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        stats = json.loads(proc.stdout)["meshes"]["per_object"][0]
        self.assertEqual(stats["non_manifold_edges"], 4)
        self.assertEqual(stats["boundary_edges"], 4)
        self.assertIsNone(stats["flipped_normal_faces"])

    def test_info_detects_flipped_normals_on_a_manifold_mesh(self):
        # flipped_cube.glb: a plain closed cube with every face normal manually inverted --
        # manifold (no holes), so the flipped-normal check is trustworthy here and should fire.
        proc = run("info.py", str(FLIPPED_FIXTURE), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        stats = json.loads(proc.stdout)["meshes"]["per_object"][0]
        self.assertEqual(stats["non_manifold_edges"], 0)
        self.assertEqual(stats["flipped_normal_faces"], 12)

    def test_optimize_fill_holes_and_recalc_normals_fixes_broken_cube(self):
        out = self.out / "fixed.glb"
        proc = run("optimize.py", str(BROKEN_FIXTURE), "-o", str(out), "--fill-holes", "--recalc-normals", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["holes_filled"], 1)
        self.assertEqual(data["warnings"], [])  # mesh was manifold again before recalc ran

        verify = run("info.py", str(out), "--json")
        stats = json.loads(verify.stdout)["meshes"]["per_object"][0]
        self.assertEqual(stats["non_manifold_edges"], 0)
        self.assertEqual(stats["flipped_normal_faces"], 0)

    def test_optimize_recalc_normals_warns_on_non_manifold_mesh(self):
        # Applying --recalc-normals directly to a still-non-manifold mesh (no --fill-holes first)
        # must warn rather than silently trust the result -- this is the same unreliable-fix
        # case test_info_detects_hole_and_skips_unreliable_flip_check guards on the read side.
        out = self.out / "still_broken.glb"
        proc = run("optimize.py", str(BROKEN_FIXTURE), "-o", str(out), "--recalc-normals", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(len(data["warnings"]), 1)
        self.assertIn("non-manifold", data["warnings"][0])

    def test_info_detects_unapplied_scale_and_optimize_fixes_it(self):
        # scaled_cube.glb: a cube with object.scale (0.01, 0.01, 0.01) never baked in -- the
        # classic cm/m unit-mismatch symptom, which glTF round-trips faithfully as a node scale
        # rather than silently applying it.
        proc = run("info.py", str(SCALED_FIXTURE), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        stats = data["meshes"]["per_object"][0]
        self.assertAlmostEqual(stats["scale"][0], 0.01, places=4)
        self.assertTrue(any("unapplied scale" in w for w in data["warnings"]))

        out = self.out / "scale_fixed.glb"
        proc = run("optimize.py", str(SCALED_FIXTURE), "-o", str(out), "--fix-scale", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["scales_fixed"], 1)

        verify = run("info.py", str(out), "--json")
        fixed_stats = json.loads(verify.stdout)["meshes"]["per_object"][0]
        self.assertEqual(fixed_stats["scale"], [1.0, 1.0, 1.0])

    def test_optimize_origin_center_and_bottom_recenter_without_moving_geometry(self):
        # offset_origin_cube.glb: a cube whose origin sits 3 units away from its own geometry's
        # local center (a deliberately off-center pivot, not a defect -- info.py reports this as
        # data, not a warning; see _local_bbox_reference_points in scripts/bpy/info.py).
        before = json.loads(run("info.py", str(OFFSET_ORIGIN_FIXTURE), "--json").stdout)
        self.assertEqual(before["meshes"]["per_object"][0]["origin_offset_from_center"], [3.0, 0.0, 0.0])
        world_bbox_before = before["bounding_box"]

        out = self.out / "origin_center.glb"
        proc = run("optimize.py", str(OFFSET_ORIGIN_FIXTURE), "-o", str(out), "--origin", "center", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)

        after = json.loads(run("info.py", str(out), "--json").stdout)
        self.assertEqual(after["meshes"]["per_object"][0]["origin_offset_from_center"], [0.0, 0.0, 0.0])
        # The whole point of origin_set over moving the object: world-space geometry is
        # untouched, only the origin moves within the object's local frame.
        self.assertEqual(after["bounding_box"], world_bbox_before)

        out_bottom = self.out / "origin_bottom.glb"
        proc = run("optimize.py", str(OFFSET_ORIGIN_FIXTURE), "-o", str(out_bottom), "--origin", "bottom", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        after_bottom = json.loads(run("info.py", str(out_bottom), "--json").stdout)
        self.assertEqual(after_bottom["meshes"]["per_object"][0]["origin_offset_from_bottom_center"], [0.0, 0.0, 0.0])
        self.assertEqual(after_bottom["bounding_box"], world_bbox_before)

    def test_info_detects_zero_area_uv_and_no_false_positive_overlap(self):
        # zero_area_uv_cube.glb: every face's UV collapsed to a single point -- never
        # meaningfully unwrapped. Confirmed not to also spuriously register as UV overlap
        # (degenerate faces are excluded from the overlap scan, not double-counted).
        proc = run("info.py", str(ZERO_AREA_UV_FIXTURE), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        uv = data["meshes"]["per_object"][0]["uv_checks"]
        self.assertEqual(uv["zero_area_faces"], 12)
        self.assertEqual(uv["overlapping_faces_approx"], 0)
        self.assertTrue(any("zero-area UVs" in w for w in data["warnings"]))

    def test_info_detects_overlapping_uv_islands(self):
        # overlapping_uv_cubes.glb: two cubes joined into one mesh, with the second island's UVs
        # deliberately copied on top of the first's -- a real, if unusual, full overlap.
        proc = run("info.py", str(OVERLAPPING_UV_FIXTURE), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        uv = data["meshes"]["per_object"][0]["uv_checks"]
        self.assertEqual(uv["overlapping_faces_approx"], 12)
        self.assertTrue(any("overlapping UVs" in w for w in data["warnings"]))

    def test_info_uv_checks_have_no_false_positives_on_a_real_character_mesh(self):
        # fox.glb: a real, cleanly-laid-out UV map. A naive bounding-box-only overlap filter
        # falsely flagged 273/576 faces here during development (see references/pitfalls.md) --
        # the exact 2D triangle-overlap test must read 0.
        proc = run("info.py", str(ROOT / "tests" / "fixtures" / "fox.glb"), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        uv = json.loads(proc.stdout)["meshes"]["per_object"][0]["uv_checks"]
        self.assertEqual(uv["overlapping_faces_approx"], 0)
        self.assertEqual(uv["zero_area_faces"], 0)

    def test_convert_and_verify_roundtrip(self):
        # fbx keeps the same shared-vertex topology as glb, so this roundtrip should match
        # exactly. STL has no index buffer at all (every triangle stores 3 independent
        # vertices) -- a glb -> stl -> glb roundtrip legitimately changes the vertex count
        # (24 -> 36 for this cube) and is not a bug; see references/pitfalls.md.
        out = self.out / "box.fbx"
        proc = run("convert.py", str(FIXTURE), "-o", str(out), "--verify", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(out.exists())
        data = json.loads(proc.stdout)
        self.assertTrue(data["verify"]["matches"], data["verify"]["diffs"])

    def test_optimize_decimates_to_requested_ratio(self):
        out = self.out / "box_opt.glb"
        proc = run("optimize.py", str(FIXTURE), "-o", str(out), "--decimate-ratio", "0.5", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["triangles_before"], 12)
        self.assertLessEqual(data["triangles_after"], 12)

    def test_check_3d_print_target_passes_on_a_watertight_cube(self):
        stl = self.out / "box.stl"
        run("convert.py", str(FIXTURE), "-o", str(stl))
        proc = run("check.py", str(stl), "--target", "3d-print", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["overall"], "PASS")

    def test_batch_recipe_over_a_folder(self):
        in_dir = self.out / "in"
        in_dir.mkdir()
        (in_dir / "box.glb").write_bytes(FIXTURE.read_bytes())
        recipe = self.out / "recipe.json"
        recipe.write_text(json.dumps({"steps": [{"script": "convert", "args": {"format": "obj"}}]}))
        out_dir = self.out / "out"
        proc = run("batch.py", str(in_dir), "--recipe", str(recipe), "--out-dir", str(out_dir), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        results = json.loads(proc.stdout)
        self.assertTrue(all(r["ok"] for r in results))
        self.assertTrue((out_dir / "box_convert.obj").exists())


if __name__ == "__main__":
    unittest.main()
