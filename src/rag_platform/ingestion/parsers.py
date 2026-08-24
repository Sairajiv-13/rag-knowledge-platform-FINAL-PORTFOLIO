"""Document parsers: bytes -> list[Block].

Each Block carries provenance metadata (page number or heading path) that
survives chunking and ends up in chunks.meta — that's what makes citations
point at "guide.md § Deployment > TLS" instead of just a chunk id.

Deliberately boring parsers (pypdf, bs4, a hand-rolled heading scanner) over a
document-AI dependency; their failure modes are listed in the README.
"""

import io
import re
from dataclasses import dataclass, field
from typing import Any

from bs4 import BeautifulSoup
from pypdf import PdfReader

from rag_platform.exceptions import ParseError, UnsupportedDocumentTypeError
from rag_platform.models import DocumentSourceType

_SUFFIX_TO_TYPE = {
    ".pdf": DocumentSourceType.PDF,
    ".md": DocumentSourceType.MARKDOWN,
    ".markdown": DocumentSourceType.MARKDOWN,
    ".html": DocumentSourceType.HTML,
    ".htm": DocumentSourceType.HTML,
}


def source_type_for_filename(filename: str) -> DocumentSourceType:
    """Shared by the upload route and the CLI so they can never disagree on
    what's supported."""
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    try:
        return _SUFFIX_TO_TYPE[suffix]
    except KeyError:
        raise UnsupportedDocumentTypeError(
            f"unsupported file extension {suffix or '(none)'!r} "
            f"(supported: {sorted(_SUFFIX_TO_TYPE)})"
        ) from None


@dataclass(frozen=True)
class Block:
    text: str
    meta: dict[str, Any] = field(default_factory=dict)


def parse_document(raw: bytes, source_type: DocumentSourceType) -> list[Block]:
    if source_type is DocumentSourceType.PDF:
        return _parse_pdf(raw)
    if source_type is DocumentSourceType.MARKDOWN:
        return _parse_markdown(raw)
    if source_type is DocumentSourceType.HTML:
        return _parse_html(raw)
    # Unreachable while the enum has three members; kept so a new enum value
    # can't silently fall through to "parsed as nothing".
    raise UnsupportedDocumentTypeError(f"no parser for source_type={source_type}")


def _parse_pdf(raw: bytes) -> list[Block]:
    try:
        reader = PdfReader(io.BytesIO(raw))
        blocks = []
        for page_no, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                blocks.append(Block(text=text, meta={"page": page_no}))
    except ParseError:
        raise
    except Exception as exc:  # pypdf raises a zoo of exception types
        raise ParseError(f"invalid PDF: {exc}") from exc
    if not blocks:
        raise ParseError("PDF has no extractable text (scanned image without OCR?)")
    return blocks


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^(```|~~~)")


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ParseError("file is not valid UTF-8 text") from exc


def _parse_markdown(raw: bytes) -> list[Block]:
    """Heading-aware scanner: one Block per section, meta = heading path.

    A full markdown AST buys nothing here — retrieval wants plain text plus
    'where in the document am I', which headings provide.
    """
    blocks: list[Block] = []
    heading_stack: list[tuple[int, str]] = []
    buf: list[str] = []
    in_fence = False

    def flush() -> None:
        text = "\n".join(buf).strip()
        buf.clear()
        if text:
            blocks.append(Block(text=text, meta={"headings": [h for _, h in heading_stack]}))

    for line in _decode(raw).splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            buf.append(line)
            continue
        match = None if in_fence else _HEADING_RE.match(line)  # '# ...' in code is code
        if match:
            flush()
            level, title = len(match.group(1)), match.group(2).strip()
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, title))
        else:
            buf.append(line)
    flush()

    if not blocks:
        raise ParseError("markdown has no body text (headings alone are not content)")
    return blocks


_TEXT_TAGS = ["p", "li", "pre", "blockquote", "td", "th"]
_HEADING_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6"]


def _parse_html(raw: bytes) -> list[Block]:
    try:
        soup = BeautifulSoup(raw, "html.parser")  # stdlib parser: no C dependency
    except Exception as exc:
        raise ParseError(f"invalid HTML: {exc}") from exc
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()

    blocks: list[Block] = []
    heading_stack: list[tuple[int, str]] = []
    for el in soup.find_all(_HEADING_TAGS + _TEXT_TAGS):
        if el.name in _HEADING_TAGS:
            title = el.get_text(" ", strip=True)
            if not title:
                continue
            level = int(el.name[1])
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, title))
            continue
        # find_all returns nested matches too (<li><p>x</p></li> would yield x
        # twice); keep only the outermost text element.
        if el.find_parent(_TEXT_TAGS) is not None:
            continue
        text = el.get_text(" ", strip=True)
        if text:
            blocks.append(Block(text=text, meta={"headings": [h for _, h in heading_stack]}))

    if not blocks:
        raise ParseError("HTML has no extractable text content")
    return blocks
