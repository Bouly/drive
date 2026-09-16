"""Tests for the extraction step: documents through Tika, videos and audio transcribed."""

import shutil
import subprocess
from io import BytesIO
from pathlib import Path
from unittest import mock

from django.core.files.storage import default_storage

import pytest

from core import factories, models

from graph.services import extraction
from graph.services.albert import AlbertClient
from graph.services.extraction import (
    ExtractionError,
    Transcriber,
    extract_text,
    is_extractable,
)

pytestmark = pytest.mark.django_db


def make_file(mimetype, content=b"content", filename="file.bin", size=None):
    """A ready file whose content is in object storage."""
    item = factories.ItemFactory(
        type=models.ItemTypeChoices.FILE,
        filename=filename,
        mimetype=mimetype,
        size=len(content) if size is None else size,
        update_upload_state=models.ItemUploadStateChoices.READY,
    )
    default_storage.save(item.file_key, BytesIO(content))
    return item


class FakeTika:
    """Records what Tika would receive and answers a fixed text."""

    def __init__(self, text="", error=None):
        self.text = text
        self.error = error
        self.received = None

    def extract(self, content, mimetype=None, filename=None):  # pylint: disable=unused-argument
        """Read the streamed file like requests would."""
        self.received = content.read()
        if self.error:
            raise self.error
        return self.text


class FakeTranscriber:
    """Answers a fixed speech and records the file it was given."""

    def __init__(self, text, client=None):
        self.text = text
        self.client = client
        self.received = None

    def transcribe(self, path):
        """Read the local copy of the file."""
        self.received = Path(path).read_bytes()
        return self.text


@pytest.mark.parametrize("mimetype", ["video/mp4", "video/quicktime", "audio/mpeg"])
def test_is_extractable_accepts_videos_and_audio(mimetype):
    """Videos and audio are part of the graph."""
    assert is_extractable(make_file(mimetype))


def test_is_extractable_rejects_files_over_the_limit(settings):
    """A file bigger than GRAPH_MAX_FILE_SIZE is left out."""
    settings.GRAPH_MAX_FILE_SIZE = 100
    assert not is_extractable(make_file("video/mp4", size=101))
    assert is_extractable(make_file("video/mp4", size=100))


def test_extract_text_video_combines_metadata_and_speech():
    """A video gives its Tika metadata then its transcribed speech, from a local copy."""
    item = make_file("video/mp4", b"fake video bytes", filename="reunion.mp4")
    tika = FakeTika("Réunion préavis\nCompte rendu\n")
    transcriber = FakeTranscriber(" Bonjour à tous. ")

    text = extract_text(item, extractor=tika, transcriber=transcriber)

    assert text == "Réunion préavis\nCompte rendu\n\nBonjour à tous."
    assert tika.received == b"fake video bytes"
    assert transcriber.received == b"fake video bytes"


def test_extract_text_video_keeps_the_speech_when_tika_fails():
    """Metadata are a bonus: a Tika error does not lose the transcription."""
    item = make_file("video/mp4")
    tika = FakeTika(error=ExtractionError("Tika answered 500"))

    text = extract_text(item, extractor=tika, transcriber=FakeTranscriber("Bonjour."))

    assert text == "Bonjour."


def test_extract_text_video_transcription_errors_are_raised():
    """Without the speech there is nothing to index: the error reaches the task."""
    item = make_file("video/mp4")
    transcriber = mock.Mock()
    transcriber.transcribe.side_effect = ExtractionError("ffmpeg failed")

    with pytest.raises(ExtractionError):
        extract_text(item, extractor=FakeTika("titre"), transcriber=transcriber)


def test_a_silent_video_is_described_from_one_of_its_frames():
    """A video nobody speaks in is placed by what it shows, like a photo."""
    item = make_file("video/mp4", b"fake video", filename="abeilles.mp4")
    client = mock.Mock()
    client.describe_image.return_value = "Un essaim d'abeilles sur un cadre de ruche."

    def run(command, **kwargs):  # pylint: disable=unused-argument
        if command[0] == "ffprobe":
            return completed("12.0\n")
        Path(command[-1]).write_bytes(b"jpeg bytes")
        return completed()

    with mock.patch("graph.services.extraction.subprocess.run", side_effect=run):
        text = extract_text(
            item, extractor=FakeTika("abeilles"), transcriber=FakeTranscriber("", client=client)
        )

    assert text == "Un essaim d'abeilles sur un cadre de ruche.\n\nabeilles"
    assert client.describe_image.call_args[0][0] == b"jpeg bytes"


def test_a_video_that_speaks_is_not_looked_at():
    """Speech says what the video is about: no frame travels to the model."""
    item = make_file("video/mp4", b"fake video", filename="reunion.mp4")
    client = mock.Mock()
    speech = "Bonjour à tous, voici le compte rendu de la réunion de préavis de ce matin."

    text = extract_text(
        item, extractor=FakeTika(""), transcriber=FakeTranscriber(speech, client=client)
    )

    assert text == speech
    client.describe_image.assert_not_called()


