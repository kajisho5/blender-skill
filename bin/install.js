#!/usr/bin/env node
/**
 * blender-skill installer.
 *
 * Copies SKILL.md and scripts/ into the skills directory of one or more
 * coding agents. Default target is Claude Code (~/.claude/skills/blender-skill).
 *
 *   npx blender-skill                 # Claude Code
 *   npx blender-skill --cursor        # Cursor  (~/.cursor/skills/blender-skill)
 *   npx blender-skill --codex         # Codex   (~/.agents/skills/blender-skill)
 *   npx blender-skill --all           # all of the above
 *   npx blender-skill --dir ./skills  # custom parent directory
 *   npx blender-skill --project       # ./.claude/skills/blender-skill in the current project
 *   npx blender-skill --uninstall     # remove from the selected targets
 *
 * Already installed? re-run `npx blender-skill` to refresh ~/.claude/skills/blender-skill
 * Copies are not updated automatically.
 */
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');

const SKILL_NAME = 'blender-skill';
const ROOT = path.resolve(__dirname, '..');
const PAYLOAD = ['SKILL.md', 'scripts', 'package.json'];

const args = process.argv.slice(2);
const has = (flag) => args.includes(flag);
const optValue = (flag) => {
  const i = args.indexOf(flag);
  return i !== -1 && args[i + 1] ? args[i + 1] : null;
};

if (has('--help') || has('-h')) {
  console.log(fs.readFileSync(__filename, 'utf8').split('*/')[0].replace(/^\/\*\*?\s?|^\s\*\s?/gm, ''));
  process.exit(0);
}

const home = os.homedir();
const targets = [];
const want = { claude: has('--claude'), cursor: has('--cursor'), codex: has('--codex') };
if (has('--all')) want.claude = want.cursor = want.codex = true;
const customDir = optValue('--dir');
const project = has('--project');

if (!want.claude && !want.cursor && !want.codex && !customDir && !project) want.claude = true;

if (want.claude) targets.push({ label: 'Claude Code', dir: path.join(home, '.claude', 'skills', SKILL_NAME) });
if (want.cursor) targets.push({ label: 'Cursor', dir: path.join(home, '.cursor', 'skills', SKILL_NAME) });
if (want.codex) targets.push({ label: 'Codex', dir: path.join(home, '.agents', 'skills', SKILL_NAME) });
if (project) targets.push({ label: 'project (.claude/skills)', dir: path.join(process.cwd(), '.claude', 'skills', SKILL_NAME) });
if (customDir) targets.push({ label: 'custom', dir: path.join(path.resolve(customDir), SKILL_NAME) });

function copyRecursive(src, dst) {
  const stat = fs.statSync(src);
  if (stat.isDirectory()) {
    fs.mkdirSync(dst, { recursive: true });
    for (const entry of fs.readdirSync(src)) {
      if (entry === '__pycache__') continue;
      copyRecursive(path.join(src, entry), path.join(dst, entry));
    }
  } else {
    fs.copyFileSync(src, dst);
    if (src.endsWith('.py')) fs.chmodSync(dst, 0o755);
  }
}

let failed = false;
for (const t of targets) {
  try {
    if (has('--uninstall')) {
      fs.rmSync(t.dir, { recursive: true, force: true });
      console.log(`removed ${t.label}: ${t.dir}`);
      continue;
    }
    // Copy into a scratch directory next to the real target first, then swap it into place
    // with a single rename, so an interrupted upgrade never leaves the target half-populated.
    const tmpDir = `${t.dir}.tmp-${process.pid}`;
    fs.rmSync(tmpDir, { recursive: true, force: true });
    fs.mkdirSync(tmpDir, { recursive: true });
    for (const item of PAYLOAD) {
      const src = path.join(ROOT, item);
      if (!fs.existsSync(src)) { if (item !== 'SKILL.md') continue; throw new Error(`missing ${item} in package`); }
      copyRecursive(src, path.join(tmpDir, item));
    }
    const bakDir = `${t.dir}.bak-${process.pid}`;
    fs.rmSync(bakDir, { recursive: true, force: true });
    const hadOld = fs.existsSync(t.dir);
    if (hadOld) fs.renameSync(t.dir, bakDir);
    try {
      fs.renameSync(tmpDir, t.dir);
    } catch (err) {
      if (hadOld) { try { fs.renameSync(bakDir, t.dir); } catch (_) { /* the old copy stays at bakDir */ } }
      throw err;
    }
    if (hadOld) fs.rmSync(bakDir, { recursive: true, force: true });
    console.log(`installed ${t.label}: ${t.dir}`);
  } catch (err) {
    failed = true;
    console.error(`failed for ${t.label} (${t.dir}): ${err.message}`);
  }
}

if (!has('--uninstall')) {
  console.log('\nDone. This is scaffolding only -- no Blender tools are implemented yet.');
}
process.exit(failed ? 1 : 0);
