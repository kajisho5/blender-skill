#!/usr/bin/env bash
# Runs the core pipeline end to end against the fixture in tests/fixtures/ and writes real
# output to examples/out/ (git-ignored) -- a smoke test you can run by hand to see the whole
# skill work, not just unit-test assertions. Needs Blender; see scripts/_run.py's search order.
set -euo pipefail
cd "$(dirname "$0")/.."

OUT=examples/out
rm -rf "$OUT"
mkdir -p "$OUT"

SRC=tests/fixtures/fox.glb  # has a UV map, unlike box.glb -- needed for --textures/bake below

echo "== info =="
python3 scripts/info.py "$SRC"

echo "== optimize (decimate to 60%) =="
python3 scripts/optimize.py "$SRC" -o "$OUT/fox_optimized.glb" --decimate-ratio 0.6 --weld-doubles --purge-unused

echo "== convert + verify (glb -> fbx) =="
python3 scripts/convert.py "$SRC" -o "$OUT/fox.fbx" --verify

echo "== render: thumbnail, turntable, sheet =="
python3 scripts/render.py "$SRC" -o "$OUT/thumbnail.png" --fast
python3 scripts/render.py "$SRC" --turntable -o "$OUT/turntable.mp4" --frames 24 --fast
python3 scripts/render.py "$SRC" --sheet -o "$OUT/sheet.png" --fast

echo "== look: wireframe, textures, uv =="
python3 scripts/look.py "$SRC" --wireframe -o "$OUT/wireframe.png" --fast
python3 scripts/look.py "$SRC" --textures -o "$OUT/textures.png"
python3 scripts/look.py "$SRC" --uv -o "$OUT/uv.svg"

echo "== bake: ambient occlusion =="
python3 scripts/bake.py "$SRC" --pass ao --size 512 -o "$OUT/bake_ao.png"

echo "== check: three.js and webxr =="
python3 scripts/check.py "$SRC" --target three.js || true
python3 scripts/check.py "$SRC" --target webxr || true

echo
echo "Done. See $OUT/ for every generated file."
