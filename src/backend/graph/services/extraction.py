"""
Extract the text of an item's file (step 1 of the pipeline).

Plain text files are read directly; everything else (docx, odt, pdf, pptx,
xlsx, images with OCR...) goes through an Apache Tika server, which returns
the text of any format it knows.
"""

import logging

from django.conf import settings
from django.core.files.storage import default_storage

import requests

from core import models

logger = logging.getLogger(__name__)


class ExtractionError(Exception):
    """The text of a file could not be extracted."""


class ExtractionSkipped(ExtractionError):
    """The file is not eligible: wrong type, too big, not uploaded yet."""


def is_extractable(item):
    """Return True if the item is a ready file whose type and size we accept."""
    if item.type != models.ItemTypeChoices.FILE:
        return False
    if item.upload_state != models.ItemUploadStateChoices.READY:
        return False
    if (item.size or 0) > settings.GRAPH_MAX_FILE_SIZE:
        return False
    mimetype = item.mimetype or ""
    return any(mimetype.startswith(prefix) for prefix in settings.GRAPH_ALLOWED_MIMETYPES)


class TikaExtractor:
    """Text extraction through the Apache Tika REST server."""

    def __init__(self, url=None, timeout=None):
        self.url = (url or settings.GRAPH_TIKA_URL).rstrip("/")
        self.timeout = timeout or settings.GRAPH_TIKA_TIMEOUT

    def extract(self, content, mimetype=None, filename=None):
        """Send raw bytes to Tika and return the extracted text."""
        headers = {"Accept": "text/plain; charset=UTF-8"}
        if mimetype:
            headers["Content-Type"] = mimetype
        if filename:
            headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        # OCR applies to images and scanned PDFs: French first, English as backup.
        headers["X-Tika-OCRLanguage"] = settings.GRAPH_OCR_LANGUAGES
        headers["X-Tika-PDFextractInlineImages"] = "false"

        try:
            response = requests.put(
                f"{self.url}/tika", data=content, headers=headers, timeout=self.timeout
            )
        except requests.RequestException as exc:
            raise ExtractionError(f"Tika is unreachable: {exc}") from exc
        if response.status_code == 422:
            raise ExtractionError("Tika cannot parse this file (encrypted or corrupted)")
        if not response.ok:
            raise ExtractionError(f"Tika answered {response.status_code}")
        response.encoding = "utf-8"
        return response.text


def extract_text(item, extractor=None):
    """
    Return the text of an item's file.

    Raises ExtractionSkipped when the item is not eligible and ExtractionError
    when the extraction itself fails.
    """
    if not is_extractable(item):
        raise ExtractionSkipped(f"Item {item.id} is not extractable")

    mimetype = item.mimetype or ""
    with default_storage.open(item.file_key, "rb") as fd:
        content = fd.read()

    if mimetype.startswith("text/"):
        return content.decode("utf-8", errors="replace")

    extractor = extractor or TikaExtractor()
    text = extractor.extract(content, mimetype=mimetype, filename=item.filename)
    logger.info("Extracted %d characters from item %s (%s)", len(text), item.id, mimetype)
    return text
