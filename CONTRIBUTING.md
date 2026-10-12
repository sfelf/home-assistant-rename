# Contributing to Home Assistant Rename

Thanks for helping out! Bug reports, ideas and pull requests are all welcome.

- **Found a bug or have an idea?** [Open an issue](https://github.com/sfelf/homeassistant-rename/issues).
  For bugs, include the command you ran (with `--dry-run` output if you can), your
  Home Assistant version and the error.
- **Found a security problem?** Please don't open a public issue. See
  [SECURITY.md](SECURITY.md) for how to report it privately.

## Development setup

You need [uv](https://docs.astral.sh/uv/). It installs the right Python and all
dependencies for you.

```bash
git clone https://github.com/sfelf/homeassistant-rename.git
cd homeassistant-rename
uv run ha-rename --help
```

Run the checks that CI runs:

```bash
uv run pytest
uv run ruff check
uv run ruff format
```

- `uv run pytest` runs the test suite with a coverage report. The tests use a fake
  Home Assistant WebSocket server and an in-memory keyring, so they never touch a real
  instance or your real keyring.
- [Ruff](https://docs.astral.sh/ruff/) checks the code (`ruff check`; add `--fix` to
  apply fixes) and formats it (`ruff format`).

When you try a change against a real Home Assistant, use `--dry-run` first.

## Code layout

All code lives in `src/ha_rename/`:

- `cli.py`: command-line parsing and the main program flow
- `rename.py`: works out which entities to rename and how (no I/O)
- `client.py`: minimal Home Assistant WebSocket API client
- `credentials.py`: saves the access token in the system keyring and prompts for it

Tests are in `tests/`, one file per module, with shared fixtures in `tests/conftest.py`.

## Pull requests

1. Fork the repository and create a branch for your change.
2. Make the change, with tests. Keep coverage at 100%: CI reports it to
   [Codecov](https://codecov.io/gh/sfelf/homeassistant-rename).
3. Update the README if you change user-facing behaviour or options.
4. Open a pull request and fill in the template.

`main` is protected: every change goes in through a pull request, and it can only be
merged once the **All checks passed** status is green. That covers lint, formatting
and the tests on Python 3.11–3.14 (Linux) plus macOS and Windows.
[Dependabot](.github/dependabot.yml) opens weekly pull requests to keep GitHub Actions
and Python dependencies up to date.

## Releasing (maintainers)

1. On a branch, bump the version in `pyproject.toml` (e.g. `uv version --bump minor`),
   open a pull request and merge it.
2. Tag the merged commit on `main` and push the tag:

   ```bash
   git switch main && git pull
   git tag -a v0.2.0 -m "v0.2.0"
   git push origin v0.2.0
   ```

   The [Release workflow](.github/workflows/release.yml) checks that the tag matches
   the version in `pyproject.toml`, runs the full test suite, builds the wheel and
   source archive, and creates a **draft** GitHub Release titled with the tag, with the
   files attached and generated release notes.
3. Review and edit the notes on the draft under
   [Releases](https://github.com/sfelf/homeassistant-rename/releases) (keep the title
   as the tag), then click **Publish release**. Nothing is public until you do.

## License

By contributing, you agree that your contributions are licensed under the
[GNU Affero General Public License v3.0 or later](LICENSE), the same as the project.
