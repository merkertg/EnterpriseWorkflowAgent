"""Check that inline Markdown links point to existing repository files."""

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"!?\[[^\]\n]*\]\((<[^>\n]+>|[^\s)]+)(?:\s+[^)]*)?\)")


def main() -> int:
    """Report missing local targets; remote URLs and anchors are outside this check."""
    files = [
        ROOT / "README.md",
        ROOT / "AGENTS.md",
        *sorted((ROOT / "docs").rglob("*.md")),
    ]
    errors: list[str] = []
    checked = 0
    for source in files:
        # Ignore examples inside fenced code blocks rather than treating them as links.
        content = re.sub(
            r"^```[^\n]*\n.*?^```[^\n]*$",
            "",
            source.read_text(encoding="utf-8"),
            flags=re.MULTILINE | re.DOTALL,
        )
        for match in LINK.finditer(content):
            target = match.group(1).strip("<>")
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            checked += 1
            resolved = (source.parent / unquote(parsed.path)).resolve()
            if not resolved.is_relative_to(ROOT) or not resolved.exists():
                errors.append(
                    f"{source.relative_to(ROOT)}: missing local target {target}"
                )
    if errors:
        print("\n".join(errors))
        return 1
    print(f"Checked {checked} local links in {len(files)} Markdown files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
