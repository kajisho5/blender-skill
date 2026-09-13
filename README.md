# blender-skill

Agent Skill scaffolding for giving coding agents (Claude Code, Cursor, Codex) local, headless
Blender capabilities -- modeled on the same repo layout, CI/release pipeline and packaging
conventions as [`ffmpeg-skill`](https://github.com/kajisho5/ffmpeg-skill).

**Status: scaffolding only.** This repository currently ships the project infrastructure
(CI, release automation, npm packaging, issue/PR templates) but no Blender tools yet.
`SKILL.md` and `scripts/` are placeholders to be filled in.

## Install

```bash
npx blender-skill                 # Claude Code (~/.claude/skills/blender-skill)
npx blender-skill --cursor        # Cursor  (~/.cursor/skills/blender-skill)
npx blender-skill --codex         # Codex   (~/.agents/skills/blender-skill)
npx blender-skill --project       # ./.claude/skills/blender-skill in the current project
```

## Development

```bash
git clone https://github.com/kajisho5/blender-skill
cd blender-skill
npm test
```

## License

MIT -- see [LICENSE](LICENSE).
