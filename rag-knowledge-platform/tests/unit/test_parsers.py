"""Parser behavior per format, including the rejection paths."""

import pytest

from rag_platform.exceptions import ParseError, UnsupportedDocumentTypeError
from rag_platform.ingestion.parsers import parse_document, source_type_for_filename
from rag_platform.models import DocumentSourceType


def make_pdf(pages: list[str]) -> bytes:
    """Minimal valid PDF with correct xref offsets — no library needed."""

    def obj(n: int, body: str) -> bytes:
        return f"{n} 0 obj\n{body}\nendobj\n".encode()

    def stream_obj(n: int, s: str) -> bytes:
        b = s.encode()
        return (
            f"{n} 0 obj\n<< /Length {len(b)} >>\nstream\n".encode() + b + b"\nendstream\nendobj\n"
        )

    n_pages = len(pages)
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n_pages))
    font_obj = 3 + 2 * n_pages
    objects = [
        obj(1, "<< /Type /Catalog /Pages 2 0 R >>"),
        obj(2, f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>"),
    ]
    for i, text in enumerate(pages):
        page_n, content_n = 3 + 2 * i, 4 + 2 * i
        objects.append(
            obj(
                page_n,
                (
                    f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                    f"/Contents {content_n} 0 R /Resources << /Font << /F1 {font_obj} 0 R >> >> >>"
                ),
            )
        )
        objects.append(
            stream_obj(content_n, f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET" if text else "")
        )
    objects.append(obj(font_obj, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"))
    out, offsets = b"%PDF-1.4\n", []
    for o in objects:
        offsets.append(len(out))
        out += o
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{off:010d} 00000 n \n".encode() for off in offsets)
    trailer = f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    out += trailer.encode()
    return out


# --- markdown ---

MD = b"""# Title

Intro paragraph here.

## Section A

First point in section A.

### Deep

Nested content line.
"""


def test_markdown_blocks_and_heading_paths():
    blocks = parse_document(MD, DocumentSourceType.MARKDOWN)
    texts = [b.text for b in blocks]
    assert "Intro paragraph here." in texts
    by_text = {b.text: b.meta for b in blocks}
    assert by_text["Intro paragraph here."]["headings"] == ["Title"]
    assert by_text["First point in section A."]["headings"] == ["Title", "Section A"]
    assert by_text["Nested content line."]["headings"] == ["Title", "Section A", "Deep"]


def test_markdown_headings_only_is_rejected():
    with pytest.raises(ParseError):
        parse_document(b"# Only\n## Headings\n", DocumentSourceType.MARKDOWN)


def test_markdown_invalid_utf8_rejected():
    with pytest.raises(ParseError):
        parse_document(b"\xff\xfe broken", DocumentSourceType.MARKDOWN)


# --- html ---

HTML = b"""<html><head><style>p{color:red}</style>
<script>var SECRET_JS = 1;</script></head>
<body><h1>Guide</h1><h2>Install</h2>
<p>Run the installer.</p><ul><li>Step one item.</li></ul></body></html>"""


def test_html_strips_script_and_style_tracks_headings():
    blocks = parse_document(HTML, DocumentSourceType.HTML)
    all_text = " ".join(b.text for b in blocks)
    assert "SECRET_JS" not in all_text and "color:red" not in all_text
    assert "Run the installer." in all_text and "Step one item." in all_text
    by_text = {b.text: b.meta for b in blocks}
    assert by_text["Run the installer."]["headings"] == ["Guide", "Install"]


def test_html_without_text_rejected():
    with pytest.raises(ParseError):
        parse_document(b"<html><body><script>x()</script></body></html>", DocumentSourceType.HTML)


# --- pdf ---


def test_pdf_pages_carry_page_meta():
    raw = make_pdf(["Alpha page text.", "Beta page text."])
    blocks = parse_document(raw, DocumentSourceType.PDF)
    assert any("Alpha" in b.text for b in blocks) and any("Beta" in b.text for b in blocks)
    pages = {b.meta.get("page") for b in blocks}
    assert pages == {1, 2}


def test_pdf_corrupt_bytes_rejected_with_reason():
    with pytest.raises(ParseError) as exc_info:
        parse_document(b"\x00\x01 not a pdf at all", DocumentSourceType.PDF)
    assert "invalid PDF" in str(exc_info.value)


def test_pdf_without_text_layer_rejected():
    with pytest.raises(ParseError) as exc_info:
        parse_document(make_pdf([""]), DocumentSourceType.PDF)
    assert "no extractable text" in str(exc_info.value)


# --- filename dispatch ---


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("a.md", DocumentSourceType.MARKDOWN),
        ("B.MARKDOWN", DocumentSourceType.MARKDOWN),
        ("x.PDF", DocumentSourceType.PDF),
        ("y.htm", DocumentSourceType.HTML),
    ],
)
def test_source_type_for_filename(name, expected):
    assert source_type_for_filename(name) == expected


@pytest.mark.parametrize("name", ["noext", "z.docx", "z."])
def test_source_type_unsupported(name):
    with pytest.raises(UnsupportedDocumentTypeError):
        source_type_for_filename(name)
