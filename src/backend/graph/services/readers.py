"""
Read the text of a document, in this process, without a server for it.

The other side of ``documents.py``, which writes them.

Office files are ZIP archives holding XML, and that is all they are: a .docx
is ``word/document.xml``, an .odt is ``content.xml``. Reading them takes the
standard library ‒ ``zipfile`` and an XML parser ‒ and a PDF with a text layer
takes one small library. This used to go through an Apache Tika server: two
gigabytes of Java resident next to the application, an HTTP hop on every file,
and a failure mode of its own.

What is not here is what a parser cannot do alone: an image, or a PDF that is
a photograph of a page, hold no text to read. Those go to Albert, which is
already the project's model provider.
"""

import io
import logging
import zipfile

import pypdfium2 as pdfium
from defusedxml.ElementTree import ParseError, fromstring
from pypdf import PdfReader
from pypdf.errors import PyPdfError

logger = logging.getLogger(__name__)


class UnsupportedDocument(Exception):
    """This format is not read here; the caller decides what to do about it."""


# OOXML (Microsoft) and ODF (OpenDocument) both nest their text in elements
# whose local name says where a line ends. Anything else is markup.
BREAKS = {
    "p",  # a paragraph, in both families
    "h",  # an ODF heading
    "br",
    "tab",
    "tr",  # a table row
    "si",  # a shared string of a spreadsheet
    "note",
}

# Where the text of each family lives inside the archive. Prefixes, because a
# presentation holds one part per slide and a workbook one per sheet.
PARTS = {
    "ooxml": ("word/document.xml", "ppt/slides/slide", "xl/sharedStrings.xml"),
    "odf": ("content.xml",),
}

# A part of an office file is text: past this it is a zip bomb, not a document.
MAX_PART_BYTES = 64 * 1024 * 1024
# Under this many words per page, a PDF is a photograph of a document rather
# than a document: measured on a real drive, a scan of forty pages came back
# with two hundred words where its text held five thousand seven hundred.
SCAN_WORDS_PER_PAGE = 10
# How many pages of a scan are read: enough to say what it is about, not so
# many that one file holds up a burst of uploads.
PAGES_SCANNED = 20
# How large the page is drawn before being read. Tesseract wants around 300
# dots per inch; a PDF point is 1/72 inch, so three times over is 216.
SCAN_SCALE = 3


def family_of(mimetype):
    """Which reader a mimetype belongs to, or None when none does."""
    mimetype = mimetype or ""
    if mimetype.startswith("application/vnd.openxmlformats-officedocument."):
        return "ooxml"
    if mimetype.startswith("application/vnd.oasis.opendocument."):
        return "odf"
    if mimetype == "application/pdf":
        return "pdf"
    return None


def text_of_xml(raw):
    """
    The readable text of an office XML part, one line per paragraph.

    ``defusedxml`` and not the plain parser: these files come from whoever
    uploaded them, and an XML entity bomb is three lines long.
    """
    try:
        root = fromstring(raw)
    except (ParseError, ValueError) as exc:
        raise UnsupportedDocument(f"unreadable XML part: {exc}") from exc

    pieces = []

    def walk(element, depth):
        # Deep nesting is a crafted file, not a document; stop rather than
        # hit the interpreter's own limit.
        if depth > 100:
            return
        if element.text:
            pieces.append(element.text)
        for child in element:
            walk(child, depth + 1)
        if element.tag.rsplit("}", 1)[-1] in BREAKS:
            pieces.append("\n")
        if element.tail:
            pieces.append(element.tail)

    walk(root, 0)
    return "".join(pieces)


def read_office(stream, family):
    """The text of every part of an office file, in the order of the archive."""
    try:
        archive = zipfile.ZipFile(stream)
    except (zipfile.BadZipFile, OSError) as exc:
        raise UnsupportedDocument(f"not a readable archive: {exc}") from exc

    wanted = PARTS[family]
    texts = []
    with archive:
        for info in sorted(archive.infolist(), key=lambda i: i.filename):
            if not info.filename.startswith(wanted):
                continue
            if info.file_size > MAX_PART_BYTES:
                logger.warning("Skipped %s: %d bytes once unpacked", info.filename, info.file_size)
                continue
            with archive.open(info) as part:
                texts.append(text_of_xml(part.read(MAX_PART_BYTES)))
    if not texts:
        raise UnsupportedDocument("no readable part in the archive")
    return "\n\n".join(text for text in texts if text.strip())


