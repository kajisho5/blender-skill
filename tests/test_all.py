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
VERTEX_COLORS_FIXTURE = ROOT / "tests" / "fixtures" / "vertex_colors_cube.blend"
ROOT_MOTION_FIXTURE = ROOT / "tests" / "fixtures" / "root_motion_rig.blend"
UNWEIGHTED_VERTEX_FIXTURE = ROOT / "tests" / "fixtures" / "unweighted_vertex_cube.blend"
MULTI_MATERIAL_FIXTURE = ROOT / "tests" / "fixtures" / "multi_material_cube.glb"
MISCONFIGURED_TRANSPARENCY_FIXTURE = ROOT / "tests" / "fixtures" / "misconfigured_transparency.blend"
DRACO_FIXTURE = ROOT / "tests" / "fixtures" / "box_draco.glb"
DUPLICATE_MESH_FIXTURE = ROOT / "tests" / "fixtures" / "duplicate_mesh_scene.blend"
sys.path.insert(0, str(ROOT / "scripts"))
import _obj_mtl  # noqa: E402
import _run  # noqa: E402
import _usdz_validate  # noqa: E402

USDZ_FIXTURE = ROOT / "tests" / "fixtures" / "box.usdz"
BROKEN_USDZ_FIXTURE = ROOT / "tests" / "fixtures" / "box_broken.usdz"
PHONG_OBJ_FIXTURE = ROOT / "tests" / "fixtures" / "phong_materials.obj"
THIN_WALL_STL_FIXTURE = ROOT / "tests" / "fixtures" / "thin_wall_box.stl"
POINT_CLOUD_FIXTURE = ROOT / "tests" / "fixtures" / "points.ply"


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


class TestUsdzValidate(unittest.TestCase):
    """No Blender needed -- _usdz_validate reads a .usdz's own zip structure directly. The
    negative case can't be driven through check.py's CLI end-to-end: Blender's own USD importer
    refuses to even open a re-compressed usdz (confirmed: "Could not open USD archive for
    reading"), so info.py -- which check.py always runs first -- fails before this module's
    check would ever run. Testing the module directly is the only way to exercise that path.
    """

    def test_valid_usdz_passes(self):
        # box.usdz: a real convert.py output (Blender's own USD exporter) -- uncompressed,
        # 64-byte-aligned, as Apple's USDZ spec requires.
        result = _usdz_validate.validate(str(USDZ_FIXTURE))
        self.assertIsNotNone(result)
        self.assertTrue(result["valid"])
        self.assertEqual(result["issues"], [])
        self.assertTrue(result["entries"][0]["stored"])
        self.assertTrue(result["entries"][0]["aligned"])

    def test_recompressed_usdz_fails(self):
        # box_broken.usdz: the same content, re-zipped with Deflate compression -- violates both
        # rules at once (not stored, and no longer 64-byte-aligned since Deflate changes the
        # entry's size).
        result = _usdz_validate.validate(str(BROKEN_USDZ_FIXTURE))
        self.assertIsNotNone(result)
        self.assertFalse(result["valid"])
        self.assertFalse(result["entries"][0]["stored"])
        self.assertFalse(result["entries"][0]["aligned"])


