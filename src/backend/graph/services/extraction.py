"""
Extract the text of an item's file (step 1 of the pipeline).

Plain text files are read directly; everything else (docx, odt, pdf, pptx,
xlsx, images with OCR, videos, audio...) goes through an Apache Tika server,
which returns the text of any format it knows. For videos and audio Tika only
finds the metadata (title, comment): the speech is transcribed by Albert from
the audio track, cut into segments by ffmpeg and sent side by side.

A file is copied block by block to a temporary file rather than loaded in
memory: a video can weigh gigabytes.
"""

import logging
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

from django.conf import settings
from django.core.files.storage import default_storage

import requests

from core import models

from graph.services.albert import AlbertClient, AlbertError

logger = logging.getLogger(__name__)

MEDIA_PREFIXES = ("video/", "audio/")
# Bytes read at once when copying a file out of object storage.
COPY_BLOCK_SIZE = 8 * 1024 * 1024


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


def is_media(mimetype):
    """Videos and audio: their text is mostly speech."""
    return (mimetype or "").startswith(MEDIA_PREFIXES)


class TikaExtractor:
    """Text extraction through the Apache Tika REST server."""

    def __init__(self, url=None, timeout=None):
        self.url = (url or settings.GRAPH_TIKA_URL).rstrip("/")
        self.timeout = timeout or settings.GRAPH_TIKA_TIMEOUT

    def extract(self, content, mimetype=None, filename=None):
        """Send raw bytes or an open binary file (streamed) to Tika, return the text."""
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


def _run(command):
    """Run ffmpeg or ffprobe and return its output; ExtractionError when it fails."""
    try:
        result = subprocess.run(  # noqa: S603 - fixed program, arguments are not a shell
            command,
            capture_output=True,
            text=True,
            timeout=settings.GRAPH_FFMPEG_TIMEOUT,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ExtractionError(f"{command[0]} is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise ExtractionError(f"{command[0]} took too long") from exc
    if result.returncode != 0:
        raise ExtractionError(f"{command[0]} failed: {result.stderr.strip()[-300:]}")
    return result.stdout


class Transcriber:
    """The speech of a video or audio file, through ffmpeg and Albert."""

    def __init__(self, client=None, segment_seconds=None, workers=None):
        self.client = client or AlbertClient()
        self.segment_seconds = segment_seconds or settings.GRAPH_TRANSCRIPTION_SEGMENT_SECONDS
        self.workers = workers or settings.GRAPH_TRANSCRIPTION_WORKERS

    @staticmethod
    def has_audio(path):
        """True if the file has at least one audio stream."""
        output = _run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a",
                "-show_entries",
                "stream=index",
                "-of",
                "csv=p=0",
                path,
            ]
        )
        return bool(output.strip())

    def split(self, path, directory):
        """
        The audio track as small mp3 segments, in order: mono, 16 kHz, 32 kbit/s
        (what speech recognition needs), about 2.4 MB per 10 minutes.
        """
        _run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-i",
                path,
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "32k",
                "-f",
                "segment",
                "-segment_time",
                str(self.segment_seconds),
                "-reset_timestamps",
                "1",
                str(Path(directory) / "part%05d.mp3"),
            ]
        )
        return sorted(Path(directory).glob("part*.mp3"))

    def _transcribe_segment(self, segment):
        with segment.open("rb") as audio:
            return self.client.transcribe(audio, segment.name)

    def transcribe(self, path):
        """The text spoken in the file, empty when it has no audio track."""
        if not self.has_audio(path):
            return ""
        with tempfile.TemporaryDirectory() as directory:
            segments = self.split(path, directory)
            if not segments:
                return ""
            with ThreadPoolExecutor(max_workers=min(self.workers, len(segments))) as pool:
                texts = list(pool.map(self._transcribe_segment, segments))
        logger.info("Transcribed %d audio segments of %s", len(segments), path)
        return "\n\n".join(text for text in texts if text)


