"""bpy side of render.py: thumbnail, turntable (PNG sequence or FFmpeg-encoded video), and a
4-view sheet. Eevee by default (--cycles switches the engine); no HDRI preset yet -- this
skill ships no HDRI asset to preset against, so claiming one would be untested. See
references/pitfalls.md.
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common
import _compat

import bpy
import mathutils


def _scene_bounds():
    mesh_objs = [o for o in bpy.data.objects if o.type == "MESH"]
    bbox_min = mathutils.Vector((float("inf"),) * 3)
    bbox_max = mathutils.Vector((float("-inf"),) * 3)
    for o in mesh_objs:
        for corner in o.bound_box:
            world = o.matrix_world @ mathutils.Vector(corner)
            bbox_min = mathutils.Vector(min(a, b) for a, b in zip(bbox_min, world))
            bbox_max = mathutils.Vector(max(a, b) for a, b in zip(bbox_max, world))
    if not mesh_objs:
        return mathutils.Vector((0, 0, 0)), 2.0
    return (bbox_min + bbox_max) / 2, max((bbox_max - bbox_min).length, 0.1)


def _add_camera(center, size, azimuth_deg=45.0, elevation=0.7):
    angle = math.radians(azimuth_deg)
    dist = size * 1.4
    offset = mathutils.Vector((math.sin(angle), -math.cos(angle), elevation)) * dist
    cam_data = bpy.data.cameras.new("render_camera")
    cam_obj = bpy.data.objects.new("render_camera", cam_data)
    bpy.context.collection.objects.link(cam_obj)
    cam_obj.location = center + offset
    cam_obj.rotation_euler = (center - cam_obj.location).to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = cam_obj
    return cam_obj


def _add_lighting(preset: str):
    if preset == "studio":
        rig = [(1.0, (0.9, 0.3, 0.4)), (0.4, (-0.9, 0.5, 0.2)), (0.6, (0.0, -0.9, 0.6))]
        for energy, rot in rig:
            data = bpy.data.lights.new("studio_light", type="AREA")
            data.energy = energy * 200
            obj = bpy.data.objects.new("studio_light", data)
            bpy.context.collection.objects.link(obj)
            obj.rotation_euler = rot
            obj.location = mathutils.Vector(rot).normalized() * 5
    elif preset == "outdoor":
        data = bpy.data.lights.new("sun", type="SUN")
        data.energy = 3.0
        obj = bpy.data.objects.new("sun", data)
        bpy.context.collection.objects.link(obj)
        obj.rotation_euler = (0.7, 0.2, 0.5)
    else:
        raise ValueError(f"lighting preset {preset!r} is not implemented (studio, outdoor are)")


def _setup_scene(engine: str, resolution, samples):
    scene = bpy.context.scene
    scene.render.engine = "CYCLES" if engine == "cycles" else _compat.eevee_engine_id()
    if engine == "cycles":
        scene.cycles.samples = samples
    else:
        scene.eevee.taa_render_samples = samples
    scene.render.resolution_x, scene.render.resolution_y = resolution
    scene.render.film_transparent = True
    return scene


def _mode_thumbnail(args, scene):
    center, size = _scene_bounds()
    if not scene.camera:
        _add_camera(center, size)
    _add_lighting(args.get("light", "outdoor"))
    scene.render.filepath = args["output"]
    scene.render.image_settings.file_format = "PNG"
    bpy.ops.render.render(write_still=True)
    return {}


def _mode_turntable(args, scene):
    center, size = _scene_bounds()
    _add_camera(center, size)
    _add_lighting(args.get("light", "outdoor"))
    frames = args.get("frames", 24)
    scene.frame_start, scene.frame_end = 1, frames
    scene.render.fps = args.get("fps", 24)

    mesh_objs = [o for o in bpy.data.objects if o.type == "MESH"]
    pivot = bpy.data.objects.new("turntable_pivot", None)
    bpy.context.collection.objects.link(pivot)
    for o in mesh_objs:
        o.parent = pivot
        o.matrix_parent_inverse = pivot.matrix_world.inverted()
    for f in range(1, frames + 1):
        scene.frame_set(f)
        pivot.rotation_euler[2] = 2 * math.pi * (f - 1) / frames
        pivot.keyframe_insert("rotation_euler", index=2, frame=f)

    output = args["output"]
    if output.lower().endswith((".mp4", ".mov")):
        scene.render.image_settings.file_format = "FFMPEG"
        scene.render.ffmpeg.format = "MPEG4"
        scene.render.ffmpeg.codec = "H264"
        scene.render.filepath = output
    else:
        # A printf-style sequence path (e.g. turntable_####.png); Blender substitutes the frame
        # number for the run of '#' characters itself.
        scene.render.image_settings.file_format = "PNG"
        scene.render.filepath = output
    bpy.ops.render.render(animation=True)
    return {"frames": frames}


def _mode_sheet(args, scene):
    import numpy as np
    center, size = _scene_bounds()
    _add_lighting(args.get("light", "outdoor"))
    tiles = []
    tmp_dir = Path(args["output"]).parent
    for i, az in enumerate((0, 90, 180, 270)):
        cam = _add_camera(center, size, azimuth_deg=az)
        scene.camera = cam
        frame_path = str(tmp_dir / f"_sheet_tile_{i}.png")
        scene.render.filepath = frame_path
        scene.render.image_settings.file_format = "PNG"
        bpy.ops.render.render(write_still=True)
        img = bpy.data.images.load(frame_path)
        px = np.array(img.pixels[:], dtype="float32").reshape(img.size[1], img.size[0], img.channels)
        tiles.append(np.flipud(px))
        bpy.data.images.remove(img)
        Path(frame_path).unlink(missing_ok=True)
        bpy.data.objects.remove(cam, do_unlink=True)

    h, w = tiles[0].shape[:2]
    canvas = np.zeros((h * 2, w * 2, 4), dtype="float32")
    canvas[..., 3] = 1.0
    positions = [(0, 0), (0, 1), (1, 0), (1, 1)]
    for tile, (r, c) in zip(tiles, positions):
        canvas[r * h:(r + 1) * h, c * w:(c + 1) * w] = tile
    out_img = bpy.data.images.new("render_sheet", width=w * 2, height=h * 2, alpha=True)
    out_img.pixels.foreach_set(np.flipud(canvas).ravel())
    out_img.filepath_raw = args["output"]
    out_img.file_format = "PNG"
    out_img.save()
    bpy.data.images.remove(out_img)
    return {"tiles": 4}


_MODES = {"thumbnail": _mode_thumbnail, "turntable": _mode_turntable, "sheet": _mode_sheet}


def run(args):
    _compat.reset_scene()
    _compat.import_file(args["path"], args["format"])
    resolution = tuple(args.get("resolution") or (512, 512))
    samples = args.get("samples") or (16 if args.get("fast") else 64)
    scene = _setup_scene(args.get("engine", "eevee"), resolution, samples)
    mode = args["mode"]
    if mode not in _MODES:
        raise ValueError(f"unknown render mode {mode!r}")
    result = _MODES[mode](args, scene)
    return {"mode": mode, "output_path": args["output"], **result}


if __name__ == "__main__":
    _common.main(run)
