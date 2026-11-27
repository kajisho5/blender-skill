# Decisions

Design decisions for blender-skill, in the same one-bullet-per-ADR form the other AI Video
Production OS skills use (`docs/decisions.md`). Numbered in the order they were made; a later ADR
that changes an earlier one says so instead of editing it.

- **ADR-1 Resolution presets (RM-058): an explicit `--resolution W H` wins over
  `--resolution-preset NAME`, which wins over the 512x512 default.** `render.py
  --resolution-preset` names one frame's size: `thumbnail` 512x512 (the size render.py has always
  defaulted to), `square` 1080x1080, `720p` 1280x720, `1080p` 1920x1080, `4k` 3840x2160 (UHD-1,
  the consumer/streaming "4K"; DCI 4096x2160 is a cinema-projection size this skill's turntables
  are not delivered at), `vertical` 1080x1920 (9:16 short-form). "One frame" is literal: a
  thumbnail, every turntable frame or video frame, and every tile of `--sheet`, whose 2x2 sheet is
  therefore twice the preset in each dimension. Every preset is even in both dimensions, so it is
  always valid for H.264's 4:2:0 chroma subsampling.
  - *Precedence, not conflict.* Both flags may be given; the explicit size is used and the result
    reports the preset it overrode (`resolution.overridden_preset`) rather than dropping it
    silently. This differs from `optimize.py`'s `--texture-max` / `--texture-auto-resolution`,
    which refuse each other: those are two *strategies*, whereas a preset is only a name for a
    size, and a caller (or an MCP agent layering its own default preset under a user's exact size)
    adding an explicit size has stated the more specific intent. The result's `resolution` block
    always carries `width`, `height`, `preset` (null when explicit) and `source`
    (`explicit`/`preset`/`default`), so which rule applied is never guessed from the output.
  - *An unknown name is an error even when an explicit size would have won.* A misspelt preset is
    a mistake in the request, and accepting it because it happened to be overridden would hide
    that. It is `SkillError(kind="input")` -- `{"status": "error", "error": {"kind": "input",
    "message": "unknown resolution preset: '8k'", "hint": "valid presets: ..."}}` under `--json`,
    exit 1, the repo's existing input-error shape -- never argparse's `choices=` (exit 2 with usage
    text on stderr, no JSON). Names match case-insensitively (`4K` is `4k`); there are no aliases,
    so the contract's list is the complete set of accepted spellings.
  - *One table.* `RESOLUTION_PRESETS` in `scripts/_render_output.py` is read by render.py's
    validation, its `--help`, the MCP `render` tool description, and the contract's
    `tools[].options["--resolution-preset"]` (choices, sizes, default, precedence, error kind), so
    no listing can offer a name the CLI refuses.

- **ADR-2 Alpha mode and render passes (ECOSYSTEM_ROADMAP.md step 8): every alpha result states
  `alpha.mode`, `straight` or `premultiplied`; `--passes` writes shadow / ao / mist /
  object_index as a multilayer EXR or as one PNG per pass.**
  - *The words.* `alpha.mode` takes the same two values as color-grading-skill's
    `project.source.alpha_mode` (its ADR-33) and the agent's `source_alpha_mode` (its ADR-131),
    so a render can be handed on without translating. color-grading-skill cannot tell the mode
    of a ProRes 4444 or QTRLE file from the stream and assumes straight with a warning; this
    skill wrote the file, so it says which (`mode_source`: `request` or `default`).
  - *What Blender writes, measured, not assumed.* A PNG is straight (its specification, and
    Blender un-premultiplies on save). A ProRes 4444 `.mov` is straight too: on 4.5.13 a frame
    decoded back matches the PNG of the same render within 4 levels where alpha is below 255,
    where a premultiplied file would be several times darker. A multilayer EXR is premultiplied
    (OpenEXR convention, Blender's render buffer as is).
  - *`--alpha-mode`.* Needs `--transparent` or an `.exr` output (an EXR always carries alpha).
    PNG takes both: `premultiplied` re-writes each written PNG with `round(c * a / 255)`, so a
    pixel with alpha 255 is unchanged and only pixels with alpha below 255 differ. The alpha
    block then carries a `note`: a PNG cannot say it is premultiplied, so the caller has to pass
    the mode on (color-grading-skill refuses a premultiplied source for a grade that keeps the
    alpha, its ADR-33). A straight EXR or a premultiplied `.mov` is refused (`input`) instead of
    converted: either would mean re-encoding Blender's output after the fact, and the other
    container already holds the mode asked for. A premultiplied `--sheet` is refused too (the
    sheet is composed from its tiles after the render and is written straight).
  - *Passes.* `--passes` takes `beauty`, `shadow`, `ao`, `mist`, `object_index` (beauty is
    always written). `-o NAME.exr` writes one multilayer EXR per frame (a still, or
    `NAME_####.exr` for a turntable); `-o NAME.png` writes the beauty PNG plus `NAME.<pass>.png`
    per pass per frame, cut from an uncompressed float EXR of the same render that is then
    deleted; that intermediate has a hidden name of the run's own
    (`.NAME.passes-<random>.exr`), so the file it deletes is never one that was already there. PNG passes are data, not pictures: linear, 16-bit, no view transform; object_index
    is the index itself (0 = background), and when no mesh has a pass index each gets 1, 2, ...
    in name order (`object_index_map` in the result). Mist runs linearly from just in front of
    the model to just behind it, not Blender's fixed 5 m start. A sheet and a video take no
    passes (`input`).
  - *Per engine.* Checked on 4.5.13: Eevee writes Shadow but no object index; Cycles writes
    the object index but no Shadow (its shadow catcher is a different pass). A pass the chosen
    engine does not write is refused with the engine that does, rather than silently missing.
  - *Read back.* The `passes` block is read from the written files: the EXR header's channel
    list (stdlib `scripts/_exr.py`, every compression, single-part as 4.x writes it and
    multi-part as 5.x writes it, with 5.x's spelled-out `Ambient Occlusion` / `Object Index`
    layer names), or each pass PNG's header (size, bit depth, channels). `verified` is false
    with `missing` when a pass is not there.
  - *Contract.* Additive: two new `render` options in `tools[].options` (`--alpha-mode`,
    `--passes`), new result fields `alpha.mode`, `alpha.mode_source` and `passes`. Nothing
    existing changed.
