"""bpy side of look.py: the agent's eyes. Four modes, each producing one PNG (or SVG for uv):

- uv: the UV layout, via Blender's own uv.export_layout(mode='SVG'). PNG mode of that same
  operator needs bpy.types.GPUOffScreen, which raises SystemError("GPU functions for drawing
  are not available in background mode") under `-b` -- confirmed on a real headless Blender
  4.2.23 (no GPU, software Mesa) -- so SVG is the only mode this uses in headless mode.
- wireframe: a real render (Eevee) with every mesh object's geometry replaced by a Wireframe
  modifier, non-destructively (the modifier is removed again before returning; nothing is
  written back to the source file).
- textures: a grid of every image in bpy.data.images, downscaled and composited with numpy
  (bundled with Blender's own Python -- this runs inside Blender, not the stdlib-only host).
- compare: two existing images placed side by side, for a before/after look.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common
import _compat

import bpy
import numpy as np


def _quick_render(output: str, resolution=(480, 480), samples=32):
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE_NEXT"
    scene.render.resolution_x, scene.render.resolution_y = resolution
    scene.render.filepath = output
    scene.render.image_settings.file_format = "PNG"
    scene.eevee.taa_render_samples = samples

    if scene.camera is None:
        _auto_camera_and_light(scene)
    bpy.ops.render.render(write_still=True)


def _auto_camera_and_light(scene):
    """Frame every mesh object from a 3/4 angle and add a basic sun light, for files that carry
    no camera of their own (most raw glTF/FBX asset exports)."""
    import mathutils
    mesh_objs = [o for o in bpy.data.objects if o.type == "MESH"]
    bbox_min = mathutils.Vector((float("inf"),) * 3)
    bbox_max = mathutils.Vector((float("-inf"),) * 3)
    for o in mesh_objs:
        for corner in o.bound_box:
            world = o.matrix_world @ mathutils.Vector(corner)
            bbox_min = mathutils.Vector(min(a, b) for a, b in zip(bbox_min, world))
            bbox_max = mathutils.Vector(max(a, b) for a, b in zip(bbox_max, world))
    center = (bbox_min + bbox_max) / 2 if mesh_objs else mathutils.Vector((0, 0, 0))
    size = max((bbox_max - bbox_min).length, 1.0) if mesh_objs else 2.0

    cam_data = bpy.data.cameras.new("look_camera")
    cam_obj = bpy.data.objects.new("look_camera", cam_data)
    bpy.context.collection.objects.link(cam_obj)
    cam_obj.location = center + mathutils.Vector((size, -size, size * 0.7))
    direction = center - cam_obj.location
    cam_obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    scene.camera = cam_obj

    light_data = bpy.data.lights.new("look_light", type="SUN")
    light_data.energy = 3.0
    light_obj = bpy.data.objects.new("look_light", light_data)
    bpy.context.collection.objects.link(light_obj)
    light_obj.rotation_euler = (0.7, 0.2, 0.5)


def _mode_uv(args):
    _compat.reset_scene()
    _compat.import_file(args["path"], args["format"])
    mesh_objs = [o for o in bpy.data.objects if o.type == "MESH" and o.data.uv_layers]
    if not mesh_objs:
        return {"skipped": "no mesh has a UV map"}
    for o in mesh_objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = mesh_objs[0]
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.export_layout(filepath=args["output"], mode="SVG", export_all=True)
    bpy.ops.object.mode_set(mode="OBJECT")
    return {"meshes_with_uv": len(mesh_objs)}


def _mode_wireframe(args):
    _compat.reset_scene()
    _compat.import_file(args["path"], args["format"])
    added = []
    for o in bpy.data.objects:
        if o.type != "MESH":
            continue
        mod = o.modifiers.new("look_wireframe", "WIREFRAME")
        mod.thickness = 0.01
        added.append((o, mod))
    _quick_render(args["output"], samples=args.get("samples", 16))
    for o, mod in added:
        o.modifiers.remove(mod)
    return {"objects_rendered": len(added)}


def _montage(images, tile=(256, 256), columns=4):
    """A grid of RGBA float images (each already resized to `tile`) into one composited array."""
    n = len(images)
    columns = max(1, min(columns, n))
    rows = (n + columns - 1) // columns
    w, h = tile
    canvas = np.zeros((rows * h, columns * w, 4), dtype=np.float32)
    canvas[..., 3] = 1.0
    for i, img in enumerate(images):
        r, c = divmod(i, columns)
        canvas[r * h:(r + 1) * h, c * w:(c + 1) * w, :] = img
    return canvas


def _resize(pixels: np.ndarray, size) -> np.ndarray:
    """Nearest-neighbor resize -- no PIL/scipy here, just numpy index math. Good enough for a
    thumbnail contact sheet; not meant for quality-critical output."""
    h, w = pixels.shape[:2]
    tw, th = size
    ys = (np.arange(th) * h // th).clip(0, h - 1)
    xs = (np.arange(tw) * w // tw).clip(0, w - 1)
    return pixels[ys][:, xs]


def _mode_textures(args):
    _compat.reset_scene()
    _compat.import_file(args["path"], args["format"])
    # Images imported from a packed source (the common case: glTF/FBX textures embedded in the
    # file) report has_data=False until their pixels are actually decoded -- accessing .pixels
    # is what triggers that lazy load (confirmed on Blender 4.2.23), so has_data must be checked
    # *after* touching .pixels once, not before.
    imgs = []
    for img in bpy.data.images:
        if img.name in ("Render Result", "Viewer Node"):
            continue
        if len(img.pixels) and img.has_data:
            imgs.append(img)
    if not imgs:
        return {"skipped": "no textures in this file"}
    tile = (256, 256)
    tiles = []
    for img in imgs:
        px = np.array(img.pixels[:], dtype=np.float32).reshape(img.size[1], img.size[0], img.channels)
        if img.channels == 3:
            px = np.dstack([px, np.ones(px.shape[:2], dtype=np.float32)])
        elif img.channels == 1:
            px = np.dstack([px] * 3 + [np.ones(px.shape[:2], dtype=np.float32)])
        tiles.append(_resize(np.flipud(px), tile))
    canvas = _montage(tiles, tile=tile, columns=min(4, len(tiles)))
    _save_png(canvas, args["output"])
    return {"textures": len(imgs), "names": [i.name for i in imgs]}


def _load_pixels(path: str) -> np.ndarray:
    img = bpy.data.images.load(path)
    px = np.array(img.pixels[:], dtype=np.float32).reshape(img.size[1], img.size[0], img.channels)
    if img.channels == 3:
        px = np.dstack([px, np.ones(px.shape[:2], dtype=np.float32)])
    bpy.data.images.remove(img)
    return np.flipud(px)


def _mode_compare(args):
    a = _load_pixels(args["image_a"])
    b = _load_pixels(args["image_b"])
    h = max(a.shape[0], b.shape[0])
    a = _resize(a, (a.shape[1], h))
    b = _resize(b, (b.shape[1], h))
    canvas = np.zeros((h, a.shape[1] + b.shape[1] + 4, 4), dtype=np.float32)
    canvas[..., 3] = 1.0
    canvas[:, :a.shape[1]] = a
    canvas[:, a.shape[1] + 4:] = b
    _save_png(canvas, args["output"])
    return {"width": canvas.shape[1], "height": canvas.shape[0]}


def _save_png(pixels: np.ndarray, output: str) -> None:
    h, w = pixels.shape[:2]
    img = bpy.data.images.new("look_output", width=w, height=h, alpha=True)
    img.pixels.foreach_set(np.flipud(pixels).ravel().astype(np.float32))
    img.filepath_raw = output
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)


_MODES = {"uv": _mode_uv, "wireframe": _mode_wireframe, "textures": _mode_textures, "compare": _mode_compare}


def run(args):
    mode = args["mode"]
    if mode not in _MODES:
        raise ValueError(f"unknown look mode {mode!r}")
    return {"mode": mode, **_MODES[mode](args)}


if __name__ == "__main__":
    _common.main(run)
