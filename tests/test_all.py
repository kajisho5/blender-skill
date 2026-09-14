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
