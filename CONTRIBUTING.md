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
- Write a [good commit message](https://tbaggery.com/2008/04/19/a-note-about-git-commit-messages.html).
- It's a good idea to arrange a discussion with other developers to ensure there is consensus on large features, architecture changes, and other core code changes. PR reviews will go much faster when there are no surprises.

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
