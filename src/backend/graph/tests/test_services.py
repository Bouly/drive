"""Tests for the extraction and embedding services (HTTP mocked)."""

from unittest import mock

import pytest
import responses

from core import factories, models

from graph.pipeline import prepare_item
from graph.services.embeddings import EmbeddingError, OllamaEmbedder, TEIEmbedder, get_embedder
from graph.services.extraction import (
    ExtractionError,
    ExtractionSkipped,
    TikaExtractor,
    extract_text,
    is_extractable,
)

pytestmark = pytest.mark.django_db

TIKA = "http://tika:9998"
TEI = "http://embeddings:80"


def make_file(mimetype="application/pdf", size=1000, state=models.ItemUploadStateChoices.READY):
    """A file item in the given upload state (ready by default)."""
    return factories.ItemFactory(
        type=models.ItemTypeChoices.FILE,
        update_upload_state=state,
        mimetype=mimetype,
        size=size,
    )


def test_is_extractable_rules(settings):
    """Only ready files of an allowed type and size are extracted."""
    settings.GRAPH_ALLOWED_MIMETYPES = ["application/pdf", "text/"]
    settings.GRAPH_MAX_FILE_SIZE = 5000
    assert is_extractable(make_file())
    assert is_extractable(make_file(mimetype="text/plain"))
    assert not is_extractable(make_file(mimetype="video/mp4"))
    assert not is_extractable(make_file(size=10_000))
    assert not is_extractable(make_file(state=models.ItemUploadStateChoices.PENDING))
    assert not is_extractable(factories.ItemFactory(type=models.ItemTypeChoices.FOLDER))


@responses.activate
def test_tika_extractor_sends_bytes_and_returns_text(settings):
    """The extractor PUTs the file to Tika with its mimetype and reads plain text."""
    settings.GRAPH_TIKA_URL = TIKA
    responses.put(f"{TIKA}/tika", body="Bonjour  le monde", status=200)
    text = TikaExtractor().extract(b"%PDF-1.4", mimetype="application/pdf", filename="a.pdf")
    assert text == "Bonjour  le monde"
    request = responses.calls[0].request
    assert request.headers["Content-Type"] == "application/pdf"
    assert request.headers["Accept"].startswith("text/plain")
    assert request.body == b"%PDF-1.4"


@responses.activate
def test_tika_extractor_errors(settings):
    """Unparsable files and server errors raise ExtractionError."""
    settings.GRAPH_TIKA_URL = TIKA
    responses.put(f"{TIKA}/tika", status=422)
    with pytest.raises(ExtractionError, match="cannot parse"):
        TikaExtractor().extract(b"x", mimetype="application/pdf")
    responses.put(f"{TIKA}/tika", status=500)
    with pytest.raises(ExtractionError, match="500"):
        TikaExtractor().extract(b"x", mimetype="application/pdf")


def test_extract_text_reads_plain_text_without_tika(settings):
    """text/* files are decoded straight from storage."""
    settings.GRAPH_ALLOWED_MIMETYPES = ["text/"]
    item = make_file(mimetype="text/plain")
    with mock.patch(
        "graph.services.extraction.default_storage.open", mock.mock_open(read_data=b"salut")
    ):
        assert extract_text(item) == "salut"


def test_extract_text_skips_ineligible(settings):
    """A folder or a disallowed type is skipped, not sent to Tika."""
    settings.GRAPH_ALLOWED_MIMETYPES = ["application/pdf"]
    with pytest.raises(ExtractionSkipped):
        extract_text(make_file(mimetype="video/mp4"))


