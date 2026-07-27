import re
from pathlib import Path
from urllib.parse import unquote

import pytest

MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+\.md)(?:#[^)]+)?\)")


@pytest.mark.contract
def test_relative_markdown_links_resolve(project_root: Path) -> None:
    broken: list[str] = []
    for source in project_root.rglob("*.md"):
        text = source.read_text(encoding="utf-8")
        for target in MARKDOWN_LINK.findall(text):
            if "://" in target:
                continue
            resolved = (source.parent / unquote(target)).resolve()
            if not resolved.is_file():
                broken.append(f"{source.relative_to(project_root)} -> {target}")

    assert not broken, "\n".join(broken)
