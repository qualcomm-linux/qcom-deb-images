# Temporary workarounds

This document describes the policy for temporary workarounds in the images: when
to add one, where it lives, how it is enabled and tracked and when it should be
removed.

## When to use a workaround

A workaround is a temporary change that is needed until a fix lands in Debian
upstream. It may, for example, install, remove or pin a package, add or modify
a configuration file or other file to the root filesystem.

Permanent image policy belongs in the recipes, variants, profiles or overlays,
not in a workaround. If you can't name the upstream change that will allow the
workaround to be deleted, it is not a workaround.

## Layout

Each workaround lives in its own topic directory under
`debos-recipes/workarounds/`. The directory name is lowercase kebab-case and
describes the problem being worked around, e.g.
`debos-recipes/workarounds/<topic>/`. It contains:

- `README.md` (required): see [README contents](#readme-contents).
- `recipe.yaml` (required): a self-contained debos sub-recipe applying the
  workaround with existing debos actions, such as `apt`, `run` or `overlay`.
- any supporting files, such as an `overlay/` directory, referenced relative to
  `recipe.yaml`.

`recipe.yaml` receives the `suite` and `variant` variables from the root
filesystem recipe.

## Enabling workarounds

Workarounds are selected with the `workarounds` variable of the root filesystem
recipe, a `,`-separated list of topic directory names. It defaults to `none`,
which applies no workaround:

```bash
debos -t workarounds:<topic>[,<topic>...] debos-recipes/qualcomm-linux-debian-rootfs.yaml
```

Selected workarounds are applied in the given order, late in the root
filesystem build. Selecting a workaround that doesn't exist fails the build.

The selected workarounds are recorded in the image at `/etc/buildinfo` as
`WORKAROUNDS=<topic>[,<topic>...]`, so any image can be traced back to the
workarounds it was built with.

## Tracking

Each workaround must have a GitHub issue with the `workaround` label, linked
from its `README.md`. The issue tracks the upstream fix and the removal of the
workaround.

### README contents

Each workaround's `README.md` should briefly document:

- **Purpose**: The problem being worked around and its impact.
- **Changes**: What the workaround changes in the image.
- **References**: Links to relevant bugs, patches, discussions, releases or
  backports related to the workaround or its eventual removal.
- **Removal conditions**: When the workaround can be removed, such as when a
  package containing the fix has been released or backported.

The README title should be the human-readable name of the workaround.

These are guidelines rather than strict requirements. Authors can adapt the
structure as appropriate for each workaround, provided the README clearly
explains why it exists, what it does and when it can be removed.

Keep READMEs short and avoid duplicating implementation details already evident
from the recipe.

For example, `workarounds/dtb-loader/README.md`:

```markdown
# Automatically load DTBs when the boot firmware does not provide one

Tracking issue: https://github.com/qualcomm-linux/qcom-deb-images/issues/227

## Purpose

Some Qualcomm Compute platforms, such as Snapdragon laptops, do not
provide a suitable device tree through their boot firmware. Without
one, Linux cannot boot correctly.

## Changes

Install and configure Stubble to automatically select and load the
appropriate device tree during boot.

## References

- <link to upstream systemd-stub support>

## Removal conditions

Remove this workaround once automatic device tree selection is
supported by the boot components available in Debian.
```

## Maintenance and removal

The author of the tracking issue should be responsible for monitoring the
workaround's upstream progress and ensuring it is removed from this repository
once no longer necessary.

A workaround should be removed once its upstream fix is available in the suites
we build: delete its directory, drop it from any `workarounds` lists it is
selected in and close its tracking issue.
