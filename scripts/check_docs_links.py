# /// script
# requires-python = ">=3.12"
# ///
"""Check every internal link in docs/ and the navigation in docs/docs.json.

    uv run scripts/check_docs_links.py            # print what was checked, and every problem
    uv run scripts/check_docs_links.py --check    # print the problems alone

Both modes exit 1 when something is wrong. Three things are checked, and no network is used.

- Every internal link resolves: `/reference/cli` is a page, `/images/hero-light.svg` is a file,
  and `#a-heading` is a heading on the page that links to it.
- Every page under docs/ appears exactly once in the navigation, and every navigation entry is a
  page that exists.
- Every redirect points at a page that exists, from a path that is no longer one.

Links inside a code fence, inside an inline code span and inside an MDX comment are page content,
not links, so they are skipped. An external link is not fetched.

A link's anchor is normalised the same way a heading is, so `#the-a/v-value` and `#the-av-value`
both name the heading "The a/v value". The check is that the heading is there, not that the author
guessed one site's punctuation rule.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
NAV = DOCS / "docs.json"

FENCE = re.compile(r"^(?P<fence>```+|~~~+).*?^(?P=fence)", re.MULTILINE | re.DOTALL)
MDX_COMMENT = re.compile(r"\{/\*.*?\*/\}", re.DOTALL)
CODE_SPAN = re.compile(r"`[^`\n]*`")
MD_LINK = re.compile(r"(?<!\\)\[[^\]\n]*\]\(\s*(?P<target>[^)\s]+)")
ATTR_LINK = re.compile(r"\b(?:href|src)\s*=\s*\"(?P<target>[^\"]+)\"")
HEADING = re.compile(r"^#{1,6}\s+(?P<text>.+?)\s*#*\s*$", re.MULTILINE)
EXTERNAL = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|//)")


@dataclass
class Page:
    """One .mdx file: the slug the site serves it at, and the anchors it offers."""

    slug: str
    path: Path
    anchors: set[str]
    links: list[str] = field(default_factory=list)


def slugify(text: str) -> str:
    """A heading's anchor: its words, lowercase, joined by hyphens.

    Markdown around the text goes first, so `### \\`decktalk build\\`` is `decktalk-build`, and then
    every run of characters that cannot sit in an anchor becomes one hyphen. The same function
    reads the anchor a link asks for, so the two sides are compared as words rather than as one
    site's spelling of a slash or a quotation mark.
    """
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # a link in a heading keeps its words
    text = re.sub(r"[`*]", "", text).strip().lower()
    return re.sub(r"[^a-z0-9_]+", "-", text).strip("-")


def prose(text: str) -> str:
    """The page with its code fences and MDX comments blanked out, line count kept."""
    for pattern in (FENCE, MDX_COMMENT):
        text = pattern.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    return text


def read_pages() -> dict[str, Page]:
    """Every page under docs/, keyed by the slug the site serves it at."""
    pages: dict[str, Page] = {}
    for path in sorted(DOCS.rglob("*.mdx")):
        slug = path.relative_to(DOCS).with_suffix("").as_posix()
        body = prose(path.read_text(encoding="utf-8"))
        # Headings keep their code spans, because most of them are a command name in backticks.
        anchors = {slugify(m["text"]) for m in HEADING.finditer(body)}
        body = CODE_SPAN.sub("", body)  # a link inside `backticks` is a sample, not a link
        links = [m["target"] for m in MD_LINK.finditer(body)] + [m["target"] for m in ATTR_LINK.finditer(body)]
        pages[slug] = Page(slug, path, anchors, links)
    return pages


def nav_slugs(nav: dict) -> list[str]:
    """Every page named in the navigation, in the order it is listed, however deep it is nested."""
    out: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, str):
            out.append(node)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            for key, value in node.items():
                if key in {"pages", "groups", "tabs", "anchors", "navigation"}:
                    walk(value)

    walk(nav.get("navigation", {}))
    return out


def check_link(target: str, page: Page, pages: dict[str, Page], redirects: dict[str, str]) -> str | None:
    """The problem with one link target, or None when it resolves."""
    if EXTERNAL.match(target) or target.startswith(("mailto:", "?")):
        return None
    path, _, raw = target.partition("#")
    anchor = slugify(raw)
    if not path:
        return None if anchor in page.anchors else f"#{raw} is not a heading on this page"
    if not path.startswith("/"):
        return f"{target} is relative, and an internal link is written from the site root"
    slug = path.strip("/")
    if slug in redirects:
        return f"{path} is a redirect to /{redirects[slug]}, so link that page instead"
    if slug not in pages:
        return None if (DOCS / slug).is_file() else f"{path} is neither a page nor a file under docs/"
    if anchor and anchor not in pages[slug].anchors:
        return f"{path} has no heading #{raw}"
    return None


def problems() -> list[str]:
    """Every broken link, every page outside the navigation, and every navigation entry with no page."""
    pages = read_pages()
    nav = json.loads(NAV.read_text(encoding="utf-8"))
    listed = nav_slugs(nav)
    redirects = {r["source"].strip("/"): r["destination"].strip("/") for r in nav.get("redirects", [])}
    found: list[str] = []

    for slug in sorted(pages):
        page = pages[slug]
        for target in page.links:
            problem = check_link(target, page, pages, redirects)
            if problem:
                found.append(f"{page.path.relative_to(ROOT)}: {problem}")

    # MDX rejects an HTML comment, and Mintlify then fails to render the whole page.
    for page in pages.values():
        if "<!--" in page.path.read_text(encoding="utf-8"):
            found.append(f"{page.path.relative_to(ROOT)}: an HTML comment, which MDX cannot parse, so use {{/* */}}")

    for slug in sorted(set(pages) - set(listed)):
        found.append(f"docs/docs.json: {slug} is a page and is in no navigation group")
    for slug in listed:
        if slug not in pages:
            found.append(f"docs/docs.json: navigation names {slug}, and docs/{slug}.mdx is not there")
    for slug in sorted({s for s in listed if listed.count(s) > 1}):
        found.append(f"docs/docs.json: navigation names {slug} more than once")

    for source, destination in sorted(redirects.items()):
        if source in pages:
            found.append(f"docs/docs.json: /{source} redirects away and is still a page")
        if destination not in pages:
            found.append(f"docs/docs.json: /{source} redirects to /{destination}, which is not a page")

    logo = nav.get("logo")
    assets = {"favicon": nav.get("favicon")}
    assets |= {f"logo.{k}": v for k, v in (logo or {}).items() if k != "href"} if isinstance(logo, dict) else {}
    assets |= {"logo": logo} if isinstance(logo, str) else {}
    for key, asset in sorted(assets.items()):
        if isinstance(asset, str) and not (DOCS / asset.strip("/")).is_file():
            found.append(f"docs/docs.json: {key} names {asset}, which is not a file under docs/")

    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="print the problems alone")
    args = ap.parse_args()
    found = problems()
    for line in found:
        print(line)
    if found:
        print(f"{len(found)} broken links or navigation problems in docs/.")
        return 1
    if not args.check:
        pages = read_pages()
        links = sum(len(p.links) for p in pages.values())
        print(f"{len(pages)} pages, {links} links, every internal link and every navigation entry resolves.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
