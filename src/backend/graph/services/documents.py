"""
Write a piece of text as a real office file: a document, a sheet, a picture.

The graph is seeded with public documents from the Albert API, and a drive
made of thirty text files says nothing about a drive: what a reader recognises
is a folder holding notes, tables and scans side by side. So each document is
written in one of three formats, and each one is a file the pipeline can read
back on its own ‒ Tika opens the two OpenDocument ones, and reads the picture
with OCR, which is what any scan uploaded by hand goes through.

No library is needed for either: an OpenDocument file is a zip of two XML
parts, and the picture is drawn with Pillow, which the image thumbnails
already bring in. Nothing here knows about Albert or about Drive.
"""

import io
import textwrap
import zipfile
from xml.sax.saxutils import escape

from PIL import Image, ImageDraw, ImageFont

# The fonts of the backend image (font-noto, installed in the Dockerfile).
FONT_REGULAR = "/usr/share/fonts/noto/NotoSans-Regular.ttf"
FONT_BOLD = "/usr/share/fonts/noto/NotoSans-Bold.ttf"

ODT_MIMETYPE = "application/vnd.oasis.opendocument.text"
ODS_MIMETYPE = "application/vnd.oasis.opendocument.spreadsheet"
PNG_MIMETYPE = "image/png"

# An OpenDocument reader looks at the first bytes of the zip for the mimetype,
# which has to be the first entry and stored uncompressed.
_MANIFEST = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" '
    'manifest:version="1.2">'
    '<manifest:file-entry manifest:full-path="/" manifest:media-type="{mimetype}"/>'
    '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
    "</manifest:manifest>"
)

_NAMESPACES = (
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
    'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
    'office:version="1.2"'
)


def _opendocument(mimetype, body):
    """A zipped OpenDocument file holding ``body`` as its content.xml."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(zipfile.ZipInfo("mimetype"), mimetype, compress_type=zipfile.ZIP_STORED)
        archive.writestr(
            "META-INF/manifest.xml",
            _MANIFEST.format(mimetype=mimetype),
            compress_type=zipfile.ZIP_DEFLATED,
        )
        archive.writestr(
            "content.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            f"<office:document-content {_NAMESPACES}><office:body>{body}</office:body>"
            "</office:document-content>",
            compress_type=zipfile.ZIP_DEFLATED,
        )
    return buffer.getvalue()


def as_document(title, paragraphs):
    """The text as an OpenDocument text file: a heading, then its paragraphs."""
    lines = [f'<text:h text:outline-level="1">{escape(title)}</text:h>']
    lines += [f"<text:p>{escape(paragraph)}</text:p>" for paragraph in paragraphs]
    return _opendocument(ODT_MIMETYPE, f"<office:text>{''.join(lines)}</office:text>")


def as_sheet(title, rows, headers=("Extrait", "Contenu")):
    """
    The text as an OpenDocument spreadsheet: one passage per row.

    A table of a document's passages is what an agent actually keeps in a
    sheet ‒ a reference in the first column, the text it points at in the
    second ‒ and it gives the drive a file whose text Tika reads back whole.
    """

    def cell(value):
        return (
            '<table:table-cell office:value-type="string">'
            f"<text:p>{escape(str(value))}</text:p>"
            "</table:table-cell>"
        )

    def row(values):
        return f"<table:table-row>{''.join(cell(value) for value in values)}</table:table-row>"

    body = [row([title]), row(headers), *[row(values) for values in rows]]
    return _opendocument(
        ODS_MIMETYPE,
        "<office:spreadsheet>"
        f'<table:table table:name="{escape(title[:31] or "Extraits")}">{"".join(body)}</table:table>'
        "</office:spreadsheet>",
    )


# Drawing of the page: a scan is a page, not a screenshot, so the proportions
# are those of A4 at 150 dpi and the text is set at a size OCR reads back.
PAGE_WIDTH = 1240
MARGIN = 80
TITLE_SIZE = 34
BODY_SIZE = 25
LINE_HEIGHT = 38
WRAP_COLUMNS = 62


def as_picture(title, paragraphs):
    """
    The text as a PNG page, the way a scanned fiche reaches a drive.

    Set black on white at a readable size: Tika's OCR reads it back word for
    word, accents included, so the file is placed in the graph by what it
    actually says and not by its name.
    """
    title_font = ImageFont.truetype(FONT_BOLD, TITLE_SIZE)
    body_font = ImageFont.truetype(FONT_REGULAR, BODY_SIZE)

    heading = textwrap.wrap(title, width=48) or [""]
    lines = []
    for paragraph in paragraphs:
        lines.extend(textwrap.wrap(paragraph, width=WRAP_COLUMNS))
        lines.append("")
    while lines and not lines[-1]:
        lines.pop()

    top = MARGIN + len(heading) * (TITLE_SIZE + 10) + 30
    height = max(1754, top + len(lines) * LINE_HEIGHT + MARGIN)
    page = Image.new("RGB", (PAGE_WIDTH, height), "white")
    draw = ImageDraw.Draw(page)

    y = MARGIN
    for line in heading:
        draw.text((MARGIN, y), line, font=title_font, fill="#101010")
        y += TITLE_SIZE + 10
    # A rule under the heading, as a printed fiche has.
    y += 12
    draw.line([(MARGIN, y), (PAGE_WIDTH - MARGIN, y)], fill="#101010", width=2)
    y = top
    for line in lines:
        draw.text((MARGIN, y), line, font=body_font, fill="#141414")
        y += LINE_HEIGHT

    buffer = io.BytesIO()
    page.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


def render(kind, title, paragraphs):
    """
    ``(extension, mimetype, bytes)`` for one of "document", "sheet", "picture".

    The sheet numbers the passages it holds, so its first column says where in
    the document each row comes from.
    """
    if kind == "sheet":
        rows = [(f"Extrait {i + 1}", text) for i, text in enumerate(paragraphs)]
        return "ods", ODS_MIMETYPE, as_sheet(title, rows)
    if kind == "picture":
        return "png", PNG_MIMETYPE, as_picture(title, paragraphs)
    return "odt", ODT_MIMETYPE, as_document(title, paragraphs)
