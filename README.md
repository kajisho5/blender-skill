# blender-skill

**Give your coding agent a 3D asset pipeline.** Local Blender, headless, no cloud, no API keys.

## Install

```bash
npx blender-skill                                   # this repo's own installer (Node)
npx skills add kajisho5/blender-skill                # skills.sh registry
gh skill install kajisho5/blender-skill blender-skill  # GitHub CLI 2.90+
```

Any of the three installs `SKILL.md` and `scripts/` where your agent looks for skills. Requires
[Blender](https://www.blender.org/download/) 4.2+ on the machine; the scripts find it themselves
(`--blender` flag, `$BLENDER`, `PATH`, or the OS's usual install location) and tell you how to
install it if they can't.

## What this is / isn't

Inspects, converts, optimizes, renders, bakes and validates 3D assets from natural-language
requests -- for a coding agent working with `.glb`/`.gltf`/`.fbx`/`.obj`/`.stl`/`.usd`/`.usdz`/
`.ply`/`.abc`/`.blend` files, not for someone modeling interactively in Blender's UI. It runs
Blender exclusively headless (`blender -b`) and never opens a window. That makes it a complement
to, not a replacement for, [Blender's own MCP add-on](https://extensions.blender.org/add-ons/bmc/)
or [ahujasid/blender-mcp](https://github.com/ahujasid/blender-mcp) -- use this to check and fix a
file's numbers deterministically (triangle count, texture size, manifoldness, delivery-target
budgets), and a live Blender connection for anything that needs a human's eye on the actual
3D viewport.

## Features

- **`info.py`** -- object/mesh/material/texture/animation/armature counts, unit scale, bounding
  box, non-manifold-edge and duplicate-vertex detection (computed on a welded scratch copy, so a
  perfectly normal hard-edged/UV-seamed mesh isn't misreported as broken), UV presence.
- **`convert.py`** -- glb/gltf, fbx, obj, stl, usd, usdz, ply, abc, .blend, any direction;
  `--verify` re-imports the output and diffs it against the input.
- **`optimize.py`** -- decimate, weld duplicate vertices, recalculate normals, triangulate, cap
  texture resolution, purge orphan data; `--target-web`/`--target-mobile`/`--target-ar` presets;
  `--draco`/`--meshopt`/`--ktx2` delegate to [gltf-transform](https://github.com/donmccurdy/glTF-Transform)
  (and, for `--ktx2`, the [KTX-Software](https://github.com/KhronosGroup/KTX-Software) `ktx` CLI)
  when installed, and say so and skip just that step when they aren't.
- **`render.py`** -- a thumbnail, a 360° turntable (PNG sequence or an FFmpeg-encoded video), or
  a 4-view sheet. Eevee by default, `--cycles` to switch.
- **`look.py`** -- the agent's eyes: a wireframe render, a grid of every texture in the file, a
  before/after comparison, or the UV layout (as SVG -- headless Blender's PNG UV export needs a
  GPU offscreen context `-b` mode doesn't have).
- **`check.py`** -- PASS/WARN/FAIL against a delivery target's budget (three.js, Unity, Unreal,
  Godot, iOS/Android AR, WebXR, 3D printing, Sketchfab), every row naming its fix command.
- **`bake.py`** -- Cycles-bake a material to a texture (AO/normal/roughness/diffuse/combined);
  `--atlas` repacks UVs across several objects into one shared image first.
- **`scene.py`** -- assemble a declarative `scene.json` (asset placement, lights, camera,
  background) into a render and/or an exported 3D file.
- **`batch.py`** -- chain this skill's own scripts as a recipe over every file in a folder, with
  a content-hash cache so a re-run only reprocesses what changed.
- **`verify.py`** -- the whole toolchain (info → convert --verify → check) over one or more real
  files, PASS/FAIL per file.
- **MCP server** (`mcp/server.py`) -- every script above as an MCP tool over stdio.

## Usage

```
"check this glb for a Unity import"          -> check.py model.glb --target unity
"cut this to 50k triangles"                  -> info.py, then optimize.py --decimate-ratio ...
"convert this fbx to usdz and confirm it worked" -> convert.py model.fbx -o model.usdz --verify
"make a turntable of this model"             -> render.py model.glb --turntable -o turntable.mp4
"bake this material to a single texture"     -> bake.py model.glb --pass combined -o baked.png
```

## Scripts

| Script | Purpose |
|---|---|
| `info.py` | Inspect: counts, scale, bounding box, manifold/UV checks |
| `convert.py` | Cross-format conversion, with round-trip `--verify` |
| `optimize.py` | Decimate, weld, triangulate, cap texture size, purge unused, Draco/Meshopt/KTX2 (delegated) |
| `render.py` | Thumbnail, turntable, 4-view sheet |
| `look.py` | Wireframe, texture grid, before/after compare, UV layout |
| `check.py` | Delivery-target PASS/WARN/FAIL with fix commands |
| `bake.py` | Material-to-texture baking, with UV-atlas repacking |
| `scene.py` | Declarative multi-asset scene assembly |
| `batch.py` | Recipe chains over a folder, with a content-hash cache |
| `verify.py` | The whole toolchain over real files |

See `references/pitfalls.md` for the real gotchas found building this (GPU-offscreen limits
under `-b`, an Eevee engine-name rename between Blender versions, format round-trip quirks) and
`references/blender-versions.md` for which Blender versions have actually been tested.

## MCP

```json
{
  "mcpServers": {
    "blender-skill": {
      "command": "python3",
      "args": ["/path/to/blender-skill/mcp/server.py"]
    }
  }
}
```

Each tool takes one argument, `argv`: the exact CLI arguments you'd pass to that script.

## Requirements

- [Blender](https://www.blender.org/download/) 4.2 or newer (LTS releases tested: 4.2, 4.5;
  latest tested: 5.2 -- see `references/blender-versions.md`)
- Python 3.9+ for the host-side scripts (standard library only, no `pip install`)
- Node.js 16+ only for the `npx blender-skill` installer itself

## Development

```bash
git clone https://github.com/kajisho5/blender-skill
cd blender-skill
python3 tests/test_all.py
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and [ROADMAP.md](ROADMAP.md) for what's planned next.

## License

MIT -- see [LICENSE](LICENSE).
