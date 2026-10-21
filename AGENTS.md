# AGENTS.md

Guidance for AI coding agents working in **qcom-deb-images** — the repo that
builds Debian/Ubuntu images and flashable assets for Qualcomm Linux boards.

This file complements, and does not replace, [CONTRIBUTING.md](CONTRIBUTING.md)
and [README.md](README.md). It is organised by activity: read the section that
matches what you are about to do.

## Orienting yourself

- [debos](https://github.com/go-debos/debos) recipes under `debos-recipes/`
  build a Debian rootfs (`…-image.yaml`) and per-board flashable assets
  (`…-flash.yaml`).
- Board-specific logic lives almost entirely in
  `debos-recipes/qualcomm-linux-debian-flash.yaml`; helper shell lives under
  `scripts/`.
- CI lives under `ci/` (LAVA boot configs) and `.github/workflows/`.

## Working with debos recipes

- The `…-flash.yaml` recipe mixes three languages: YAML (debos actions), Go
  [`text/template`](https://pkg.go.dev/text/template) (the `{{- … }}` blocks
  that build the board list and expand per-board actions), and POSIX shell (the
  bodies of `action: run`). When editing, keep each construct in its own layer —
  template-expand values into shell variables at the top of a `run` body, then
  keep the rest of the body in plain shell, as the existing actions do.
- External inputs (boot binaries, CDT archives, and the `qcom-ptool` /
  `qcom-dtb-metadata` tarballs) are pinned by URL **and** `sha256sum`, and
  verified at build time with `sha256sum --strict -c -`. Treat a URL and its
  checksum as a unit: never change one without regenerating the other from the
  actual bytes you downloaded.
- Tools fetched as source (`qcom-ptool`, `qcom-dtb-metadata`) are pinned to
  exact commits in the recipe. Changing a pin can change behaviour or data
  formats the recipe depends on — review what moved between the old and new
  commit before bumping.
- Prefer making the recipe data-driven over special-casing: most boards are just
  another entry in the board list. Reach for new template/shell logic only when
  a board needs handling the existing fields cannot express.

## Writing commit messages

- Follow the existing history's [Conventional Commits](https://www.conventionalcommits.org/)
  style: `feat(debos/flash): …`, `fix(debos/flash): …`, and `!` for breaking
  changes (`refactor(debos/flash)!: …`). Scope the subject to the area touched.
- Keep each change focused; split independent changes into separate commits.
- **AI attribution.** When an agent helped produce a change, add an
  `Assisted-by:` trailer per [CONTRIBUTING.md](CONTRIBUTING.md), one line per
  tool, in the format `Assisted-by: AGENT_NAME:MODEL_VERSION [TOOL…]`.
- **DCO.** Only a human may add `Signed-off-by:` to certify the
  [DCO](https://developercertificate.org/). Never add it on their behalf.

## Using project skills

Task-specific playbooks live under `skills/` in the portable `SKILL.md` format
(a `name` + `description` frontmatter and Markdown instructions), modeled on the
[qcom-linux-skills](https://github.com/qualcomm-linux/qcom-linux-skills) catalog
so any agent that understands `SKILL.md` (Claude Code, Codex, Cursor, …) can use
them. Each skill's `description` carries the phrases that should trigger it —
invoke a skill by name, or just describe the task.
