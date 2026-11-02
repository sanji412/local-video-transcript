"""Keep the published entry point and iteration notes navigable."""

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]


def test_readme_links_to_iteration_changelog():
    assert "(CHANGELOG.md)" in (ROOT / "README.md").read_text()
    assert (ROOT / "CHANGELOG.md").is_file()


def test_changelog_local_links_resolve_and_releases_are_unique():
    path = ROOT / "CHANGELOG.md"
    content = path.read_text()
    releases = re.findall(r"^## \[(\d+\.\d+\.\d+)\]", content, re.MULTILINE)
    assert len(releases) >= 6
    assert len(releases) == len(set(releases))
    for target in re.findall(r"\]\(([^)]+)\)", content):
        url = urlsplit(target)
        if not url.scheme and url.path:
            assert (path.parent / unquote(url.path)).is_file(), target
