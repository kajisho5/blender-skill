#!/usr/bin/env python3
"""Run a recipe (a chain of this skill's own scripts) over every 3D file in a folder, with a
content-hash cache so a re-run only reprocesses what actually changed.

A recipe is JSON: {"steps": [{"script": "optimize", "args": {"decimate-ratio": 0.5}}, ...]}.
Each step's "args" become CLI flags (`decimate-ratio: 0.5` -> `--decimate-ratio 0.5`; a bare
`true` becomes a flag with no value). The first step reads the source file; each later step
reads the previous step's output. Output files land in --out-dir, named
`<stem>_<script>.<ext>` per step (the same `<input>_<operation>.<ext>` convention every script
already uses on its own).

Usage:
  python3 scripts/batch.py assets/ --recipe recipe.json --out-dir out/
  python3 scripts/batch.py assets/ --recipe recipe.json --out-dir out/ --watch --watch-interval 5
"""
import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common
import _formats
from _common import SkillError

HERE = Path(__file__).resolve().parent


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()[:16]


def _step_argv(step: dict, input_path: str, output_path: str) -> list:
    argv = [sys.executable, str(HERE / f"{step['script']}.py"), input_path, "-o", output_path]
    for key, val in (step.get("args") or {}).items():
        if key == "format":  # consumed by _run_recipe to name the output file, not a real CLI flag
            continue
        flag = "--" + key.replace("_", "-")
        if val is True:
            argv.append(flag)
        elif val is False or val is None:
            continue
        else:
            argv += [flag, str(val)]
    return argv


def _run_recipe(path: Path, recipe: dict, out_dir: Path) -> dict:
    current = str(path)
    log = []
    for step in recipe["steps"]:
        out_fmt = step.get("args", {}).get("format") or path.suffix.lstrip(".")
        output = str(out_dir / f"{path.stem}_{step['script']}.{out_fmt}")
        argv = _step_argv(step, current, output)
        proc = subprocess.run(argv, capture_output=True, text=True)
        log.append({"script": step["script"], "argv": argv, "returncode": proc.returncode,
                    "stderr_tail": proc.stderr.strip().splitlines()[-5:]})
        if proc.returncode != 0:
            return {"file": str(path), "ok": False, "failed_step": step["script"], "log": log}
        current = output
    return {"file": str(path), "ok": True, "output": current, "log": log}


def _process_once(files: list, recipe: dict, out_dir: Path, cache_path: Path, use_cache: bool) -> list:
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if use_cache and cache_path.exists() else {}
    recipe_key = hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()[:16]
    results = []
    for f in files:
        file_key = f"{f}:{_file_hash(f)}:{recipe_key}"
        if use_cache and cache.get(str(f)) == file_key:
            results.append({"file": str(f), "ok": True, "cached": True})
            continue
        result = _run_recipe(f, recipe, out_dir)
        results.append(result)
        if use_cache and result["ok"]:
            cache[str(f)] = file_key
    if use_cache:
        cache_path.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("directory")
    ap.add_argument("--recipe", required=True, help="recipe JSON file")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--cache", action="store_true", help="skip files whose content+recipe hash matches a previous successful run")
    ap.add_argument("--watch", action="store_true", help="keep re-scanning the directory (Ctrl-C to stop)")
    ap.add_argument("--watch-interval", type=float, default=5.0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    directory = _common.require_exists(args.directory, "directory")
    recipe = json.loads(_common.require_exists(args.recipe, "recipe").read_text(encoding="utf-8"))
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_path = out_dir / ".batch_cache.json"

    def scan():
        return sorted(p for p in directory.iterdir() if p.is_file() and _formats.detect_format(str(p)))

    if args.watch:
        try:
            while True:
                results = _process_once(scan(), recipe, out_dir, cache_path, args.cache)
                for r in results:
                    tag = "cached" if r.get("cached") else ("ok" if r["ok"] else "FAIL")
                    print(f"{tag:6s} {r['file']}")
                time.sleep(args.watch_interval)
        except KeyboardInterrupt:
            return 0

    results = _process_once(scan(), recipe, out_dir, cache_path, args.cache)
    if args.json:
        print(json.dumps(results, indent=2, sort_keys=True))
    else:
        for r in results:
            tag = "cached" if r.get("cached") else ("ok" if r["ok"] else "FAIL")
            print(f"{tag:6s} {r['file']}" + (f" -> {r['output']}" if r.get("output") else ""))
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
