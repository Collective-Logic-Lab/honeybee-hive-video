# Releasing Hive Video Tools

Releases use the `hive-video` distribution name on PyPI and `vX.Y.Z` Git tags in `Collective-Logic-Lab/honeybee-hive-video`. The package version itself is `X.Y.Z`. During 0.x, minor releases may change the API; explain incompatible changes in the release notes. Patch releases contain compatible fixes.

## One-time PyPI setup

The initial PyPI owner is [peterdresslar](https://pypi.org/user/peterdresslar/). While signed into that account, open [account publishing settings](https://pypi.org/manage/account/publishing/) and add a pending GitHub publisher with these exact values:

| Field | Value |
| --- | --- |
| PyPI project name | `hive-video` |
| GitHub owner | `Collective-Logic-Lab` |
| Repository | `honeybee-hive-video` |
| Workflow filename | `release.yml` |
| Environment | `pypi` |

The GitHub owner is the lab organization even though the PyPI owner is a personal account. A pending publisher creates the PyPI project on its first successful upload and becomes its regular publisher. See [PyPI's setup instructions](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/).

The GitHub repository needs an environment named `pypi`. Restrict its deployment tags to `v*`. No PyPI API token is stored in GitHub or this repository: the publishing job obtains a short-lived credential for this workflow and environment.

## Prepare a release

1. Work on a branch. Set matching versions in `pyproject.toml` and `src/hive_video/__init__.py`, update `uv.lock` if needed, and write release notes under `docs/agent-generated/`.
2. Review the API and CLI changes, installation instructions, license, scientific claims, and intended package contents. The distribution includes the reusable tools, their package README, and license; it excludes research data and internal analyses.
3. Open a PR. The release workflow builds a wheel and source archive, then checks the wheel on Linux and macOS. Inspect its results and the diff before merging.
4. From the merged `main` revision, create and push the matching tag. Create a GitHub release for that existing tag and use the reviewed release notes. For the first release, these are `v0.1.0` and [release-v0.1.0.md](release-v0.1.0.md).

## Publish and verify

Publishing the GitHub release triggers [release.yml](../../.github/workflows/release.yml). It checks that the tag matches both package versions and points to a commit included in `main`, builds the distributions once, and tests that wheel on Linux and macOS. Separate publishing jobs attach the tested files to GitHub and upload the same files to PyPI. Pull requests and ordinary pushes run checks without publishing.

After both uploads succeed, compare the GitHub and PyPI distribution checksums and install the exact published version in a fresh environment. Check the base installation and the `resequence` extra. A GitHub release page alone does not establish that the PyPI upload succeeded.

If publishing fails, inspect the failed job before rerunning it. Keep already published distributions unchanged; a correction to released code receives a new version. Retain the tag, workflow run, artifact checksums, and verification results as the release record.
