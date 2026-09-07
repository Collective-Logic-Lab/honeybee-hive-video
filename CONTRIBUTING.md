# Contributing

Contributions from new programmers are welcome. Bug reports, questions, clearer examples, and small fixes all help. This is a scientific project: we value code that another person can understand, check, and explain.

## Questions, bugs, and suggestions

Use [GitHub issues](https://github.com/Collective-Logic-Lab/honeybee-hive-video/issues). Search briefly for an existing discussion, then tell us what you were trying to do and what happened. For a bug, include the command or small example, expected result, error message, and package version if you have them. For a feature, describe what it would help you do.

You do not need to diagnose the problem or provide a perfect report. Questions and incomplete reports are welcome. Share a small example or relevant log excerpt rather than uploading a full video; remove credentials and private information first.

## Branches and pull requests

Make changes on a branch and submit a pull request (PR), including for small fixes and documentation. A PR is the place to explain and review a proposed change before it reaches `main`. Automated enforcement of the [Solo-to-Small workflow](https://github.com/peterdresslar/rulesets/blob/main/solo-to-small.md) is planned; follow this workflow now.

1. If you cannot push to this repository, work in a fork, your own GitHub copy. Switch to `main` and update it before creating a branch: `git switch main`, `git pull --ff-only`, then `git switch -c fix-download-message`.
2. Keep the change focused. You do not need a separate issue for a small fix; discuss substantial changes before investing a lot of work.
3. Check your change, read the complete diff, then commit and push your branch. Open a PR explaining the problem, the change, and how you checked it. Mention anything you could not test.

Draft PRs and requests for help are welcome. Contributors with merge access may merge after reviewing their own diff; another person's approval is optional. Ask for review when it would help, especially for scientific or compatibility changes. Do not force-push or delete `main`.

## The package is released software

`src/hive_video/` is the released `hive-video` package. People use its Python functions and command-line tools from other projects. Changes to function arguments, command options, defaults, or output formats can break their work.

Preserve existing public behavior unless a change has been discussed and agreed. Discuss breaking changes before implementing them, identify them clearly in the PR, and include updated documentation and an example showing how existing callers should adapt. Coordinate version changes and releases with a maintainer. Keep optional resequencing dependencies optional.

Keep reusable package code separate from this repository's analysis, experiments, and cluster launchers. For scientific changes, explain the assumptions and what your checks establish. Record consequential method decisions in [METHODS.md](docs/agent-generated/METHODS.md); do not silently change the meaning of existing results.

## Local setup and checks

Use Python 3.12 or newer and `uv`; the [README](README.md#prerequisites) explains how to clone the repository. From your checkout's root:

```bash
uv sync --locked --extra resequence
```

This prepares the local development environment, including resequencing support. Run Ruff on the Python files you changed and run the relevant tests. These commands check the package and test code with Ruff and run the complete test suite:

```bash
uv run ruff check src/hive_video tests
uv run python -m unittest discover -s tests
```

Use small, deterministic examples and add a focused test for changed behavior when appropriate. Documentation edits usually need a read-through, working links, and checked examples. Keep large videos, generated results, credentials, and local editor files out of commits; commit the updated `uv.lock` when dependencies change. Ask before work that requires substantial downloads or cluster compute.

## Working with coding agents

Coding agents must always read and follow the root [AGENTS.md](AGENTS.md) before doing work. It is the shared working contract. Agents must not create hidden directories for instructions, memory, or working state, such as `.agents/`, `.codex/`, or `.claude/`, or introduce alternate agent instruction files.

You are responsible for everything you submit, including code written with an agent. Read it, run it, and be able to explain its purpose, assumptions, and behavior. Do not submit large amounts of generated code you do not understand. Work in small pieces and ask for help when something is unclear; generated explanations and passing tests do not replace your understanding.

Use the human contributor's Git identity. Coding agents must not be listed as commit authors, co-authors, or committers, including in `Co-authored-by` trailers. You may describe tool assistance in the PR text.