def test_a_video_the_model_only_heard_credits_in_is_looked_at():
    """Three words of subtitling credits are silence, whatever the model says."""
    item = make_file("video/mp4", b"fake video", filename="abeilles.mp4")
    client = mock.Mock()
    client.describe_image.return_value = "Un essaim d'abeilles."

    def run(command, **kwargs):  # pylint: disable=unused-argument
        if command[0] == "ffprobe":
            return completed("8.0\n")
        Path(command[-1]).write_bytes(b"jpeg bytes")
        return completed()

    with mock.patch("graph.services.extraction.subprocess.run", side_effect=run):
        text = extract_text(
            item,
            extractor=FakeTika(""),
            transcriber=FakeTranscriber("Sous-titrage ST' 501", client=client),
        )

    assert text == "Un essaim d'abeilles.\n\nSous-titrage ST' 501"


def test_extract_text_document_streams_the_file_to_tika():
    """A document is sent to Tika as a file, not loaded in memory first."""
    item = make_file("application/pdf", b"%PDF-1.7 fake", filename="doc.pdf")
    tika = FakeTika("Le texte du PDF")

    assert extract_text(item, extractor=tika) == "Le texte du PDF"
    assert tika.received == b"%PDF-1.7 fake"


def test_extract_text_caps_long_texts(settings):
    """Only GRAPH_MAX_TEXT_CHARS characters are kept."""
    settings.GRAPH_MAX_TEXT_CHARS = 10
    item = make_file("text/plain", ("é" * 50).encode())

    assert extract_text(item) == "é" * 10


def completed(stdout=""):
    """A successful subprocess result."""
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def test_transcriber_skips_files_without_audio():
    """A silent video costs no call to Albert."""
    client = mock.Mock()
    with mock.patch("graph.services.extraction.subprocess.run", return_value=completed("")):
        assert Transcriber(client=client).transcribe("/tmp/video.mp4") == ""
    client.transcribe.assert_not_called()


def test_transcriber_transcribes_segments_in_order():
    """Segments are transcribed in parallel and joined in their order."""

    def run(command, **kwargs):
        if command[0] == "ffprobe":
            return completed("1\n")
        directory = Path(command[-1]).parent
        for i in range(5):
            (directory / f"part{i:05d}.mp3").write_bytes(f"segment {i}".encode())
        return completed()

    client = mock.Mock()
    client.transcribe.side_effect = lambda audio, name: audio.read().decode().upper()

    with mock.patch("graph.services.extraction.subprocess.run", side_effect=run):
        text = Transcriber(client=client, workers=3).transcribe("/tmp/video.mp4")

    assert text == "\n\n".join(f"SEGMENT {i}" for i in range(5))
    assert client.transcribe.call_count == 5


def test_transcriber_without_ffmpeg():
    """A missing ffmpeg is an extraction error, not a crash."""
    with (
        mock.patch("graph.services.extraction.subprocess.run", side_effect=FileNotFoundError),
        pytest.raises(ExtractionError, match="ffprobe is not installed"),
    ):
        Transcriber(client=mock.Mock()).transcribe("/tmp/video.mp4")


def test_transcriber_ffmpeg_failure():
    """ffmpeg's error message is kept."""
    failure = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="Invalid data")
    with (
        mock.patch("graph.services.extraction.subprocess.run", return_value=failure),
        pytest.raises(ExtractionError, match="Invalid data"),
    ):
        Transcriber(client=mock.Mock()).transcribe("/tmp/video.mp4")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_transcriber_splits_a_real_audio_track(tmp_path):
    """With the real ffmpeg: a 2.5 s tone cut every second gives 3 segments."""
    source = tmp_path / "tone.mp4"
    # Not a whole number of seconds: AAC pads the track by a few milliseconds.
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=duration=2.5", str(source)],
        check=True,
    )
    client = mock.Mock()
    client.transcribe.side_effect = lambda audio, name: name

    text = Transcriber(client=client, segment_seconds=1).transcribe(str(source))

    assert text.split("\n\n") == ["part00000.mp3", "part00001.mp3", "part00002.mp3"]


def test_albert_transcribe_sends_the_audio(settings):
    """The audio goes as a multipart file with the speech model and the language."""
    settings.GRAPH_ALBERT_AUDIO_MODEL = "openweight-audio"
    settings.GRAPH_TRANSCRIPTION_LANGUAGE = "fr"
    response = mock.Mock(ok=True)
    response.json.return_value = {"text": " Bonjour. "}
    audio = BytesIO(b"mp3")

    with mock.patch("graph.services.albert.requests.request", return_value=response) as request:
        text = AlbertClient(api_key="key").transcribe(audio, "part00000.mp3")

    assert text == "Bonjour."
    kwargs = request.call_args.kwargs
    assert request.call_args.args[1].endswith("/audio/transcriptions")
    # Without the content type, Albert answers 500.
    assert kwargs["files"] == {"file": ("part00000.mp3", audio, "audio/mpeg")}
    assert kwargs["data"] == {
        "model": "openweight-audio",
        "response_format": "json",
        "language": "fr",
    }
    assert kwargs["timeout"] == settings.GRAPH_TRANSCRIPTION_TIMEOUT


def test_media_prefixes_cover_video_and_audio():
    """Only videos and audio go through the transcription."""
    assert extraction.is_media("video/webm")
    assert extraction.is_media("audio/ogg")
    assert not extraction.is_media("application/pdf")
