from __future__ import annotations

import re
from pathlib import Path


REPO_DIR = Path(__file__).resolve().parents[1]

# GitHub Pages serves a project site under the lower-cased owner and repository
# name. A link that keeps the repository's own capitalization returns 404, and
# no local build catches that: the built site is checked over localhost.
BROWSER_ANALYZER_URL = "https://alexandrefimov.github.io/query-doctor/"
PAGES_URL_RE = re.compile(r"https://[A-Za-z0-9-]+\.github\.io[^\s)\]\"'>]*")
README_FILES = ("README.md", "README.ru.md", "web/README.md")
DOC_GLOBS = (
    "*.md",
    "docs/**/*.md",
    "deploy/**/*.md",
    "web/*.md",
    ".github/ISSUE_TEMPLATE/*.md",
)


def documented_pages_urls() -> dict[str, list[str]]:
    urls: dict[str, list[str]] = {}
    for pattern in DOC_GLOBS:
        for path in sorted(REPO_DIR.glob(pattern)):
            if not path.is_file():
                continue
            found = PAGES_URL_RE.findall(path.read_text(encoding="utf-8"))
            if found:
                urls[str(path.relative_to(REPO_DIR))] = found
    return urls


def test_every_readme_links_the_deployed_browser_analyzer():
    for readme in README_FILES:
        text = (REPO_DIR / readme).read_text(encoding="utf-8")
        assert BROWSER_ANALYZER_URL in text, readme


def test_no_public_document_links_an_undeployed_pages_path():
    documented = documented_pages_urls()
    assert documented, "no public document links the browser analyzer"
    for document, urls in documented.items():
        for url in urls:
            assert url == BROWSER_ANALYZER_URL, f"{document}: {url}"