@responses.activate
def test_tei_embedder_batches_and_checks_dimension(settings):
    """Texts are sent in batches and vectors must have the configured size."""
    settings.GRAPH_EMBEDDING_URL = TEI
    settings.GRAPH_EMBEDDING_DIM = 3
    settings.GRAPH_EMBEDDING_BATCH_SIZE = 2
    settings.GRAPH_EMBEDDING_PASSAGE_PREFIX = "passage: "
    responses.post(f"{TEI}/embed", json=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    responses.post(f"{TEI}/embed", json=[[0.0, 0.0, 1.0]])
    vectors = TEIEmbedder().embed_passages(["a", "b", "c"])
    assert vectors == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    assert responses.calls[0].request.body.decode().count("passage: ") == 2

    responses.post(f"{TEI}/embed", json=[[0.1, 0.2]])
    with pytest.raises(EmbeddingError, match="dimensions"):
        TEIEmbedder().embed_passages(["d"])


@responses.activate
def test_tei_embedder_retries_when_busy(settings):
    """A 503 is retried, a 400 is not."""
    settings.GRAPH_EMBEDDING_URL = TEI
    settings.GRAPH_EMBEDDING_DIM = 2
    responses.post(f"{TEI}/embed", status=503)
    responses.post(f"{TEI}/embed", json=[[1.0, 0.0]])
    with mock.patch("graph.services.embeddings.time.sleep"):
        assert TEIEmbedder().embed(["a"]) == [[1.0, 0.0]]

    responses.post(f"{TEI}/embed", status=400, body="bad")
    with (
        mock.patch("graph.services.embeddings.time.sleep"),
        pytest.raises(EmbeddingError, match="400"),
    ):
        TEIEmbedder().embed(["b"])


@responses.activate
def test_prepare_item_end_to_end(settings):
    """Extraction, chunking and embedding chain into chunks with vectors."""
    settings.GRAPH_ALLOWED_MIMETYPES = ["application/pdf"]
    settings.GRAPH_TIKA_URL = TIKA
    settings.GRAPH_EMBEDDING_BACKEND = "tei"
    settings.GRAPH_EMBEDDING_URL = TEI
    settings.GRAPH_EMBEDDING_DIM = 2
    settings.GRAPH_CHUNK_WORDS = 4
    settings.GRAPH_CHUNK_OVERLAP = 1
    responses.put(f"{TIKA}/tika", body="un deux trois quatre cinq six sept")
    responses.post(f"{TEI}/embed", json=[[1.0, 0.0], [0.0, 1.0]])
    item = make_file()
    with mock.patch(
        "graph.services.extraction.default_storage.open", mock.mock_open(read_data=b"%PDF")
    ):
        prepared = prepare_item(item)
    assert [c.text for c in prepared.chunks] == ["un deux trois quatre", "quatre cinq six sept"]
    assert [c.embedding for c in prepared.chunks] == [[1.0, 0.0], [0.0, 1.0]]
    assert not prepared.is_empty


@responses.activate
def test_ollama_embedder_normalizes_and_reports_model(settings):
    """Ollama vectors are scaled to unit length; the backend is picked by setting."""
    settings.GRAPH_EMBEDDING_BACKEND = "ollama"
    settings.GRAPH_EMBEDDING_URL = "http://embeddings:11434"
    settings.GRAPH_EMBEDDING_MODEL = "bge-m3"
    settings.GRAPH_EMBEDDING_DIM = 2
    responses.post("http://embeddings:11434/api/embed", json={"embeddings": [[3.0, 4.0]]})
    responses.post("http://embeddings:11434/api/show", json={"details": {"family": "bert"}})
    embedder = get_embedder()
    assert isinstance(embedder, OllamaEmbedder)
    assert embedder.embed(["a"]) == [[0.6, 0.8]]
    assert responses.calls[0].request.body.decode().count('"model": "bge-m3"') == 1
    assert embedder.info() == {"model_id": "bge-m3", "backend": "ollama", "family": "bert"}


def test_get_embedder_rejects_unknown_backend(settings):
    """A typo in the backend name fails loudly."""
    settings.GRAPH_EMBEDDING_BACKEND = "nope"
    with pytest.raises(EmbeddingError, match="GRAPH_EMBEDDING_BACKEND"):
        get_embedder()