@contextmanager
def local_copy(item):
    """The item's file, copied block by block to a temporary file removed afterwards."""
    with tempfile.NamedTemporaryFile(suffix=Path(item.filename or "").suffix) as copy:
        with default_storage.open(item.file_key, "rb") as source:
            for block in iter(lambda: source.read(COPY_BLOCK_SIZE), b""):
                copy.write(block)
        copy.flush()
        yield copy.name


def _media_metadata(path, item, extractor):
    """Title, comment... found by Tika; a failure here must not lose the speech."""
    try:
        with open(path, "rb") as content:
            return extractor.extract(content, mimetype=item.mimetype, filename=item.filename)
    except ExtractionError as exc:
        logger.warning("Tika could not read the metadata of item %s: %s", item.id, exc)
        return ""


def _seconds_in(path):
    """Half the length of the file, in seconds, or 1 when it cannot be read."""
    try:
        output = _run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path]
        )
        return max(1.0, float(output.strip()) / 2)
    except (ExtractionError, ValueError):
        return 1.0


def describe_frame(path, item, client=None):
    """
    What is seen in the middle of a silent video, in one sentence, or "".

    A video that says nothing is nothing to the graph: the bee video of the
    demo drive carried its filename and "Sous-titrage ST' 501", so it sat
    among files it had no relation to. One frame, read by the same vision
    model as a photo, gives it its subject back.
    """
    with tempfile.NamedTemporaryFile(suffix=".jpg") as frame:
        try:
            _run(
                [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    str(_seconds_in(path)),
                    "-i",
                    path,
                    "-frames:v",
                    "1",
                    # Wide enough for the model to read the scene, small
                    # enough to travel in the request.
                    "-vf",
                    "scale=768:-2",
                    frame.name,
                ]
            )
            raw = Path(frame.name).read_bytes()
        except (ExtractionError, OSError) as exc:
            logger.warning("No frame taken from item %s: %s", item.id, exc)
            return ""
        if not raw:
            return ""
        try:
            return (client or AlbertClient()).describe_image(raw, "image/jpeg")
        except AlbertError as exc:
            logger.warning("Albert could not describe the frame of item %s: %s", item.id, exc)
            return ""


def _extract_media(path, item, extractor, transcriber):
    """Metadata from Tika and speech from Albert, obtained side by side."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        metadata = pool.submit(_media_metadata, path, item, extractor)
        speech = pool.submit(transcriber.transcribe, path)
        parts = [metadata.result().strip(), speech.result().strip()]
    if not parts[1] and (item.mimetype or "").startswith("video/"):
        # Nobody speaks: what the video shows is all it has to say. The
        # description comes first, so it survives the cap on long texts.
        parts.insert(0, describe_frame(path, item, getattr(transcriber, "client", None)))
    return "\n\n".join(part for part in parts if part)


def _cap(text, item):
    """Keep at most GRAPH_MAX_TEXT_CHARS characters of a text."""
    limit = settings.GRAPH_MAX_TEXT_CHARS
    if len(text) <= limit:
        return text
    logger.warning("Item %s: text cut from %d to %d characters", item.id, len(text), limit)
    return text[:limit]


def extract_text(item, extractor=None, transcriber=None):
    """
    Return the text of an item's file.

    Raises ExtractionSkipped when the item is not eligible and ExtractionError
    when the extraction itself fails.
    """
    if not is_extractable(item):
        raise ExtractionSkipped(f"Item {item.id} is not extractable")

    mimetype = item.mimetype or ""
    if mimetype.startswith("text/"):
        # A UTF-8 character is at most 4 bytes: no need to read further.
        with default_storage.open(item.file_key, "rb") as source:
            content = source.read(settings.GRAPH_MAX_TEXT_CHARS * 4)
        return _cap(content.decode("utf-8", errors="replace"), item)

    extractor = extractor or TikaExtractor()
    with local_copy(item) as path:
        if is_media(mimetype):
            text = _extract_media(path, item, extractor, transcriber or Transcriber())
        else:
            with open(path, "rb") as content:
                text = extractor.extract(content, mimetype=mimetype, filename=item.filename)
    logger.info("Extracted %d characters from item %s (%s)", len(text), item.id, mimetype)
    return _cap(text, item)
