# Contributing to blender-skill

Thanks for considering a contribution. This project has one job: execute explicit,
agent-given Blender operations deterministically, safely, and verifiably. See SKILL.md's
"What this skill does and does not decide" before proposing anything -- it defines the boundary
this project holds deliberately (no content/scene understanding, no creative judgement, no
AI/LLM integration, no cloud dependency, headless only).

## Before you start

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md).

- **Read `references/pitfalls.md` first.** It records every real Blender-version and
  cross-format gotcha found building this (an operator renamed between Blender versions, a
  format that legitimately can't round-trip losslessly, a GPU limitation under `-b`). A fix
  that looks obviously correct may already be a documented tradeoff there.
- **Small, hardening-focused changes are the easiest to land.** Bug fixes, cross-Blender-version
  correctness (`scripts/bpy/_compat.py`), test coverage for an edge case, and documentation
  drift fixes are always welcome. New tools or features are a higher bar -- see ROADMAP.md for
  what's already planned, and open an issue before writing code for anything not on it.
- **Every change needs a reproduction against a real Blender.** This project doesn't mock bpy --
  if you're fixing a bug, show the failing case (which Blender version, which script, the exact
  command) before your fix and the passing case after.

## Scope

This project intentionally does **not**:
- open Blender's UI, or depend on it being available (headless only, `-b`)
- depend on cloud services or require API keys
- mutate input files
- make creative or compositional decisions on the calling agent's behalf (see SKILL.md)
- add a pip/third-party dependency to the host-side scripts (stdlib only; Blender's own bundled
  Python, including its bundled numpy, is fair game *inside* `scripts/bpy/*.py`, which runs
  under Blender's interpreter, not the host's)

## Development

```bash
git clone https://github.com/kajisho5/blender-skill
cd blender-skill
python3 tests/test_all.py     # skips (not fails) without Blender installed
```

Python 3.9+ standard library only on the host side. Blender 4.2+ is required to run anything --
every script's own `--help` and error messages point at how to install it if it's missing.

## Tests

- `tests/test_all.py` -- real end-to-end checks against a real Blender and the fixtures in
  `tests/fixtures/` (a UV-less watertight cube, a rigged/UV'd fox). No mocked bpy.
- Add a test alongside any behavioral change. A fix without a regression test that would have
  caught the original bug isn't done yet.

## Pull requests

- PR titles are [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat:`/`fix:`/`docs:`/`chore:`/...) -- release-please resolves the next version from them.
- Keep PRs focused -- one fix or one small feature per PR.
- Run `python3 tests/test_all.py` locally before opening.
- Explain *why*, not just *what*, in the PR description -- the reasoning is what future
  maintainers (human or agent) need most.
- A `feat` PR updates every place the feature is stated: README.md's tool table and Scripts
  section, SKILL.md's routing table, and CHANGELOG.md (release-please writes the version-bump
  entry from your commit; a hand-written CHANGELOG.md entry is only needed for detail beyond the
  commit summary).

## Reporting issues

Bug reports should include: the exact command run, the actual vs. expected output/behavior, and
the Blender version (`blender --version`). See `tests/fixtures/` for the sample files this
project already tests against, in case your repro reduces to one of them.

## Roadmap

See [ROADMAP.md](ROADMAP.md) for the full backlog. `scripts/dev/roadmap_to_issues.py --dry-run`
shows what filing every remaining item as an issue would look like without needing `gh` or
network access.
