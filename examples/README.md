# Examples

`make_demo.sh` runs the core pipeline (info, optimize, convert --verify, render, look, bake,
check) against `tests/fixtures/fox.glb` and writes every output to `out/` (git-ignored). Run it
by hand to see the whole skill work end to end:

```bash
bash examples/make_demo.sh
```

Needs Blender 4.2+ -- see `scripts/_run.py`'s discovery order (`--blender`, `$BLENDER`, `PATH`,
or the OS's usual install location).
