# Contributing

## Commit messages

This repo uses [Conventional Commits](https://www.conventionalcommits.org/) so that
[release-please](https://github.com/googleapis/release-please) can automate versioning
and changelogs.

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

Append `!` after the type/scope **or** include a `BREAKING CHANGE:` footer. Either
triggers a **major** version bump.

```
feat!: drop support for Python 3.10

BREAKING CHANGE: minimum supported version is now 3.11
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

- Every PR and every push to `main` is validated by `.github/workflows/commitlint.yml`.
- Non-conforming commits will fail CI and block the PR.

### Release flow

1. Merge conventional commits into `main`.
2. `release-please` opens/updates a release PR with a generated `CHANGELOG.md` and version bump.
3. Merging the release PR creates a git tag + GitHub Release.
4. The `docker-release` workflow publishes the image to the private registry.
