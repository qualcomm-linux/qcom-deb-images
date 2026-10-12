# Contributing to Qualcomm Linux deb images

Hi there!
We’re thrilled that you’d like to contribute to this project.
Your help is essential for keeping this project great and for making it better.

## Branching Strategy

In general, contributors should develop on branches based off of `main` and pull requests should be made against `main`.

## Submitting a pull request

1. Please read our [code of conduct](CODE-OF-CONDUCT.md) and [license](LICENSE.txt).
1. [Fork](https://github.com/qualcomm-linux/qcom-deb-images/fork) and clone the repository.
    
    ```bash
    git clone https://github.com/<username>/qcom-deb-images.git
    ``` 

1. Create a new branch based on `main`:

    ```bash 
    git checkout -b <my-branch-name> main
    ```

1. Create an upstream `remote` to make it easier to keep your branches up-to-date:

    ```bash
    git remote add upstream https://github.com/qualcomm-linux/qcom-deb-images.git
    ```

1. Make your changes, add tests, and make sure the tests still pass.
1. Commit your changes using the [DCO](https://developercertificate.org/). You can attest to the DCO by commiting with the **-s** or **--signoff** options or manually adding the "Signed-off-by":
    
    ```bash
    git commit -s -m "Really useful commit message"`
    ```

1. After committing your changes on the topic branch, sync it with the upstream branch:

    ```bash
    git pull --rebase upstream main
    ```

1. Push to your fork.

    ```bash
    git push -u origin <my-branch-name>
    ```

    The `-u` is shorthand for `--set-upstream`. This will set up the tracking reference so subsequent runs of `git push` or `git pull` can omit the remote and branch.

1. [Submit a pull request](https://github.com/qualcomm-linux/qcom-deb-images/pulls) from your branch to `main`.
1. Pat yourself on the back and wait for your pull request to be reviewed.

Here are a few things you can do that will increase the likelihood of your pull request to be accepted:

- Follow the existing style where possible.
- Write tests.
- Keep your change as focused as possible.
  If you want to make multiple independent changes, please consider submitting them as separate pull requests.
- Write well-formed commit messages for each change, following the [commit messages](#commit-messages) documentation below.
- It's a good idea to arrange a discussion with other developers to ensure there is consensus on large features, architecture changes, and other core code changes. PR reviews will go much faster when there are no surprises.

## Running the GitHub workflows

Pull requests automatically run the `*-on-pr.yml` workflows.

The remaining workflows - the daily and weekly builds - are ran only for the `main` branch, driven by `schedule` and by the completion of other workflows, so they are never ran for a pull request.

To exercise them, run them manually against a branch pushed directly to this repository (branches from forks are not supported):

```bash
BRANCH=my-branch-name

# run the workflows
gh workflow run build-debian.yml --ref "$BRANCH"
gh workflow run build.yml --ref "$BRANCH"
gh workflow run linux-arduino.yml --ref "$BRANCH"
gh workflow run linux-next.yml --ref "$BRANCH"
gh workflow run linux-qcom-next.yml --ref "$BRANCH"
gh workflow run linux-mainline.yml --ref "$BRANCH"
gh workflow run u-boot.yml --ref "$BRANCH"

# list the workflows ran on this branch
gh run list --branch "$BRANCH"
```

They can also be run from the *Actions* tab on GitHub: pick the workflow, *Run workflow* and select the branch.

A few things to keep in mind:

- The branch has to be pushed to the `qualcomm-linux/qcom-deb-images` repository; branches on a fork cannot be used, as the workflows check that they are running from the main repository and the LAVA credentials are not available elsewhere.
  This requires write access, so ask a maintainer to run the workflow for you if you are working from a fork.
- The workflow file must already exist with a `workflow_dispatch:` trigger on `main`, but the version that runs is the one on the branch you select.
  Changes to an existing workflow can be tested before they are merged but a brand new workflow cannot be run until it lands on `main`.
- Changes to a `schedule:`, `push:` or `workflow_run:` trigger itself cannot be tested this way, as GitHub always reads those from `main`.
- These builds use the self-hosted runners and the LAVA boards, which are shared and limited. A full run takes a while; avoid queueing more of them than you need.

## Commit messages

When writing commit messages, follow the [well-formed git commit message](https://tbaggery.com/2008/04/19/a-note-about-git-commit-messages.html) guide.

The subject of each commit message (e.g. the first line) should be in the form:

```
<area>: <description>
```

The `area` should identify the part of the project affected by the change. Prefer the path-like prefixes already used in the repository, such as `debos/flash`,
`debos/rootfs`, `ci`, `kernel`, `kernel-configs`, `Makefile`, `README` or `scripts/build-linux-deb`.

For changes spanning multiple areas, use a broader prefix that describes the overall change. The prefix may be omitted when no meaningful area can be identified.

Some examples:

```
ci: reschedule nightly builds to allow more time for testing
debos/rootfs: install additional USB debugging packages
kernel-configs: enable support for the RB3 Gen 2 USB hub
README: document the supported image variants
docs: expand the testing documentation
scripts/build-linux-deb: handle release candidate versions
```

General guidelines:

- Keep the subject concise (within 72 characters).
- Use the imperative mood (e.g. "add" rather than "added" or "adds").
- Start the description with a lowercase letter, unless it begins with a proper noun or identifier.
- Do not end the subject with a full stop.
- Separate the subject from the commit body with a blank line.
- Use the body to explain **why** the change is needed and provide any relevant context, rather than simply repeating what the diff does.
- Reference relevant issues or bug reports in the body where appropriate.

The length of each line in the commit message is checked by a linter to ensure
it does not exceed 72 characters.

### Breaking changes

Breaking changes should be indicated by appending `!` to the area prefix,
immediately before the colon, for example:

```
debos/rootfs!: change the default partition layout
```

For changes without an area prefix, use `!: description`.

The commit body should explain the compatibility impact and any migration steps
required. A `BREAKING CHANGE:` footer may also be included to make the impact explicit.

Commit messages do not need to follow the Conventional Commits specification.
Prefixes such as `feat`, `fix`, `refactor` and `chore` are not required; please
identify the affected area instead.

# AI-Assisted Contributions

- Contributions that were developed with the help of generative AI tools (e.g.,
  coding assistants, chat-based models, AI agents) must follow the guidelines
  in this document.
- These guidelines apply to all forms of contribution including code,
  documentation, tests, and configuration.
- AI tools assisting with development on this project must follow the standard
  contribution process, including any project-specific guidelines, coding
  style, and procedures documented in this repository.
- **Fully autonomous submissions are not permitted.** A human contributor must
  be actively involved in reviewing, validating, and taking ownership of every
  change before it is submitted.

## Licensing and Legal Requirements

All contributions—whether human-written or AI-assisted—must comply with the
project's licensing requirements:

- Contributors are responsible for ensuring that AI-generated content does not
  introduce code with incompatible licenses or infringe on third-party
  intellectual property.
- Do not knowingly submit AI output that reproduces copyrighted material
  verbatim. If you suspect a suggestion originates from a specific copyrighted
  source, do not include it.

## DCO and Sign-off

AI tools and agents **must not** add `Signed-off-by` tags. Only a human can
certify the [Developer Certificate of Origin
(DCO)](https://developercertificate.org/).

The human submitter is responsible for:

- **Reviewing** all AI-generated or AI-assisted code before submission.
- **Ensuring compliance** with this project's licensing and contribution
  requirements.
- **Adding their own** `Signed-off-by` tag to certify the DCO.
- **Taking full responsibility** for the contribution, just as they would for
  entirely human-written code.

If you use an AI tool to generate or modify code, you are still the author of
record and bear the same obligations as any other contributor.

## Attribution

Attribution helps track the evolving role of AI in the development process.
Contributions should follow the [Linux Kernel's coding
assistant](https://docs.kernel.org/process/coding-assistants.html) format and
include an Assisted-by tag in the following format:

```
Assisted-by: AGENT_NAME:MODEL_VERSION [TOOL1] [TOOL2]
```

Where:

- AGENT_NAME is the name of the AI tool or framework
- MODEL_VERSION is the specific model version used
- [TOOL1] [TOOL2] are optional specialized analysis tools used (e.g.,
  coccinelle, sparse, smatch, clang-tidy)

If multiple AI tools were used, add a separate `Assisted-by` line for each.

### Commit Example with Attribution

Below is an example of a properly formatted commit message that includes AI
attribution:

```
feat: add input validation for user config parser

Add bounds checking and type validation to the configuration file
parser to prevent crashes on malformed input.

Assisted-by: Claude:claude-3-opus coccinelle sparse
Signed-off-by: Jane Smith <jane.smith@example.com>
```
