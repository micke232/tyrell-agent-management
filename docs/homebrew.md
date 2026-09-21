# GitHub releases and Homebrew

The application and Homebrew tap share the repository
[micke232/tyrell-agent-management](https://github.com/micke232/tyrell-agent-management).
There is one workflow: **Checks**. It runs on pull requests into `main`, pushes to
`main`, and version tags (`v*`). No external CI service or publishing token is needed.

## Checks before publishing

On macOS, the workflow runs the full unittest suite, builds a wheel and Homebrew
archive, installs the wheel in a clean environment, and exercises the actual
Homebrew virtualenv installer in a temporary prefix. Packages are saved as an
Actions artifact for 14 days. Interactive macOS clipboard automation is not run
on headless CI.

Only a version tag can create a GitHub release draft, and only after these checks
pass. Its commit must belong to `main`, and its name must match the package version.
The release job alone has write permission. A release remains a draft until you
publish it; ordinary merges never publish a new version.

## Branch workflow

Use feature branches and pull requests into `main`, including release preparation
and Homebrew formula updates. In GitHub Settings → Rules → Rulesets, protect
`main`: require a pull request and the **Tests and package** status check, and block
force pushes and deletion. Enable the check requirement after its first run.
Requiring another person's approval is optional for a solo project.

## Personal GitHub access

Authenticate to your personal GitHub separately from any company GitHub host:

```sh
gh auth login --hostname github.com --git-protocol https --web
```

Clone the repository, then create a feature branch for changes:

```sh
git clone https://github.com/micke232/tyrell-agent-management.git
cd tyrell-agent-management
git switch -c feature/my-change
```

Push the feature branch and open a pull request. Do not push application changes
directly to `main`.

## Release

1. Update `codex_dashboard/__init__.py` to a new version on a feature branch.
   Run the release preparation commands below and commit `Formula/agent-hub.rb`
   in that same PR. CI verifies its URL and checksum against the actual build.
2. Merge its pull request after checks pass.
3. From the updated `main`, create and push the matching tag, for example:

   ```sh
   git switch main
   git pull --ff-only
   git tag v0.2.0a6
   git push origin v0.2.0a6
   ```

4. Wait for Checks. Review the generated draft under GitHub Releases, mark alpha
   versions as prereleases, and publish it. All attached files were built together
   and tested; do not rebuild or replace the archive independently.
5. Homebrew is now available: the matching formula was already merged in the
   release PR. No second PR or bot commit is needed.

The archive contains the wheel and installation guide only, not local account
credentials, chat history, or worktrees. Re-running a completed release tag does
not overwrite an existing release; use a new version for changed packages.

## Installation after the first release is published

```sh
brew tap micke232/tyrell-agent-management https://github.com/micke232/tyrell-agent-management.git
brew install agent-hub
tyrell
```

The explicit URL allows the application repository to double as a tap. The formula
installs Python and Git. Ghostty remains optional; model CLIs and their sign-in are
configured separately. `tyrell setup` and F10 Settings guide connection setup.

For updates, stop idle agents with `tyrell stop`, then run `brew update` and
`brew upgrade agent-hub`.

## Local verification

```sh
python3 -B -m unittest discover -s tests
python3.14 -m venv .venv
.venv/bin/python -m pip install -r packaging/build-requirements.txt
.venv/bin/python scripts/prepare_release.py
HOMEBREW_DEVELOPER=1 HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_ANALYTICS=1 \
  brew ruby scripts/verify_homebrew.rb \
  "$PWD/Formula/agent-hub.rb" \
  "$PWD/dist/homebrew/agent-hub-0.2.0a6-homebrew.tar.gz"
```

Use the current version's wheel filename and an empty build output directory when
preparing a new release. The Homebrew check requires `python@3.14` locally.

References: [GitHub workflow permissions](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)
and [Homebrew taps](https://docs.brew.sh/How-to-Create-and-Maintain-a-Tap).

Build tools are pinned and wheel timestamps are fixed so PR and tag builds have
identical checksums. After changing packaged source or the installation guide,
rerun `scripts/prepare_release.py` with the build environment and commit the updated
formula. Never reuse a published version for changed code. Until its release is
published, a newly merged formula points to an unavailable asset.