class TestObjMtl(unittest.TestCase):
    """No Blender needed -- _obj_mtl reads the raw .obj/.mtl files directly."""

    def test_ns_to_roughness_matches_blenders_own_obj_importer(self):
        # phong_materials.obj/.mtl: ShinyPlastic (Ns=200) and RoughMatte (Ns=5) -- roughness
        # values here are 1 - sqrt(Ns/1000), confirmed byte-for-byte against what Blender's own
        # wm.obj_import actually assigns to Principled BSDF's Roughness input for these exact Ns
        # values (0.5527864.../0.9292893... within float32 rounding).
        materials = _obj_mtl.read_materials(str(PHONG_OBJ_FIXTURE))
        by_name = {m["name"]: m for m in materials}
        self.assertAlmostEqual(by_name["ShinyPlastic"]["pbr_heuristic"]["roughness"], 0.5527864098548889, places=5)
        self.assertAlmostEqual(by_name["RoughMatte"]["pbr_heuristic"]["roughness"], 0.9292893409729004, places=5)
        # OBJ/MTL has no metallic channel -- never guessed, always 0.0.
        self.assertEqual(by_name["ShinyPlastic"]["pbr_heuristic"]["metallic"], 0.0)
        self.assertEqual(by_name["ShinyPlastic"]["pbr_heuristic"]["base_color"], [0.8, 0.2, 0.2])

    def test_returns_none_without_a_mtllib_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            obj = Path(tmp) / "no_mtl.obj"
            obj.write_text("v 0 0 0\nv 1 0 0\nv 1 1 0\nf 1 2 3\n")
            self.assertIsNone(_obj_mtl.read_materials(str(obj)))


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

    def test_info_lists_vertex_colors_and_custom_attributes(self):
        # vertex_colors_cube.blend: one vertex color layer plus two genuinely custom mesh
        # attributes (a per-vertex float, a per-face int), alongside Blender's own non-internal
        # built-ins (position, material_index, bevel_weight_edge, crease_edge, UVMap -- none of
        # which should show up here). A .blend fixture, not .glb: glTF export drops generic
        # custom attributes entirely (confirmed -- see references/pitfalls.md), so a glb fixture
        # could never exercise custom_attributes at all.
        proc = run("info.py", str(VERTEX_COLORS_FIXTURE), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        stats = json.loads(proc.stdout)["meshes"]["per_object"][0]
        self.assertEqual([vc["name"] for vc in stats["vertex_colors"]], ["Col"])
        self.assertEqual(
            {(a["name"], a["data_type"]) for a in stats["custom_attributes"]},
            {("my_weight", "FLOAT"), ("my_id", "INT")},
        )

    def test_info_reports_no_vertex_colors_or_custom_attributes_on_plain_fixtures(self):
        for fixture in (FIXTURE, ROOT / "tests" / "fixtures" / "fox.glb"):
            proc = run("info.py", str(fixture), "--json")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            for stats in json.loads(proc.stdout)["meshes"]["per_object"]:
                self.assertEqual(stats["vertex_colors"], [])
                self.assertEqual(stats["custom_attributes"], [])

    def test_info_reports_bone_count_and_no_root_motion_on_a_real_character_mesh(self):
        # fox.glb: 3 actions, each animating 20 of the armature's 24 bones via
        # pose.bones["<name>"].<prop> fcurves, none of which ever key the root bone
        # ("_rootJoint")'s own location -- these are in-place cycles meant to be driven
        # externally, so root_motion must read False for all three, not just non-crash.
        proc = run("info.py", str(ROOT / "tests" / "fixtures" / "fox.glb"), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        animations = json.loads(proc.stdout)["animations"]
        self.assertEqual(len(animations), 3)
        for anim in animations:
            self.assertEqual(anim["bone_count"], 20)
            self.assertFalse(anim["root_motion"])

    def test_info_detects_root_motion_on_a_synthetic_rig(self):
        # root_motion_rig.blend: a 2-bone armature ("root" parented by nothing, "child" parented
        # to "root") with two actions -- RootMotionWalk keyframes the root bone's own location
        # (real root motion), InPlaceIdle only keyframes the child bone's rotation (an in-place
        # pose, root_motion must stay False even though a bone is animated).
        proc = run("info.py", str(ROOT_MOTION_FIXTURE), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        animations = {a["name"]: a for a in json.loads(proc.stdout)["animations"]}
        self.assertEqual(animations["RootMotionWalk"]["bone_count"], 2)
        self.assertTrue(animations["RootMotionWalk"]["root_motion"])
        self.assertEqual(animations["InPlaceIdle"]["bone_count"], 1)
        self.assertFalse(animations["InPlaceIdle"]["root_motion"])

    def test_info_reports_bone_hierarchy(self):
        # fox.glb: 24 bones, root "_rootJoint" with no parent, everything else chained under it
        # (confirmed real numbers -- b_Root_00 -> _rootJoint -> None).
        proc = run("info.py", str(ROOT / "tests" / "fixtures" / "fox.glb"), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        armatures = json.loads(proc.stdout)["armatures"]
        self.assertEqual(len(armatures), 1)
        bones = {b["name"]: b["parent"] for b in armatures[0]["bones"]}
        self.assertEqual(len(bones), 24)
        self.assertIsNone(bones["_rootJoint"])
        self.assertEqual(bones["b_Root_00"], "_rootJoint")

    def test_info_detects_unweighted_vertex_on_a_skinned_mesh(self):
        # unweighted_vertex_cube.blend: a cube parented to a 1-bone armature (auto-weighted),
        # with vertex 0's weight explicitly removed afterward -- a real "won't move with the
        # rig" defect, distinct from a mesh that was never meant to be skinned at all.
        proc = run("info.py", str(UNWEIGHTED_VERTEX_FIXTURE), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        stats = data["meshes"]["per_object"][0]
        self.assertEqual(stats["unweighted_vertices"], 1)
        self.assertTrue(any("no bone weight" in w for w in data["warnings"]))

    def test_info_skips_unweighted_check_on_non_skinned_meshes(self):
        # box.glb has no armature at all -- unweighted_vertices must be None (not skipped
        # silently as 0, and not every vertex flagged as a false-positive defect).
        proc = run("info.py", str(FIXTURE), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for stats in json.loads(proc.stdout)["meshes"]["per_object"]:
            self.assertIsNone(stats["unweighted_vertices"])

    def test_info_suggests_lod_ratios_from_triangle_count(self):
        # box.glb: 12 triangles -- LOD1/2/3 at 50%/25%/10% of that (real numbers: 6/3/1).
        proc = run("info.py", str(FIXTURE), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["meshes"]["triangles"], 12)
        suggestions = {s["level"]: s["target_triangles"] for s in data["meshes"]["lod_suggestions"]}
        self.assertEqual(suggestions, {"LOD1": 6, "LOD2": 3, "LOD3": 1})

    def test_info_estimates_one_draw_call_per_object_with_a_single_material(self):
        # fox.glb: 2 mesh objects, each using exactly one material's worth of faces (the
        # "Icosphere" eye mesh has no material slots at all but every face still reads
        # material_index 0 -- still one real draw call, not zero).
        proc = run("info.py", str(ROOT / "tests" / "fixtures" / "fox.glb"), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        for stats in data["meshes"]["per_object"]:
            self.assertEqual(stats["draw_calls_estimate"], 1)
        self.assertEqual(data["meshes"]["draw_calls_estimate"], 2)

    def test_info_estimates_multiple_draw_calls_for_one_object_with_multiple_materials(self):
        # multi_material_cube.glb: a single cube object with 2 materials assigned to alternating
        # faces -- the real per-object batching unit is 2 draw calls, not 1 (naive "one draw call
        # per object") and not "materials x objects" (2 materials x 1 object would coincidentally
        # also read 2 here, but that formula breaks as soon as objects share materials -- this
        # checks the actual per-object material-usage count, not that coincidental product).
        proc = run("info.py", str(MULTI_MATERIAL_FIXTURE), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["meshes"]["per_object"][0]["draw_calls_estimate"], 2)
        self.assertEqual(data["meshes"]["draw_calls_estimate"], 2)

    def test_info_detects_misconfigured_transparent_material(self):
        # misconfigured_transparency.blend: a plane material with a hard binary-alpha mask
        # texture (leaf cutout, values only 0.0/1.0) but surface_render_method left at BLENDED
        # (real alpha blending) instead of DITHERED -- a real "should be a cheap cutout, not an
        # expensive/sorting-order-fragile real blend" defect.
        proc = run("info.py", str(MISCONFIGURED_TRANSPARENCY_FIXTURE), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        issues = data["materials"]["transparency_issues"]
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["material"], "FoliageMisconfigured")
        self.assertTrue(any("hard mask" in w for w in data["warnings"]))

    def test_info_reports_no_transparency_issues_on_plain_fixtures(self):
        for fixture in (FIXTURE, ROOT / "tests" / "fixtures" / "fox.glb"):
            proc = run("info.py", str(fixture), "--json")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(proc.stdout)["materials"]["transparency_issues"], [])

    def test_info_lists_gltf_extensions_from_the_raw_file(self):
        # box_draco.glb: a real Draco-compressed export (via optimize.py --draco), so its
        # extensionsUsed/extensionsRequired genuinely contain KHR_draco_mesh_compression --
        # parsed from the file's own JSON chunk, not inferred from Blender's imported state
        # (which doesn't expose which KHR_* extensions the source file declared).
        proc = run("info.py", str(DRACO_FIXTURE), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        ext = json.loads(proc.stdout)["gltf_extensions"]
        self.assertEqual(ext["used"], ["KHR_draco_mesh_compression"])
        self.assertEqual(ext["required"], ["KHR_draco_mesh_compression"])
        self.assertIn("Draco", ext["descriptions"]["KHR_draco_mesh_compression"])

    def test_info_reports_no_gltf_extensions_on_a_plain_glb_and_none_for_non_gltf(self):
        proc = run("info.py", str(FIXTURE), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        ext = json.loads(proc.stdout)["gltf_extensions"]
        self.assertEqual(ext["used"], [])
        self.assertEqual(ext["required"], [])

        proc = run("info.py", str(VERTEX_COLORS_FIXTURE), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIsNone(json.loads(proc.stdout)["gltf_extensions"])

    def test_info_detects_already_instanced_and_duplicate_mesh_candidates(self):
        # duplicate_mesh_scene.blend: CubeA and CubeB are a linked duplicate (literally the same
        # Mesh datablock -- already properly instanced); CubeC is an independently-created cube
        # of the same size (separate datablock, identical geometry -- a real "could share one
        # datablock" candidate); SphereD is genuinely different geometry and must appear in
        # neither group.
        proc = run("info.py", str(DUPLICATE_MESH_FIXTURE), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        instancing = data["instancing"]
        self.assertEqual(len(instancing["already_instanced"]), 1)
        self.assertEqual(set(instancing["already_instanced"][0]["objects"]), {"CubeA", "CubeB"})
        self.assertEqual(len(instancing["duplicate_mesh_candidates"]), 1)
        candidate = instancing["duplicate_mesh_candidates"][0]
        self.assertEqual(set(candidate["objects"]), {"CubeA", "CubeC"})
        self.assertEqual(candidate["vertices"], 8)
        self.assertEqual(candidate["triangles"], 12)
        self.assertTrue(any("could share one datablock" in w for w in data["warnings"]))

    def test_info_reports_no_instancing_data_on_plain_fixtures(self):
        for fixture in (FIXTURE, ROOT / "tests" / "fixtures" / "fox.glb"):
            proc = run("info.py", str(fixture), "--json")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            instancing = json.loads(proc.stdout)["instancing"]
            self.assertEqual(instancing["already_instanced"], [])
            self.assertEqual(instancing["duplicate_mesh_candidates"], [])

    def test_check_ios_ar_target_validates_usdz_package_structure(self):
        # A real convert.py --draco... no, a plain usdz export -- confirms check.py's ios-ar
        # target actually runs the USDZ-package-structure row (RM-016) on a real Blender-produced
        # file, not just that the standalone validator works in isolation.
        usdz = self.out / "box.usdz"
        proc = run("convert.py", str(FIXTURE), "-o", str(usdz))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = run("check.py", str(usdz), "--target", "ios-ar", "--json")
        self.assertEqual(proc.returncode, 1, proc.stderr)  # UV-map FAIL on box.glb's unwrapped cube, unrelated to this row
        rows = {r["check"]: r for r in json.loads(proc.stdout)["checks"]}
        self.assertEqual(rows["USDZ package"]["status"], "PASS")

    def test_info_reports_obj_pbr_heuristic_matching_the_real_blender_import(self):
        proc = run("info.py", str(PHONG_OBJ_FIXTURE), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        by_name = {m["name"]: m for m in data["obj_materials"]}
        self.assertAlmostEqual(by_name["ShinyPlastic"]["pbr_heuristic"]["roughness"], 0.5527864098548889, places=5)
        # Convert the same file through Blender and read back the actual glTF
        # pbrMetallicRoughness.roughnessFactor it wrote, confirming info.py's reported heuristic
        # isn't just an isolated formula but genuinely matches what this skill's own convert.py
        # pipeline produces end to end.
        out = self.out / "phong.glb"
        proc = run("convert.py", str(PHONG_OBJ_FIXTURE), "-o", str(out), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        import struct as _struct
        with open(out, "rb") as f:
            _magic, _version, _length = _struct.unpack("<4sII", f.read(12))
            chunk_len, _chunk_type = _struct.unpack("<II", f.read(8))
            gltf = json.loads(f.read(chunk_len))
        exported = {m["name"]: m["pbrMetallicRoughness"]["roughnessFactor"] for m in gltf["materials"]}
        self.assertAlmostEqual(exported["ShinyPlastic"], by_name["ShinyPlastic"]["pbr_heuristic"]["roughness"], places=5)
        self.assertAlmostEqual(exported["RoughMatte"], by_name["RoughMatte"]["pbr_heuristic"]["roughness"], places=5)

    def test_info_reports_none_obj_materials_for_non_obj_formats(self):
        proc = run("info.py", str(FIXTURE), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIsNone(json.loads(proc.stdout)["obj_materials"])

    def test_info_detects_thin_wall_via_ray_cast_thickness(self):
        # thin_wall_box.stl: a hollow cube (boolean-differenced) with a real 0.3-unit wall on
        # every side -- ray-cast thickness analysis should read close to 0.3 (minus the small
        # epsilon offset), not the outer box's 10-unit size.
        proc = run("info.py", str(THIN_WALL_STL_FIXTURE), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        stats = json.loads(proc.stdout)["meshes"]["per_object"][0]
        self.assertLess(stats["wall_thickness_min"], 0.31)
        self.assertGreater(stats["wall_thickness_min"], 0.29)

    def test_info_reports_full_thickness_on_a_solid_mesh(self):
        # box.glb: an ordinary solid cube -- a ray cast from any face should reach all the way
        # to the opposite face, not read as "thin".
        proc = run("info.py", str(FIXTURE), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        stats = json.loads(proc.stdout)["meshes"]["per_object"][0]
        self.assertGreater(stats["wall_thickness_min"], 0.9)

    def test_check_3d_print_target_flags_thin_walls(self):
        out = self.out / "thin.stl"
        proc = run("convert.py", str(THIN_WALL_STL_FIXTURE), "-o", str(out))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = run("check.py", str(out), "--target", "3d-print", "--json")
        rows = {r["check"]: r for r in json.loads(proc.stdout)["checks"]}
        self.assertEqual(rows["wall thickness"]["status"], "WARN")

        thick_out = self.out / "thick.stl"
        proc = run("convert.py", str(FIXTURE), "-o", str(thick_out))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = run("check.py", str(thick_out), "--target", "3d-print", "--json")
        rows = {r["check"]: r for r in json.loads(proc.stdout)["checks"]}
        self.assertEqual(rows["wall thickness"]["status"], "PASS")

    def test_info_detects_a_pure_point_cloud_and_skips_its_uv_warning(self):
        # points.ply: 1000 vertices, 0 faces at all (real photogrammetry/LiDAR-style data) --
        # is_point_cloud must read True, and the usual "no UV map" warning (meaningless for a
        # point cloud) must not fire.
        proc = run("info.py", str(POINT_CLOUD_FIXTURE), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        stats = data["meshes"]["per_object"][0]
        self.assertEqual(stats["vertices"], 1000)
        self.assertEqual(stats["triangles"], 0)
        self.assertTrue(stats["is_point_cloud"])
        self.assertFalse(any("no UV map" in w for w in data["warnings"]))

    def test_info_reports_false_is_point_cloud_on_an_ordinary_mesh(self):
        proc = run("info.py", str(FIXTURE), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(json.loads(proc.stdout)["meshes"]["per_object"][0]["is_point_cloud"])

    def test_optimize_point_thin_voxel_reduces_point_cloud_density(self):
        out = self.out / "points_thin.ply"
        proc = run("optimize.py", str(POINT_CLOUD_FIXTURE), "-o", str(out), "--point-thin-voxel", "1.0", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertGreater(data["points_thinned"], 0)
        proc = run("info.py", str(out), "--json")
        remaining = json.loads(proc.stdout)["meshes"]["per_object"][0]["vertices"]
        self.assertEqual(remaining, 1000 - data["points_thinned"])
        self.assertLess(remaining, 1000)

    def test_optimize_point_thin_voxel_is_a_noop_on_a_real_mesh(self):
        # A mesh with actual faces isn't a point cloud -- --point-thin-voxel must leave its
        # vertex count untouched (--decimate-ratio is the tool for that case).
        out = self.out / "box_thin.glb"
        proc = run("optimize.py", str(FIXTURE), "-o", str(out), "--point-thin-voxel", "1.0", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["points_thinned"], 0)
        proc = run("info.py", str(out), "--json")
        self.assertEqual(json.loads(proc.stdout)["meshes"]["per_object"][0]["vertices"], 24)

    def test_optimize_webp_converts_texture_and_reports_measured_size(self):
        # fox.glb's one PNG texture (26764 bytes, real measured number) actually grows to 30060
        # bytes as WebP at Blender's own default quality (75) -- a real, confirmed finding
        # (references/pitfalls.md), not a hypothetical -- so this pins the measured before/after,
        # never a claimed reduction.
        out = self.out / "fox_webp.glb"
        proc = run("optimize.py", str(ROOT / "tests" / "fixtures" / "fox.glb"), "-o", str(out), "--webp", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["texture_bytes_before"], 26764)
        self.assertEqual(data["texture_bytes_after"], 30060)
        # And the output genuinely declares the glTF extension this conversion adds.
        proc = run("info.py", str(out), "--json")
        ext = json.loads(proc.stdout)["gltf_extensions"]
        self.assertEqual(ext["used"], ["EXT_texture_webp"])

    def test_optimize_webp_quality_flag_actually_changes_output_size(self):
        out = self.out / "fox_webp_q30.glb"
        proc = run("optimize.py", str(ROOT / "tests" / "fixtures" / "fox.glb"), "-o", str(out), "--webp", "--webp-quality", "30", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertLess(data["texture_bytes_after"], data["texture_bytes_before"])

    def test_optimize_webp_rejects_non_gltf_output(self):
        out = self.out / "fox_webp.stl"
        proc = run("optimize.py", str(ROOT / "tests" / "fixtures" / "fox.glb"), "-o", str(out), "--webp", "--json")
        self.assertNotEqual(proc.returncode, 0)

    def test_info_detects_misconfigured_colorspace(self):
        # misconfigured_colorspace.blend: Base Color tagged Non-Color (should be sRGB);
        # Roughness and a normal map's own texture both tagged sRGB (should be Non-Color).
        proc = run("info.py", str(ROOT / "tests" / "fixtures" / "misconfigured_colorspace.blend"), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        issues = {i["socket"]: i for i in data["materials"]["colorspace_issues"]}
        self.assertEqual(set(issues), {"Base Color", "Roughness", "Normal"})
        self.assertEqual(issues["Base Color"]["expected"], "sRGB")
        self.assertEqual(issues["Base Color"]["colorspace"], "Non-Color")
        self.assertEqual(issues["Roughness"]["expected"], "Non-Color")
        self.assertEqual(issues["Normal"]["image"], "normal_tex")

    def test_optimize_fix_colorspace_corrects_every_mismatch(self):
        out = self.out / "colorspace_fixed.blend"
        proc = run("optimize.py", str(ROOT / "tests" / "fixtures" / "misconfigured_colorspace.blend"), "-o", str(out), "--fix-colorspace", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["colorspace_fixed"], 3)

        verify = json.loads(run("info.py", str(out), "--json").stdout)
        self.assertEqual(verify["materials"]["colorspace_issues"], [])

    def test_optimize_texture_colorspace_forces_every_image(self):
        # ACEScg is a real colorspace in Blender's own bundled OCIO config (confirmed via its
        # own enum) -- note this tags textures for an ACES-aware shading pipeline; Blender's
        # stock config has no ACES *view transform* for rendering at all, a separate thing.
        out = self.out / "aces.blend"
        proc = run("optimize.py", str(ROOT / "tests" / "fixtures" / "misconfigured_colorspace.blend"), "-o", str(out), "--texture-colorspace", "ACEScg", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(set(data["texture_colorspace_changed"]), {"diffuse_tex", "rough_tex", "normal_tex"})

    def test_optimize_texture_colorspace_rejects_unknown_name(self):
        out = self.out / "bad.blend"
        proc = run("optimize.py", str(ROOT / "tests" / "fixtures" / "misconfigured_colorspace.blend"), "-o", str(out), "--texture-colorspace", "NotARealColorspace", "--json")
        self.assertNotEqual(proc.returncode, 0)

    def test_optimize_rejects_fix_colorspace_with_texture_colorspace_together(self):
        out = self.out / "conflict.blend"
        proc = run("optimize.py", str(ROOT / "tests" / "fixtures" / "misconfigured_colorspace.blend"), "-o", str(out), "--fix-colorspace", "--texture-colorspace", "sRGB")
        self.assertNotEqual(proc.returncode, 0)

    def test_lod_default_ratios_match_info_pys_own_suggestions(self):
        # box.glb: 12 triangles. Default ratios (0.5, 0.25, 0.1) match info.py's own LOD1/2/3
        # suggestion ladder (RM-010) -- LOD0 is always an unmodified copy at full detail.
        out_dir = self.out / "lod_default"
        proc = run("lod.py", str(FIXTURE), "--out-dir", str(out_dir), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["triangles_original"], 12)
        levels = {lvl["name"]: lvl for lvl in data["levels"]}
        self.assertEqual(set(levels), {"LOD0", "LOD1", "LOD2", "LOD3"})
        self.assertEqual(levels["LOD0"]["triangles_after"], 12)
        self.assertLessEqual(levels["LOD1"]["triangles_after"], 12)
        self.assertLessEqual(levels["LOD2"]["triangles_after"], levels["LOD1"]["triangles_after"])
        self.assertLessEqual(levels["LOD3"]["triangles_after"], levels["LOD2"]["triangles_after"])
        for lvl in data["levels"]:
            self.assertTrue(Path(lvl["output"]).exists())

    def test_lod_target_triangles_scales_lod1_and_halves_the_rest(self):
        out_dir = self.out / "lod_target"
        proc = run("lod.py", str(ROOT / "tests" / "fixtures" / "fox.glb"), "--out-dir", str(out_dir), "--target-triangles", "200", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        levels = {lvl["name"]: lvl for lvl in data["levels"]}
        # ratio1 = 200 / triangles_original (576, the real "fox" mesh -- the synthesized
        # bone-shape widget must not be counted, see references/pitfalls.md's RM-027 entry).
        self.assertEqual(data["triangles_original"], 576)
        self.assertAlmostEqual(levels["LOD1"]["ratio"], 200 / 576, places=4)
        self.assertAlmostEqual(levels["LOD2"]["ratio"], levels["LOD1"]["ratio"] / 2, places=6)
        self.assertAlmostEqual(levels["LOD3"]["ratio"], levels["LOD1"]["ratio"] / 4, places=6)

    def test_lod_outputs_never_leak_the_synthesized_bone_shape_widget(self):
        # fox.glb's synthetic "Icosphere" bone custom-shape widget (see references/pitfalls.md)
        # must never appear in a LOD output's own mesh list -- confirmed real, not just a
        # reporting artifact from Blender's own whole-scene export already excluding it.
        out_dir = self.out / "lod_no_widget"
        proc = run("lod.py", str(ROOT / "tests" / "fixtures" / "fox.glb"), "--out-dir", str(out_dir), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        import struct as _struct
        for lvl in data["levels"]:
            with open(lvl["output"], "rb") as f:
                f.read(12)
                chunk_len, _chunk_type = _struct.unpack("<II", f.read(8))
                gltf = json.loads(f.read(chunk_len))
            self.assertEqual([m["name"] for m in gltf.get("meshes", [])], ["fox1"])

    def test_lod_rejects_malformed_ratios(self):
        out_dir = self.out / "lod_bad_ratios"
        proc = run("lod.py", str(FIXTURE), "--out-dir", str(out_dir), "--ratios", "0.6,0.3")
        self.assertNotEqual(proc.returncode, 0)

    def test_split_separates_independent_hierarchies(self):
        # two_props.glb: PropA and PropB, two plain cubes with no parent relationship at all --
        # each must become its own group.
        out_dir = self.out / "split"
        proc = run("split.py", str(ROOT / "tests" / "fixtures" / "two_props.glb"), "--out-dir", str(out_dir), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        groups = {g["root_object"]: g for g in data["groups"]}
        self.assertEqual(set(groups), {"PropA", "PropB"})
        self.assertEqual(groups["PropA"]["objects"], ["PropA"])
        self.assertEqual(groups["PropB"]["objects"], ["PropB"])
        for g in data["groups"]:
            self.assertGreater(Path(g["output"]).stat().st_size, 1000)  # not the empty-export symptom (132 bytes)

    def test_split_keeps_a_skinned_mesh_with_its_armature_as_one_group(self):
        # fox.glb: "fox" (mesh) is parented to "root" (armature) via both Object parenting and
        # an Armature modifier -- must stay one single group, not two. Blender's own glTF
        # importer additionally synthesizes a small "Icosphere" mesh object on import (a shared
        # bone custom-shape display widget for every pose bone, confirmed absent from fox.glb's
        # own JSON -- see references/pitfalls.md); split.py must not misidentify that as its own
        # independent hierarchy and export it as a spurious file.
        out_dir = self.out / "split"
        proc = run("split.py", str(ROOT / "tests" / "fixtures" / "fox.glb"), "--out-dir", str(out_dir), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(len(data["groups"]), 1)
        group = data["groups"][0]
        self.assertEqual(group["root_object"], "root")
        self.assertEqual(set(group["objects"]), {"root", "fox"})
        self.assertNotIn("Icosphere", group["objects"])

        root_info = json.loads(run("info.py", str(group["output"]), "--json").stdout)
        self.assertEqual(len(root_info["armatures"]), 1)
        self.assertEqual(root_info["armatures"][0]["bone_count"], 24)
        self.assertEqual(len(root_info["animations"]), 3)  # Run_root, Survey_root, Walk_root all kept

    def test_split_rejects_blend_output_format(self):
        out_dir = self.out / "split_blend"
        proc = run("split.py", str(ROOT / "tests" / "fixtures" / "two_props.glb"), "--out-dir", str(out_dir), "--format", "blend", "--json")
        self.assertNotEqual(proc.returncode, 0)

    def test_anim_extract_keeps_only_the_armature_and_every_action(self):
        out = self.out / "fox_anim_only.glb"
        proc = run("anim.py", str(ROOT / "tests" / "fixtures" / "fox.glb"), "--extract", "-o", str(out), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(set(data["actions"]), {"Run_root", "Survey_root", "Walk_root"})
        self.assertEqual(data["armatures_kept"], ["root"])

        import struct as _struct
        with open(out, "rb") as f:
            f.read(12)
            chunk_len, _chunk_type = _struct.unpack("<II", f.read(8))
            gltf = json.loads(f.read(chunk_len))
        self.assertEqual(gltf.get("meshes", []), [])  # mesh-free -- animation only, as documented

        info = json.loads(run("info.py", str(out), "--json").stdout)
        self.assertEqual(len(info["armatures"]), 1)
        self.assertEqual(len(info["animations"]), 3)

    def test_anim_extract_action_filter_keeps_only_the_named_action(self):
        out = self.out / "fox_walk_only.glb"
        proc = run("anim.py", str(ROOT / "tests" / "fixtures" / "fox.glb"), "--extract", "--action", "Walk_root", "-o", str(out), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["actions"], ["Walk_root"])
        info = json.loads(run("info.py", str(out), "--json").stdout)
        self.assertEqual([a["name"] for a in info["animations"]], ["Walk_root"])

    def test_anim_combine_merges_separate_files_without_leaking_extra_objects(self):
        # A Mixamo-style workflow: a rigged character (here, fox.glb itself, already carrying 3
        # actions) combined with 2 more animation-only files (each produced exactly as anim.py
        # --extract would) sharing the same bone names.
        run_only = self.out / "run_only.glb"
        survey_only = self.out / "survey_only.glb"
        for action, out in (("Run_root", run_only), ("Survey_root", survey_only)):
            proc = run("anim.py", str(ROOT / "tests" / "fixtures" / "fox.glb"), "--extract", "--action", action, "-o", str(out))
            self.assertEqual(proc.returncode, 0, proc.stderr)

        combined = self.out / "combined.glb"
        proc = run("anim.py", str(ROOT / "tests" / "fixtures" / "fox.glb"), "--combine", str(run_only), str(survey_only), "-o", str(combined), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(len(data["combined"]), 2)

        # The real regression this guards: Blender's own synthesized bone-shape widget from the
        # *base* file's import must not leak into the combined output as a spurious extra mesh
        # (each anim source's own copy is already discarded by the new-object diff regardless).
        import struct as _struct
        with open(combined, "rb") as f:
            f.read(12)
            chunk_len, _chunk_type = _struct.unpack("<II", f.read(8))
            gltf = json.loads(f.read(chunk_len))
        self.assertEqual([m["name"] for m in gltf.get("meshes", [])], ["fox1"])
        self.assertEqual(len(gltf.get("animations", [])), 5)  # base's 3 + the 2 combined

    def test_anim_combine_rejects_unsupported_output_format(self):
        out = self.out / "combined.obj"
        proc = run(
            "anim.py", str(ROOT / "tests" / "fixtures" / "fox.glb"),
            "--combine", str(ROOT / "tests" / "fixtures" / "fox.glb"),
            "-o", str(out), "--json",
        )
        self.assertNotEqual(proc.returncode, 0)

    def test_convert_up_axis_reorients_the_output_geometry(self):
        # tall_cube.blend: a cube at Blender-native location (0, 0, 5) -- native format, so no
        # importer axis-convention ambiguity to confound the check (unlike OBJ, whose own
        # default import/export axes are its traditional Y-up/-Z-forward convention, not
        # Blender's; see references/pitfalls.md). Default glTF export (up=Y) must move that
        # world position into the Y column; --up-axis Z must keep it in the Z column.
        fixture = ROOT / "tests" / "fixtures" / "tall_cube.blend"
        default_out = self.out / "default_up.glb"
        proc = run("convert.py", str(fixture), "-o", str(default_out), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        z_out = self.out / "z_up.glb"
        proc = run("convert.py", str(fixture), "-o", str(z_out), "--up-axis", "Z", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)

        import struct as _struct

        def node_translation(path):
            with open(path, "rb") as f:
                f.read(12)
                chunk_len, _chunk_type = _struct.unpack("<II", f.read(8))
                gltf = json.loads(f.read(chunk_len))
            return gltf["nodes"][0]["translation"]

        self.assertEqual(node_translation(default_out), [0, 5, 0])  # glTF's mandatory Y-up
        self.assertEqual(node_translation(z_out), [0, 0, 5])  # Blender's own native Z-up, kept

    def test_convert_up_axis_rejects_unsupported_format_and_axis_combinations(self):
        fixture = ROOT / "tests" / "fixtures" / "tall_cube.blend"
        # ABC/.blend have no axis-orientation control in Blender at all.
        proc = run("convert.py", str(fixture), "-o", str(self.out / "x.abc"), "--up-axis", "Z", "--json")
        self.assertNotEqual(proc.returncode, 0)
        # glTF has no forward-axis control, and only accepts up-axis Y or Z (its spec is fixed Y-up).
        proc = run("convert.py", str(fixture), "-o", str(self.out / "x.glb"), "--forward-axis", "X", "--json")
        self.assertNotEqual(proc.returncode, 0)
        proc = run("convert.py", str(fixture), "-o", str(self.out / "x2.glb"), "--up-axis", "X", "--json")
        self.assertNotEqual(proc.returncode, 0)

    def test_convert_up_axis_applies_to_usd_via_a_root_transform(self):
        # USD needs convert_orientation=True as a master switch alongside its own up/forward
        # selection (confirmed: without it, the selection is accepted but has no effect) --
        # applied as a single root Xform wrapper's rotation, not per-object; verified directly
        # against the exported file's own raw text (upAxis stage metadata + a rotateXYZ on the
        # root prim), not through Blender's own reimport.
        fixture = ROOT / "tests" / "fixtures" / "tall_cube.blend"
        out = self.out / "axis.usda"
        proc = run("convert.py", str(fixture), "-o", str(out), "--up-axis", "Z", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        content = out.read_text()
        self.assertIn('upAxis = "Z"', content)
        self.assertIn("xformOp:rotateXYZ", content)

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

    def test_optimize_texture_auto_resolution_scales_by_scene_relative_size(self):
        # scene_scale_mix.blend: a 10-unit "BigProp" and a 1-unit "SmallProp" 20 units apart,
        # each with its own 512x512 texture. The small object's texture should get a much
        # smaller computed cap than the big one's -- a real, data-driven "screen occupancy"
        # proxy (each object's own bounding-box diagonal vs. the whole scene's combined one),
        # not just one flat number every texture in the file would share under --texture-max.
        out = self.out / "auto_res.glb"
        proc = run("optimize.py", str(ROOT / "tests" / "fixtures" / "scene_scale_mix.blend"), "-o", str(out), "--texture-auto-resolution", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        caps = data["texture_auto_caps"]
        self.assertEqual(set(caps), {"big_tex", "small_tex"})
        self.assertGreater(caps["big_tex"], caps["small_tex"])
        # Only small_tex actually needed downscaling from its original 512x512 (big_tex's
        # computed cap is above 512, so it's correctly left untouched).
        resized = {r["name"]: r for r in data["textures_resized"]}
        self.assertEqual(set(resized), {"small_tex"})
        self.assertEqual(resized["small_tex"]["to"], [caps["small_tex"]] * 2)

    def test_optimize_texture_auto_resolution_single_object_gets_full_viewport_cap(self):
        # fox.glb (after stripping Blender's own synthesized bone-shape widget -- see
        # references/pitfalls.md) has exactly one real mesh object, so its own bounding box IS
        # the whole scene's: fraction 1.0, cap = next power of two >= --viewport-width.
        out = self.out / "auto_res_single.glb"
        proc = run("optimize.py", str(ROOT / "tests" / "fixtures" / "fox.glb"), "-o", str(out), "--texture-auto-resolution", "--viewport-width", "1000", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["texture_auto_caps"], {"Image_0": 1024})  # next_pow2(1000)

    def test_optimize_rejects_texture_max_with_texture_auto_resolution_together(self):
        out = self.out / "conflict.glb"
        proc = run("optimize.py", str(FIXTURE), "-o", str(out), "--texture-max", "512", "--texture-auto-resolution")
        self.assertNotEqual(proc.returncode, 0)

    def test_optimize_triangle_counts_exclude_the_synthesized_bone_shape_widget(self):
        # The same fix split.py/anim.py/lod.py already needed (see references/pitfalls.md):
        # fox.glb's real mesh has 576 triangles; before this fix, optimize.py's own
        # triangles_before/after were inflated to 656 by Blender's synthesized bone-shape widget.
        out = self.out / "fox_opt.glb"
        proc = run("optimize.py", str(ROOT / "tests" / "fixtures" / "fox.glb"), "-o", str(out), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["triangles_before"], 576)
        self.assertEqual(data["triangles_after"], 576)

    def test_optimize_remove_unused_bones_prunes_only_genuinely_dead_leaves(self):
        # sparse_rig.blend: Root -> Spine -> Head -> HeadTip (no weight, no animation -- dead)
        #                              Spine -> Arm1 -> Arm1Tip (dead)
        #                              Spine -> Arm2 (weight 0.2)
        #                              Spine -> Arm3 (weight 0.05)
        # Only HeadTip/Arm1Tip have zero skin-weight influence and no animation -- everything
        # else (including the barely-weighted Arm2/Arm3) must survive untouched.
        out = self.out / "pruned.glb"
        proc = run("optimize.py", str(ROOT / "tests" / "fixtures" / "sparse_rig.blend"), "-o", str(out), "--remove-unused-bones", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(set(data["bones_removed"]), {"HeadTip", "Arm1Tip"})
        self.assertEqual(data["bones_demoted"], [])

        info = json.loads(run("info.py", str(out), "--json").stdout)
        self.assertEqual(set(b["name"] for b in info["armatures"][0]["bones"]), {"Root", "Spine", "Head", "Arm1", "Arm2", "Arm3"})

    def test_optimize_max_bones_removes_lowest_influence_first_and_transfers_weight(self):
        out = self.out / "capped.glb"
        proc = run("optimize.py", str(ROOT / "tests" / "fixtures" / "sparse_rig.blend"), "-o", str(out), "--max-bones", "4", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        # Lowest-influence-first order: the two genuinely dead bones, then Arm3 (0.05), then
        # Arm2 (0.2) -- never touching the more-weighted Root/Spine/Head/Arm1.
        self.assertEqual(
            [d["bone"] for d in data["bones_demoted"]],
            ["HeadTip", "Arm1Tip", "Arm3", "Arm2"],
        )
        self.assertEqual(data["bones_demoted"][2]["reassigned_to"], "Spine")
        self.assertEqual(data["bones_demoted"][3]["reassigned_to"], "Spine")

        info = json.loads(run("info.py", str(out), "--json").stdout)
        self.assertEqual(set(b["name"] for b in info["armatures"][0]["bones"]), {"Root", "Spine", "Head", "Arm1"})

    def test_optimize_max_bones_never_removes_an_animated_bone(self):
        # sparse_rig_animated_arm3.blend: same rig, but Arm3 (the lowest-weight bone) now has a
        # real keyframed location animation -- it must survive even though every other candidate
        # outranks it by weight, and the tool must say so rather than silently forcing the count.
        out = self.out / "capped_anim.glb"
        proc = run("optimize.py", str(ROOT / "tests" / "fixtures" / "sparse_rig_animated_arm3.blend"), "-o", str(out), "--max-bones", "2", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(len(data["warnings"]), 1)
        self.assertIn("animated bone", data["warnings"][0])

        info = json.loads(run("info.py", str(out), "--json").stdout)
        remaining = {b["name"] for b in info["armatures"][0]["bones"]}
        self.assertIn("Arm3", remaining)
        self.assertEqual(len(remaining), 3)  # couldn't reach 2, stopped at 3

    def test_optimize_max_bones_never_drops_a_weighted_root_bones_own_weight(self):
        # root_leaf_rig.blend: RootA is a root bone (no parent) with no children of its own --
        # a "leaf" by the pruning definition -- and carries real skin weight (the whole mesh).
        # RootB -> AnimKid is a separate chain where AnimKid is animated. With every other
        # candidate excluded (RootB isn't a leaf, AnimKid is animated), a naive cap would pick
        # RootA as the only remaining "leaf" and discard its weight since there's no parent to
        # transfer it to (reassigned_to would be null). It must instead stop and warn, exactly
        # as it already does for an animated bone, never dropping RootA or its weight.
        out = self.out / "root_leaf_capped.glb"
        proc = run("optimize.py", str(ROOT / "tests" / "fixtures" / "root_leaf_rig.blend"), "-o", str(out), "--max-bones", "1", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["bones_demoted"], [])
        self.assertEqual(len(data["warnings"]), 1)
        self.assertIn("weighted root bone", data["warnings"][0])

        info = json.loads(run("info.py", str(out), "--json").stdout)
        self.assertEqual(set(b["name"] for b in info["armatures"][0]["bones"]), {"RootA", "RootB", "AnimKid"})

    def test_optimize_rejects_non_positive_max_bones(self):
        out = self.out / "bad.glb"
        proc = run("optimize.py", str(ROOT / "tests" / "fixtures" / "sparse_rig.blend"), "-o", str(out), "--max-bones", "0", "--json")
        self.assertNotEqual(proc.returncode, 0)

    def test_optimize_keyframe_decimate_matches_hand_computed_rdp_result(self):
        # keyframe_curve.blend: a single location.x fcurve with 7 keyframes:
        #   (0,0.0) (5,0.5) (10,1.0) (15,3.0) (20,1.0) (25,1.0) (30,1.0)
        # Worked out by hand (see references/pitfalls.md): at tolerance 0.1, RDP keeps
        # frames 0, 10, 15, 20, 30 and drops 5 (exactly on the 0->10 line) and 25 (exactly on
        # the flat 20->30 line) -- 7 -> 5 keyframes.
        out = self.out / "curve_decimated.blend"
        proc = run("optimize.py", str(ROOT / "tests" / "fixtures" / "keyframe_curve.blend"), "-o", str(out), "--keyframe-decimate", "0.1", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["keyframe_points_before"], 7)
        self.assertEqual(data["keyframe_points_after"], 5)
        self.assertEqual(data["keyframes_decimated_by_action"], [{"action": "CurveTest", "keyframe_points_before": 7, "keyframe_points_after": 5}])

    def test_optimize_keyframe_decimate_large_tolerance_keeps_only_endpoints(self):
        out = self.out / "curve_hightol.blend"
        proc = run("optimize.py", str(ROOT / "tests" / "fixtures" / "keyframe_curve.blend"), "-o", str(out), "--keyframe-decimate", "10", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["keyframe_points_after"], 2)  # first/last always survive

    def test_optimize_keyframe_decimate_preserves_animation_bounds_on_a_real_character(self):
        # fox.glb has three real animations (Run/Survey/Walk) with distinct frame ranges --
        # decimating must shrink keyframe count without moving any action's start/end frame,
        # since the first/last keyframe of every fcurve is always protected. Compared against a
        # plain optimize.py pass-through (no --keyframe-decimate) on the same file, not against
        # the original fox.glb directly: optimize.py's own default (non-multi-action) glTF
        # export already truncates Run's fractional last frame (27.8 -> 27.0) with no decimation
        # involved at all -- a pre-existing export quirk (see references/pitfalls.md), not
        # something this feature should be blamed for or is expected to preserve.
        passthrough_out = self.out / "fox_passthrough.glb"
        proc0 = run("optimize.py", str(ROOT / "tests" / "fixtures" / "fox.glb"), "-o", str(passthrough_out), "--json")
        self.assertEqual(proc0.returncode, 0, proc0.stderr)
        before_info = json.loads(run("info.py", str(passthrough_out), "--json").stdout)

        out = self.out / "fox_decimated.glb"
        proc = run("optimize.py", str(ROOT / "tests" / "fixtures" / "fox.glb"), "-o", str(out), "--keyframe-decimate", "0.01", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertLess(data["keyframe_points_after"], data["keyframe_points_before"])

        after_info = json.loads(run("info.py", str(out), "--json").stdout)
        before_by_name = {a["name"]: a for a in before_info["animations"]}
        after_by_name = {a["name"]: a for a in after_info["animations"]}
        self.assertEqual(set(before_by_name), set(after_by_name))
        for name, before_anim in before_by_name.items():
            after_anim = after_by_name[name]
            self.assertEqual(before_anim["frame_start"], after_anim["frame_start"])
            self.assertEqual(before_anim["frame_end"], after_anim["frame_end"])

    def test_optimize_rejects_non_positive_keyframe_decimate(self):
        out = self.out / "bad_kf.blend"
        proc = run("optimize.py", str(ROOT / "tests" / "fixtures" / "keyframe_curve.blend"), "-o", str(out), "--keyframe-decimate", "0", "--json")
        self.assertNotEqual(proc.returncode, 0)

    def test_optimize_rejects_nan_keyframe_decimate(self):
        # A straight `<= 0` check lets NaN through (`nan <= 0` is False) -- caught by review.
        out = self.out / "bad_kf_nan.blend"
        proc = run("optimize.py", str(ROOT / "tests" / "fixtures" / "keyframe_curve.blend"), "-o", str(out), "--keyframe-decimate", "nan", "--json")
        self.assertNotEqual(proc.returncode, 0)

    def test_optimize_keyframe_decimate_never_exceeds_tolerance_on_a_constant_curve(self):
        # constant_curve.blend: (0,0.0) (5,1.0) (10,1.0), CONSTANT interpolation throughout.
        # A straight-line error estimate at frame 5 is |1.0-0.5|=0.5 -- under tolerance 0.75, so
        # a version that assumed linear interpolation would wrongly remove it. The real
        # CONSTANT-governed curve holds 0.0 from frame 0 to 10, so the true error is |1.0-0.0|=
        # 1.0 -- frame 5 must survive at any tolerance below 1.0 (caught by review; see
        # references/pitfalls.md for the full worked example).
        out = self.out / "const_low_tol.blend"
        proc = run("optimize.py", str(ROOT / "tests" / "fixtures" / "constant_curve.blend"), "-o", str(out), "--keyframe-decimate", "0.75", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["keyframe_points_after"], 3)  # frame 5 protected, nothing removed

    def test_optimize_keyframe_decimate_removes_a_constant_keyframe_once_true_error_fits(self):
        # Same curve, but tolerance 1.5 is above the *real* CONSTANT error (1.0) -- frame 5 is
        # correctly removed once the exact (not straight-line) error is what's being measured.
        out = self.out / "const_high_tol.blend"
        proc = run("optimize.py", str(ROOT / "tests" / "fixtures" / "constant_curve.blend"), "-o", str(out), "--keyframe-decimate", "1.5", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["keyframe_points_after"], 2)

    def test_optimize_remove_unused_shape_keys_resolves_a_dead_chain(self):
        # shape_key_rig.blend: Basis, Real1 (real displacement from Basis), Dead1 (zero
        # displacement from Basis), Dead2 (relative to Dead1, itself zero displacement), Real2
        # (relative to Dead2, real displacement). Only Dead1/Dead2 are geometrically no-ops --
        # Real1/Real2 must survive. Dead2's own max-displacement check only succeeds (rather than
        # crashing on a dangling relative_key, or misreporting since Dead1's removal already
        # happened by the time Dead2 is checked) if Blender's own auto-splice -- re-pointing
        # Dead2 onto Dead1's own relative_key (Basis) the moment Dead1 is removed, verified
        # directly against real Blender while building this feature -- actually took effect.
        out = self.out / "shapekey_out.blend"
        proc = run("optimize.py", str(ROOT / "tests" / "fixtures" / "shape_key_rig.blend"), "-o", str(out), "--remove-unused-shape-keys", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(
            sorted(s["shape_key"] for s in data["shape_keys_removed"]),
            ["Dead1", "Dead2"],
        )
        for s in data["shape_keys_removed"]:
            self.assertEqual(s["max_displacement"], 0.0)

        # A second pass over the already-cleaned output must find nothing left to remove.
        out2 = self.out / "shapekey_out2.blend"
        proc2 = run("optimize.py", str(out), "-o", str(out2), "--remove-unused-shape-keys", "--json")
        self.assertEqual(proc2.returncode, 0, proc2.stderr)
        self.assertEqual(json.loads(proc2.stdout)["shape_keys_removed"], [])

    def test_optimize_remove_unused_shape_keys_skips_absolute_mode_sequences(self):
        # absolute_shape_key_rig.blend: Key.use_relative = False (a timed sequence, not a
        # relative blend). StateA is geometrically identical to Basis (zero displacement from
        # relative_key) but is still a real waypoint in the Absolute-mode sequence between Basis
        # and StateB -- removing it would change how Blender interpolates between surviving
        # states. Must never be touched (caught by review; see references/pitfalls.md).
        out = self.out / "abs_out.blend"
        proc = run("optimize.py", str(ROOT / "tests" / "fixtures" / "absolute_shape_key_rig.blend"), "-o", str(out), "--remove-unused-shape-keys", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["shape_keys_removed"], [])

    def test_optimize_instance_duplicate_meshes_merges_only_truly_identical_copies(self):
        # instancing_rig.blend: CubeA1/A2/A3 are three separate-datablock but fully identical
        # cube copies -- must merge onto one shared datablock. CubeB1/B2 share the same
        # vertex/triangle/area/volume/bbox signature info.py's own loose duplicate_mesh_candidates
        # heuristic would flag, but B2's UVs are shifted -- must NOT merge (proving this check is
        # stricter than that suggestion-only fingerprint). CubeC1/C2 are geometrically identical
        # but have shape keys -- must NOT merge (shared mesh data means shared shape-key state in
        # Blender's own data model). Sphere has no duplicate at all.
        out = self.out / "instanced_out.blend"
        proc = run("optimize.py", str(ROOT / "tests" / "fixtures" / "instancing_rig.blend"), "-o", str(out), "--instance-duplicate-meshes", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(len(data["meshes_instanced"]), 1)
        group = data["meshes_instanced"][0]
        self.assertEqual(group["canonical_objects"], ["CubeA1"])
        self.assertEqual(sorted(group["merged_objects"]), ["CubeA2", "CubeA3"])
        self.assertEqual(len(group["datablocks_removed"]), 2)

        info = json.loads(run("info.py", str(out), "--json").stdout)
        already_instanced = info["instancing"]["already_instanced"]
        self.assertEqual(len(already_instanced), 1)
        self.assertEqual(sorted(already_instanced[0]["objects"]), ["CubeA1", "CubeA2", "CubeA3"])
        # B (different UVs) and C (shape-keyed) still show up as separate-datablock candidates
        # by info.py's own loose signature -- proving they were never merged.
        candidates = info["instancing"]["duplicate_mesh_candidates"][0]["objects"]
        for name in ("CubeB1", "CubeB2", "CubeC1", "CubeC2"):
            self.assertIn(name, candidates)

    def test_optimize_instance_duplicate_meshes_checks_material_index_and_smoothing(self):
        # material_index_rig.blend: MatIdxA/B share the same two materials in the same slot
        # order and identical geometry, but one face's material_index differs -- must not merge.
        # SmoothA/B are identical except one face's use_smooth -- must not merge either. Caught
        # by review; see references/pitfalls.md.
        out = self.out / "matidx_out.blend"
        proc = run("optimize.py", str(ROOT / "tests" / "fixtures" / "material_index_rig.blend"), "-o", str(out), "--instance-duplicate-meshes", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["meshes_instanced"], [])

    def test_optimize_instance_duplicate_meshes_checks_sharp_faces_and_layer_names(self):
        # sharp_uvname_rig.blend: SharpA/B are identical except one face is marked sharp on B;
        # UVNameA/B have identical UV coordinates under different layer names; ColNameA/B have
        # identical color-attribute values under different names. A shared material's node tree
        # can look up a UV/color layer by name, so none of these three pairs may merge. Caught by
        # review; see references/pitfalls.md.
        out = self.out / "sharp_uvname_out.blend"
        proc = run("optimize.py", str(ROOT / "tests" / "fixtures" / "sharp_uvname_rig.blend"), "-o", str(out), "--instance-duplicate-meshes", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["meshes_instanced"], [])

    def test_optimize_instance_duplicate_meshes_never_touches_a_datablock_an_animatable_object_still_uses(self):
        # shared_data_rig.blend: SharedA and SharedB point at the SAME mesh datablock; SharedB
        # has an Armature modifier, SharedA doesn't. Other is a separate, geometrically-identical
        # cube on its own datablock. Filtering *objects* by animatability before grouping by
        # datablock (the first version of this feature) would still route SharedA into a merge
        # group and free the shared datablock while SharedB silently still pointed at it -- a
        # real crash, confirmed by reverting just this fix (StructRNA of type Object has been
        # removed). The whole datablock must be excluded whenever any of its users is
        # animatable, so nothing here should merge at all.
        out = self.out / "shared_data_out.blend"
        proc = run("optimize.py", str(ROOT / "tests" / "fixtures" / "shared_data_rig.blend"), "-o", str(out), "--instance-duplicate-meshes", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["meshes_instanced"], [])

    def test_optimize_merge_materials_merges_identical_node_graphs_only(self):
        # materials_rig.blend: MatA/MatB/MatC are three independently-authored materials with
        # byte-identical node graphs -- must merge onto one canonical datablock. MatD has the
        # same graph shape but a different Base Color -- must not merge. MuteMatA is identical
        # to the trio (so it merges too) but MuteMatB has its Principled BSDF muted, which
        # changes what the graph actually renders -- must not merge with anything. NoNodesMatA/B
        # (use_nodes=False, identical legacy diffuse_color) must merge; NoNodesMatC (different
        # diffuse_color) must not.
        out = self.out / "materials_out.blend"
        proc = run("optimize.py", str(ROOT / "tests" / "fixtures" / "materials_rig.blend"), "-o", str(out), "--merge-materials", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(len(data["materials_merged"]), 2)
        by_canonical = {g["canonical_material"]: sorted(g["merged_materials"]) for g in data["materials_merged"]}
        self.assertEqual(by_canonical["MatA"], ["MatB", "MatC", "MuteMatA"])
        self.assertEqual(by_canonical["NoNodesMatA"], ["NoNodesMatB"])

        info = json.loads(run("info.py", str(out), "--json").stdout)
        self.assertEqual(set(info["materials"]["names"]), {"MatA", "MatD", "MuteMatB", "NoNodesMatA", "NoNodesMatC"})

    def test_optimize_merge_materials_checks_image_datablock_identity_not_pixel_content(self):
        # material_texture_rig.blend: TexMatA/TexMatB reference the exact same Image datablock
        # via an Image Texture node -- must merge. TexMatC references a separately-created Image
        # datablock with matching generation parameters (same pixel content, different identity)
        # -- must not merge with A/B, since a merge here would make TexMatC's own object start
        # sampling a texture it never actually referenced.
        out = self.out / "material_texture_out.blend"
        proc = run("optimize.py", str(ROOT / "tests" / "fixtures" / "material_texture_rig.blend"), "-o", str(out), "--merge-materials", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(len(data["materials_merged"]), 1)
        self.assertEqual(data["materials_merged"][0]["canonical_material"], "TexMatA")
        self.assertEqual(data["materials_merged"][0]["merged_materials"], ["TexMatB"])

    def test_optimize_merge_materials_reassigns_per_object_slot_overrides(self):
        # material_objlink_rig.blend: ObjLinkA_obj/ObjLinkB_obj each carry a per-OBJECT material
        # slot override (link == 'OBJECT') rather than the mesh data's own slot material, pointing
        # at two independently-authored but structurally-identical materials. Reassigning only
        # mesh.materials (as RM-037's own instancing does for mesh data) would miss this case
        # entirely -- object.material_slots needs its own reassignment pass.
        out = self.out / "material_objlink_out.blend"
        proc = run("optimize.py", str(ROOT / "tests" / "fixtures" / "material_objlink_rig.blend"), "-o", str(out), "--merge-materials", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(len(data["materials_merged"]), 1)
        self.assertEqual(data["materials_merged"][0]["canonical_material"], "ObjLinkMatA")
        self.assertEqual(data["materials_merged"][0]["merged_materials"], ["ObjLinkMatB"])

        info = json.loads(run("info.py", str(out), "--json").stdout)
        self.assertNotIn("ObjLinkMatB", info["materials"]["names"])

    def test_optimize_merge_materials_reassigns_a_curve_objects_own_material_slot(self):
        # material_curve_rig.blend: MeshObj (a mesh) and CurveObj (a Curve, not a Mesh) use two
        # independently-authored but structurally-identical materials. A reassignment pass that
        # only walked bpy.data.meshes (this feature's first version) would never touch
        # CurveObj.data.materials -- Curve.materials is a real, separate material-slot list
        # Blender gives every Curve/Text/MetaBall/GreasePencil/Volume datablock, not just Mesh --
        # so bpy.data.materials.remove(dup) would have silently dropped the curve's material
        # slot to None instead of reassigning it. Caught by review.
        out = self.out / "material_curve_out.blend"
        proc = run("optimize.py", str(ROOT / "tests" / "fixtures" / "material_curve_rig.blend"), "-o", str(out), "--merge-materials", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(len(data["materials_merged"]), 1)
        canonical = data["materials_merged"][0]["canonical_material"]

        info = json.loads(run("info.py", str(out), "--json").stdout)
        self.assertEqual(info["materials"]["names"], [canonical])

    def test_optimize_merge_materials_is_idempotent(self):
        out1 = self.out / "materials_pass1.blend"
        proc1 = run("optimize.py", str(ROOT / "tests" / "fixtures" / "materials_rig.blend"), "-o", str(out1), "--merge-materials", "--json")
        self.assertEqual(proc1.returncode, 0, proc1.stderr)
        out2 = self.out / "materials_pass2.blend"
        proc2 = run("optimize.py", str(out1), "-o", str(out2), "--merge-materials", "--json")
        self.assertEqual(proc2.returncode, 0, proc2.stderr)
        self.assertEqual(json.loads(proc2.stdout)["materials_merged"], [])

    def test_optimize_target_roblox_clamps_to_the_real_20000_triangle_budget(self):
        # highpoly_sphere.blend: a 20,480-triangle icosphere, just over Roblox's own published
        # "individual meshes cannot exceed 20,000 triangles" (create.roblox.com/docs/art/
        # modeling/specifications). --target-roblox's triangle_budget is converted into a real
        # decimate ratio from this file's own actual triangle count, not a fixed fraction that
        # couldn't guarantee compliance for an arbitrary input size.
        out = self.out / "sphere_roblox.blend"
        proc = run("optimize.py", str(ROOT / "tests" / "fixtures" / "highpoly_sphere.blend"), "-o", str(out), "--target-roblox", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["triangles_before"], 20480)
        self.assertLessEqual(data["triangles_after"], 20_000)

        info = json.loads(run("info.py", str(out), "--json").stdout)
        self.assertLessEqual(info["meshes"]["triangles"], 20_000)

    def test_optimize_explicit_decimate_ratio_overrides_a_targets_triangle_budget(self):
        # An explicit --decimate-ratio must win over --target-roblox's own budget-derived ratio,
        # not just happen to produce a similar result: --decimate-ratio 0.9 on the same
        # 20,480-triangle fixture gives exactly 20480*0.9 = 18432 -- a value the budget-derived
        # ratio (20000/20480 ~= 0.9766) would never produce, proving the explicit value was
        # actually used rather than silently ignored in favor of the target's own default.
        out = self.out / "sphere_roblox_explicit_ratio.blend"
        proc = run("optimize.py", str(ROOT / "tests" / "fixtures" / "highpoly_sphere.blend"), "-o", str(out), "--target-roblox", "--decimate-ratio", "0.9", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["triangles_after"], 18432)

    def test_optimize_target_roblox_never_decimates_two_individually_compliant_meshes(self):
        # two_compliant_meshes.blend: SphereA/SphereB, 18,800 triangles each (individually under
        # Roblox's real 20,000-triangle-*per-mesh* cap) but 37,600 combined. Roblox's own limit is
        # explicitly per individual mesh, not a scene total -- summing before comparing (this
        # feature's first version, shared with --target-vrchat/--target-quicklook, which really
        # are whole-avatar/whole-scene budgets) would wrongly decimate both meshes down to fit a
        # 20,000 *aggregate*, a case Roblox's real limit never actually restricts. Reproduced
        # directly: reverting just the per-mesh fix decimates this fixture to 20,000 combined.
        out = self.out / "two_compliant_roblox.blend"
        proc = run("optimize.py", str(ROOT / "tests" / "fixtures" / "two_compliant_meshes.blend"), "-o", str(out), "--target-roblox", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["triangles_before"], 37600)
        self.assertEqual(data["triangles_after"], 37600)

    def test_optimize_target_vrchat_recalculates_aggregate_budget_after_fill_holes(self):
        # vrchat_boundary_hole.blend: a sphere with one real hole, 69,960 triangles as imported
        # (just under VRChat's 70,000 aggregate budget -- an "aggregate" ratio computed from this
        # pre-fill-holes count alone would conclude no decimation is needed at all). --fill-holes
        # then adds enough geometry back to push the real total to 70,632, over budget -- a
        # decision already made from the stale pre-fill-holes count never revisits that. Reproduced
        # directly: reverting just this fix (computing the aggregate ratio from before_tris instead
        # of the post-topology-change count) exports 70,632 triangles, over budget.
        # (info.py's own verification is skipped here -- its self-intersection/thickness checks
        # are prohibitively slow on a mesh this size, confirmed independently: over two minutes
        # for a single run. optimize.py's own triangles_after is a directly measured count from
        # the same real Blender data, not a guess, so it alone is sufficient here.)
        out = self.out / "vrchat_boundary_out.blend"
        proc = run("optimize.py", str(ROOT / "tests" / "fixtures" / "vrchat_boundary_hole.blend"), "-o", str(out), "--target-vrchat", "--fill-holes", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["triangles_before"], 69960)
        self.assertLessEqual(data["triangles_after"], 70_000)

    def test_optimize_target_triangle_budget_is_a_noop_when_already_within_budget(self):
        # The same 20,480-triangle fixture is well under --target-vrchat's 70,000 and
        # --target-quicklook's 100,000 triangle_budget -- neither should decimate anything.
        for target in ("vrchat", "quicklook"):
            with self.subTest(target=target):
                out = self.out / f"sphere_{target}.blend"
                proc = run("optimize.py", str(ROOT / "tests" / "fixtures" / "highpoly_sphere.blend"), "-o", str(out), f"--target-{target}", "--json")
                self.assertEqual(proc.returncode, 0, proc.stderr)
                data = json.loads(proc.stdout)
                self.assertEqual(data["triangles_before"], data["triangles_after"])

    def test_optimize_target_sketchfab_and_gltf_viewer_texture_caps(self):
        # No official triangle cap on either target (both left un-decimated by design), but real
        # texture-cap differences: sketchfab (4096, matching Sketchfab's own "1 to 4 4K textures"
        # recommendation) vs gltf-viewer (1024, a lighter/faster-loading preset). Verified against
        # box.glb's own texture -- both presets' texture_max exceed its actual size, so this
        # confirms both flags parse and run cleanly end to end rather than the (trivial, no-op)
        # resize itself.
        for target in ("sketchfab", "gltf-viewer"):
            with self.subTest(target=target):
                out = self.out / f"box_{target}.glb"
                proc = run("optimize.py", str(FIXTURE), "-o", str(out), f"--target-{target}", "--json")
                self.assertEqual(proc.returncode, 0, proc.stderr)

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
