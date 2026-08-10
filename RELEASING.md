# Releasing

Publishing is automated by `.github/workflows/publish.yml`, which uploads to PyPI
when a GitHub Release is published. Authentication uses **PyPI Trusted Publishing**,
so no API token is stored in this repository, in GitHub secrets, or on any laptop.

## One-time setup

You only do this once, and it must be done before the first automated release.

1. Sign in at [pypi.org](https://pypi.org) as the owner of the `puntersedge` project.
2. Go to **Your projects → puntersedge → Manage → Publishing**.
3. Under *Add a new publisher*, choose **GitHub** and enter:

   | Field | Value |
   | --- | --- |
   | Owner | `Propertyscout001` |
   | Repository | `puntersedge-python` |
   | Workflow name | `publish.yml` |
   | Environment name | `pypi` |

4. Save. PyPI will now accept uploads that come from this workflow, and only
   from this workflow.

Optionally, in the GitHub repo under **Settings → Environments → pypi**, add
yourself as a required reviewer. The upload then pauses for your approval —
worth doing, because a PyPI version number can be yanked but never reused.

## Cutting a release

1. Update `CHANGELOG.md`.
2. Bump the version in **both** places — they are checked against each other in CI:
   - `pyproject.toml` → `version`
   - `src/puntersedge/__init__.py` → `__version__`
3. Commit and push to `main`.
4. Create the GitHub Release with a tag matching the version, prefixed with `v`:

   ```bash
   gh release create v0.2.0 --title "v0.2.0" --notes-file CHANGELOG.md
   ```

   The workflow verifies the tag matches `pyproject.toml` and fails the release
   rather than shipping a mismatched version.

5. Watch the run. On success the package is live at
   <https://pypi.org/project/puntersedge/>.

## Dry run

To build and verify without uploading, run the workflow manually from the
**Actions** tab with *dry run* left ticked. It builds, runs `twine check`, and
installs the wheel into a clean venv to confirm it imports — then stops.

## After a release that changes the public API

The website's Python guide (`templates/guide_python.html` on the web host) shows
install instructions and a code example. If a release renames methods, update
that page **after** the upload succeeds, never before — otherwise the site
documents calls that the currently-installable package does not have.