def read_pdf(stream):
    """
    The text layer of a PDF, one block per page, and what its fields hold.

    A form carries its text in its fields, not in the page: the character
    sheets of a role-playing drive came back with eight words out of eight
    hundred until they were read too.

    A scanned PDF has neither: it comes back empty, and the caller sends it to
    be looked at rather than read.
    """
    try:
        reader = PdfReader(stream)
        pages = [page.extract_text() or "" for page in reader.pages]
        blocks = [page for page in pages if page.strip()]
        blocks.extend(filled_fields(reader))
        blocks.extend(xfa_fields(reader))
    except (PyPdfError, ValueError, OSError, RecursionError) as exc:
        raise UnsupportedDocument(f"unreadable PDF: {exc}") from exc
    return "\n\n".join(blocks)


def looks_scanned(text, pages):
    """
    True when a PDF holds too little text for its length to be a document.

    A page photographed rather than written carries no text layer at all, and
    one scanned among ten leaves the others readable: the measure is per page.
    """
    return pages > 0 and len(text.split()) < SCAN_WORDS_PER_PAGE * pages


def page_count(stream):
    """How many pages a PDF has, or 0 when it cannot be opened."""
    try:
        stream.seek(0)
        return len(PdfReader(stream).pages)
    except (PyPdfError, ValueError, OSError):
        return 0


def drawn_pages(stream, limit=PAGES_SCANNED):
    """
    The first pages of a PDF drawn as pictures, in PNG, ready to be read.

    Taking the pictures a PDF holds is not enough: a page photographed can be
    stored in an encoding nothing here decodes, and on a real scan of
    sixty-eight pages pypdf handed back twenty empty images. Drawing the page
    works whatever is inside it ‒ a photograph, a fax encoding, or lines.
    """
    try:
        stream.seek(0)
        document = pdfium.PdfDocument(stream.read())
    except Exception as exc:  # noqa: BLE001 - PDFium raises its own errors
        logger.info("A PDF could not be drawn: %s", exc)
        return []

    pages = []
    try:
        for number in range(min(len(document), limit)):
            picture = document[number].render(scale=SCAN_SCALE).to_pil()
            buffer = io.BytesIO()
            picture.save(buffer, format="PNG")
            pages.append(buffer.getvalue())
    except Exception as exc:  # noqa: BLE001
        logger.info("A page could not be drawn: %s", exc)
    finally:
        document.close()
    return pages


def xfa_fields(reader):
    """
    What was written in an XFA form, the other way Acrobat stores a form.

    A sheet of this kind came back with twenty-eight words out of seven
    hundred and fifty: everything it holds is in this XML, not in the page.
    """
    try:
        xfa = reader.xfa
    except (PyPdfError, ValueError, KeyError, AttributeError):
        return []
    if not xfa:
        return []
    texts = []
    for part in xfa.values():
        # The parts come either as a stream object or as the bytes themselves.
        reader = getattr(part, "get_data", None)
        raw = reader() if callable(reader) else part
        if isinstance(raw, bytes):
            try:
                texts.append(text_of_xml(raw))
            except UnsupportedDocument:
                continue
    joined = "\n".join(text.strip() for text in texts if text.strip())
    return [joined] if joined else []


def filled_fields(reader):
    """What someone wrote in the fields of a form: "label: value", one per line."""
    try:
        fields = reader.get_fields() or {}
    except (PyPdfError, ValueError, KeyError, AttributeError) as exc:
        logger.info("Could not read the fields of a PDF: %s", exc)
        return []

    lines = []
    for name, field in fields.items():
        value = field.get("/V") if hasattr(field, "get") else None
        if value is None:
            continue
        # A checkbox says /Off when nobody ticked it; a text field says "".
        written = str(value).strip().lstrip("/")
        if not written or written.lower() == "off":
            continue
        label = str(name).strip()
        lines.append(f"{label}: {written}" if label else written)
    return ["\n".join(lines)] if lines else []


def read_document(stream, mimetype):
    """
    The text of a document, or raise UnsupportedDocument.

    ``stream`` is an open binary file, read from the start.
    """
    family = family_of(mimetype)
    if family is None:
        raise UnsupportedDocument(f"no reader for {mimetype or 'an unknown type'}")
    stream.seek(0)
    if family == "pdf":
        return read_pdf(stream)
    return read_office(stream, family)
