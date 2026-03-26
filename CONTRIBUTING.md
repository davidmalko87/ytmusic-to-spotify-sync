# Contributing

Thank you for considering a contribution to **ytmusic-to-spotify-sync**!

---

## Reporting issues

- Search [existing issues](../../issues) before opening a new one.
- Use the provided issue templates (bug report / feature request).
- Include your Python version, OS, and the full error output.

## Submitting pull requests

1. Fork the repository and create a feature branch from `master`.
2. Make your changes and verify they work end-to-end.
3. Run the linter and ensure it passes before pushing:
   ```bash
   pip install ruff
   ruff check .
   ```
4. Open a PR using the provided template and fill in all sections.

---

## Versioning policy (Semantic Versioning)

This project follows [semver](https://semver.org/): `MAJOR.MINOR.PATCH`.

| Change type | Version bump | Example |
|-------------|--------------|---------|
| Bug fix, documentation, refactor | **PATCH** | `0.4.0` → `0.4.1` |
| New backward-compatible feature | **MINOR** | `0.4.1` → `0.5.0` |
| Breaking change (removes/renames a command, changes output format) | **MAJOR** | `0.5.0` → `1.0.0` |

### Two-file update rule

**Every change that modifies behaviour must update both files together in the same commit:**

| File | What to update |
|------|---------------|
| `playlist_sync/__init__.py` | `__version__ = "X.Y.Z"` — the single canonical version string |
| `CHANGELOG.md` | Add a new `## [X.Y.Z] - YYYY-MM-DD` section describing the change |

`playlist_sync/__init__.py` is the **single source of truth** for the version. Do not hardcode the version anywhere else.

PRs that change behaviour without bumping the version and updating the changelog will be asked to do so before merging.

---

## Development setup

```bash
# Clone and install dependencies
git clone https://github.com/davidmalko87/ytmusic-to-spotify-sync.git
cd ytmusic-to-spotify-sync
pip install -r requirements.txt

# Verify the entry point loads
python -c "import playlist_sync; print(playlist_sync.__version__)"

# Run the linter
pip install ruff
ruff check .
```

## Code style

- Python 3.10+ features are fine (`match`, `X | Y` unions, etc.).
- Ruff is the only enforced linter — keep `ruff check .` clean.
- Keep functions small and focused. Prefer clarity over cleverness.
- Do not add docstrings, comments, or type annotations to code you did not change.
