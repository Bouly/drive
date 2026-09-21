"""Tests for reading a document without a server: office files and PDF."""

import zipfile
from io import BytesIO

import pytest

from graph.services.readers import (
    UnsupportedDocument,
    family_of,
    read_document,
    text_of_xml,
)

OOXML = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PRESENTATION = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
SHEET = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ODT = "application/vnd.oasis.opendocument.text"


def archive(parts):
    """An office file: a ZIP of XML parts, which is all one ever is."""
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as zipped:
        for name, content in parts.items():
            zipped.writestr(name, content)
    buffer.seek(0)
    return buffer


def word(body):
    """A .docx holding one document part."""
    return archive(
        {
            "[Content_Types].xml": "<Types/>",
            "word/document.xml": (
                '<w:document xmlns:w="http://schemas.openxmlformats.org/'
                f'wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>'
            ),
        }
    )


def test_a_word_document_reads_one_line_per_paragraph():
    """The text comes out in reading order, paragraphs apart."""
    body = (
        "<w:p><w:r><w:t>La démission</w:t></w:r><w:r><w:t> met fin au CDI.</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>Le préavis dépend de la convention.</w:t></w:r></w:p>"
    )

    text = read_document(word(body), OOXML)

    assert "La démission met fin au CDI." in text
    assert "Le préavis dépend de la convention." in text
    assert text.index("La démission") < text.index("Le préavis")
    assert "\n" in text.strip()


def test_an_opendocument_reads_the_same_way():
    """ODF nests its text differently and comes out the same."""
    content = (
        '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
        ' xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
        "<office:body><office:text>"
        "<text:h>Note d'arbitrage</text:h>"
        "<text:p>Le budget est arbitré en <text:span>juillet</text:span>.</text:p>"
        "</office:text></office:body></office:document-content>"
    )

    text = read_document(archive({"content.xml": content}), ODT)

    assert "Note d'arbitrage" in text
    assert "Le budget est arbitré en juillet." in text


def test_a_presentation_reads_its_slides_in_order():
    """One part per slide: they must not come back shuffled."""
    slide = (
        '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
        ' xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
        "<p:cSld><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:cSld></p:sld>"
    )
    parts = {
        "ppt/slides/slide1.xml": slide.format(text="Premier écran"),
        "ppt/slides/slide2.xml": slide.format(text="Second écran"),
    }

    text = read_document(archive(parts), PRESENTATION)

    assert text.index("Premier écran") < text.index("Second écran")


def test_a_workbook_reads_the_words_of_its_cells():
    """A spreadsheet keeps its words in one shared table."""
    shared = (
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<si><t>Hébergement</t></si><si><t>Marché public</t></si></sst>"
    )

    text = read_document(archive({"xl/sharedStrings.xml": shared}), SHEET)

    assert "Hébergement" in text
    assert "Marché public" in text


def pdf(text, field=None):
    """
    A one-page PDF holding that line, written by hand rather than mocked.

    ``field`` adds a filled form field, the way a fillable sheet carries its
    text: in the field, not in the page.
    """
    form = b""
    page_extra = b""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        None,
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    if field:
        label, value = field
        objects[0] = b"<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [6 0 R] >> >>"
        objects[2] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents 4 0 R "
            b"/Annots [6 0 R] /Resources << /Font << /F1 5 0 R >> >> >>"
        )
        objects.append(
            b"<< /Type /Annot /Subtype /Widget /FT /Tx /T ("
            + label.encode()
            + b") /V ("
            + value.encode()
            + b") /Rect [20 20 200 40] >>"
        )
    stream = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode()
    objects[3] = (
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
    )

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    start = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n"
    ).encode()
    return BytesIO(bytes(out))


def test_a_pdf_reads_its_text_layer():
    """A PDF written by a word processor carries its text; we read it."""
    text = read_document(pdf("Le preavis depend de la convention"), "application/pdf")

    assert text == "Le preavis depend de la convention"


def test_a_pdf_form_gives_what_was_written_in_its_fields():
    """A fillable sheet carries its text in its fields, not in the page.

    Measured on a real drive: a role-playing character sheet came back with
    eight words out of eight hundred until the fields were read too.
    """
    document = pdf("Fiche de personnage", field=("Metier", "Ranger"))

    text = read_document(document, "application/pdf")

    assert "Fiche de personnage" in text
    assert "Metier: Ranger" in text


def test_a_pdf_without_a_text_layer_comes_back_empty():
    """A scan holds no text: it must come back empty, not raise.

    That is what tells the caller to have the page looked at rather than read.
    """
    writer = pytest.importorskip("pypdf").PdfWriter()
    writer.add_blank_page(width=300, height=300)
    buffer = BytesIO()
    writer.write(buffer)

    assert read_document(buffer, "application/pdf") == ""


def test_a_file_that_is_not_an_archive_is_refused():
    """A truncated or mislabelled file is reported, not crashed on."""
    with pytest.raises(UnsupportedDocument):
        read_document(BytesIO(b"certainly not a zip"), ODT)


def test_an_xml_bomb_is_refused():
    """These files come from whoever uploaded them."""
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
        '<!ENTITY lol2 "&lol;&lol;&lol;">]><lolz>&lol2;</lolz>'
    )
    with pytest.raises(UnsupportedDocument):
        text_of_xml(bomb)


def test_an_unknown_type_has_no_reader():
    """Legacy binary formats are not read here, and say so."""
    assert family_of("application/msword") is None
    with pytest.raises(UnsupportedDocument):
        read_document(BytesIO(b""), "application/msword")
