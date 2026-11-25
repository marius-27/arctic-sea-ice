"""Checks that the project documentation exists and its relative links resolve."""

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DOCUMENTS = [
    PROJECT_ROOT / "README.md",
    PROJECT_ROOT / "docs" / "scientific-scope.md",
    PROJECT_ROOT / "docs" / "repository-guide.md",
    PROJECT_ROOT / "docs" / "data-provenance.md",
]

# Markdown inline links: [text](target)
LINK_PATTERN = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def _relative_links(document: Path) -> list[str]:
    text = document.read_text(encoding="utf-8")
    links = LINK_PATTERN.findall(text)
    return [link for link in links if not link.startswith(("http://", "https://", "mailto:"))]


@pytest.mark.parametrize("document", DOCUMENTS, ids=lambda p: p.name)
def test_document_exists_and_is_not_empty(document: Path) -> None:
    assert document.is_file()
    assert document.read_text(encoding="utf-8").strip()


@pytest.mark.parametrize("document", DOCUMENTS, ids=lambda p: p.name)
def test_relative_links_point_to_existing_files(document: Path) -> None:
    broken = []
    for link in _relative_links(document):
        target = link.split("#", maxsplit=1)[0]
        if target and not (document.parent / target).exists():
            broken.append(link)
    assert not broken, f"Broken links in {document.name}: {broken}"
