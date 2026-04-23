# Contributing to Optimus

First off — thanks for considering a contribution! Optimus is a classroom
teaching tool, and we love improvements that make the simulation more
realistic, the UI more intuitive, or the code easier to hack on.

- [Ways to contribute](#ways-to-contribute)
- [Development setup](#development-setup)
- [Running tests & linters](#running-tests--linters)
- [Project layout](#project-layout)
- [Commit messages](#commit-messages)
- [Pull request checklist](#pull-request-checklist)
- [Release flow](#release-flow)

## Ways to contribute

- **Report bugs** via the [bug report issue template][new-bug].
- **Suggest features** via the [feature request template][new-feature].
- **Improve docs** — the README, `docs/`, or inline docstrings.
- **Pick up a [good first issue][gfi]** if you're new.
- **Write tests** — `tests/` coverage is always welcome.
- **Discuss** design ideas in [Discussions][discussions] before large changes.

[new-bug]: https://github.com/timpara/Optimus/issues/new?template=bug_report.yml
[new-feature]: https://github.com/timpara/Optimus/issues/new?template=feature_request.yml
[gfi]: https://github.com/timpara/Optimus/labels/good%20first%20issue
[discussions]: https://github.com/timpara/Optimus/discussions

## Development setup

Requires **Python 3.12+**.

```bash
git clone https://github.com/timpara/Optimus.git
cd Optimus
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
```

Run the server in dev mode with auto-reload:

```bash
export OPTIMUS_CLASS_PASSWORD=trade2026
export OPTIMUS_ADMIN_KEY=dev-admin
uvicorn main:app --reload --port 8000
```

Open <http://localhost:8000>.

### Running in Docker

```bash
docker compose up --build
```

## Running tests & linters

We use **pytest**, **ruff** (lint + format), and **mypy** (strict on the
application package). All three run in CI — please make sure they pass
locally before opening a PR.

```bash
pytest                     # run the test suite
pytest --cov=optimus       # with coverage
ruff check .               # lint
ruff format --check .      # format check (use `ruff format .` to apply)
mypy .                     # static type check
```

If you install `pre-commit`, the same checks can run on every commit:

```bash
pip install pre-commit
pre-commit install
```

## Project layout

```
Optimus/
├── main.py                # FastAPI app + background tick loop (entry point)
├── index.html             # Single-page frontend (served at /)
├── pyproject.toml         # Package metadata, deps, tool configs
├── Dockerfile             # Production container image
├── docker-compose.yml     # One-command local/classroom deployment
├── docs/                  # Architecture, gameplay, deployment guides
├── tests/                 # pytest suite
└── .github/               # Workflows, issue/PR templates, dependabot
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for how the simulation
engine, market coupling, and WebSocket broadcast fit together.

## Commit messages

This repo uses [Conventional Commits](https://www.conventionalcommits.org/) so
that [release-please](https://github.com/googleapis/release-please) can
automate versioning and changelogs.

### Format

```
<type>(<optional scope>): <description>

[optional body]

[optional footer(s)]
```

### Allowed types

| Type       | Release bump | Purpose                                               |
|------------|--------------|-------------------------------------------------------|
| `feat`     | **minor**    | New feature                                           |
| `fix`      | **patch**    | Bug fix                                               |
| `perf`     | patch        | Performance improvement                               |
| `refactor` | none         | Code change that neither fixes a bug nor adds feature |
| `docs`     | none         | Documentation only                                    |
| `style`    | none         | Formatting, whitespace, etc.                          |
| `test`     | none         | Adding or fixing tests                                |
| `build`    | none         | Build system or dependency changes                    |
| `ci`       | none         | CI configuration                                      |
| `chore`    | none         | Misc maintenance                                      |
| `revert`   | none         | Revert a previous commit                              |

### Breaking changes

Append `!` after the type/scope **or** include a `BREAKING CHANGE:` footer.
Either triggers a **major** version bump.

```
feat!: drop support for Python 3.11

BREAKING CHANGE: minimum supported version is now 3.12
```

### Examples

```
feat: add hourly price forecast endpoint
fix(trader): correct SOC clamping at boundary
docs: document docker release workflow
ci: add commitlint on pull requests
refactor(api): extract pricing service
```

### Enforcement

- Every PR and every push to `main` is validated by
  `.github/workflows/commitlint.yml`.
- Non-conforming commits will fail CI and block the PR.

## Pull request checklist

Before opening a PR, please confirm:

- [ ] The PR title follows the Conventional Commits format above.
- [ ] `pytest` passes locally.
- [ ] `ruff check .` and `ruff format --check .` pass.
- [ ] `mypy .` passes (for backend changes).
- [ ] New behavior is covered by at least one test.
- [ ] User-visible changes are mentioned in the PR description.
- [ ] Docs (`README.md`, `docs/`) are updated if relevant.

## Release flow

1. Merge conventional commits into `main`.
2. `release-please` opens/updates a release PR with a generated
   `CHANGELOG.md` and version bump.
3. Merging the release PR creates a git tag + GitHub Release.
4. The `docker-release` workflow publishes the image to
   `ghcr.io/timpara/optimus` (public).
