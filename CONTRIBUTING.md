# Contributing to blender-skill

Thanks for considering a contribution. This project is early scaffolding: the CI/release
pipeline and packaging exist, but the actual Blender tooling under `SKILL.md` and `scripts/`
has not been written yet.

## Before you start

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md).

Open an issue to discuss scope before writing a new tool or script -- the intended shape
(deterministic, explicit, verifiable operations invoked from an agent, no hidden creative
judgement) follows the same principles as [`ffmpeg-skill`](https://github.com/kajisho5/ffmpeg-skill);
see that repo's `CONTRIBUTING.md` and `SKILL.md` for the pattern this one is expected to follow.

## Development

```bash
git clone https://github.com/kajisho5/blender-skill
cd blender-skill
npm test
```

## Pull requests

- Keep PRs focused -- one fix or one small feature per PR.
- Explain *why*, not just *what*, in the PR description.
