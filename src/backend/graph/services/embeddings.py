"""
Turn passages into vectors (step 3 of the pipeline).

Vectors come from a local embedding server running the model named by
``GRAPH_EMBEDDING_MODEL``; everything stays on our servers. Two servers are
supported, chosen by ``GRAPH_EMBEDDING_BACKEND``:

- ``tei``: text-embeddings-inference, fast on x86 servers (production);
- ``ollama``: runs everywhere including Apple silicon (local development).

Both clients batch, retry, normalize vectors to unit length and check the
dimension so a model change never silently writes vectors of the wrong size.
"""

import logging
import math
import time

from django.conf import settings

import requests

logger = logging.getLogger(__name__)

RETRYABLE_STATUSES = (413, 429, 503)


class EmbeddingError(Exception):
    """The embedding service failed."""


def normalize(vector):
    """Scale a vector to unit length so cosine similarity is a dot product."""
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector] if norm else list(vector)


class BaseEmbedder:
    """Shared batching, retries and checks; subclasses talk to one server."""

    def __init__(self, *, url=None, dimension=None, batch_size=None, timeout=None):
        self.url = (url or settings.GRAPH_EMBEDDING_URL).rstrip("/")
        self.model = settings.GRAPH_EMBEDDING_MODEL
        self.dimension = dimension or settings.GRAPH_EMBEDDING_DIM
        self.batch_size = batch_size or settings.GRAPH_EMBEDDING_BATCH_SIZE
        self.timeout = timeout or settings.GRAPH_EMBEDDING_TIMEOUT
        self.passage_prefix = settings.GRAPH_EMBEDDING_PASSAGE_PREFIX
        self.query_prefix = settings.GRAPH_EMBEDDING_QUERY_PREFIX

    def _request(self, method, path, **kwargs):
        """One HTTP call with retries on transient failures."""
        last_error = None
        for attempt in range(3):
            try:
                response = requests.request(
                    method, f"{self.url}{path}", timeout=self.timeout, **kwargs
                )
            except requests.RequestException as exc:
                last_error = f"embedding service unreachable: {exc}"
            else:
                if response.ok:
                    return response.json()
                last_error = (
                    f"embedding service answered {response.status_code}: {response.text[:200]}"
                )
                if response.status_code not in RETRYABLE_STATUSES:
                    break
            time.sleep(1.5 * (attempt + 1))
        raise EmbeddingError(last_error)

    def _embed_batch(self, texts):
        """Return one vector per text; implemented per server."""
        raise NotImplementedError

    def embed(self, texts, prefix=""):
        """Return one unit vector per text, in the same order."""
        texts = list(texts)
        if not texts:
            return []
        vectors = []
        for start in range(0, len(texts), self.batch_size):
            batch = [f"{prefix}{text}" for text in texts[start : start + self.batch_size]]
            vectors.extend(normalize(vector) for vector in self._embed_batch(batch))
        for vector in vectors:
            if len(vector) != self.dimension:
                raise EmbeddingError(
                    f"model returned {len(vector)} dimensions, "
                    f"GRAPH_EMBEDDING_DIM is {self.dimension}"
                )
        return vectors

    def embed_passages(self, texts):
        """Vectors for stored passages (some models want a prefix here)."""
        return self.embed(texts, prefix=self.passage_prefix)

    def embed_query(self, text):
        """Vector for a search query, comparable to passage vectors."""
        return self.embed([text], prefix=self.query_prefix)[0]

    def info(self):
        """Model loaded by the server, to check it matches the settings."""
        raise NotImplementedError


class TEIEmbedder(BaseEmbedder):
    """Client of a text-embeddings-inference server."""

    def _embed_batch(self, texts):
        return self._request(
            "POST", "/embed", json={"inputs": texts, "normalize": True, "truncate": True}
        )

    def info(self):
        data = self._request("GET", "/info")
        return {"model_id": data.get("model_id"), "backend": "tei"}


class OllamaEmbedder(BaseEmbedder):
    """Client of an Ollama server (POST /api/embed)."""

    def _embed_batch(self, texts):
        data = self._request("POST", "/api/embed", json={"model": self.model, "input": texts})
        return data.get("embeddings", [])

    def info(self):
        data = self._request("POST", "/api/show", json={"model": self.model})
        details = data.get("details", {})
        return {"model_id": self.model, "backend": "ollama", "family": details.get("family")}


EMBEDDERS = {"tei": TEIEmbedder, "ollama": OllamaEmbedder}


def get_embedder():
    """The embedder configured for the project."""
    backend = settings.GRAPH_EMBEDDING_BACKEND
    try:
        return EMBEDDERS[backend]()
    except KeyError as exc:
        raise EmbeddingError(
            f"Unknown GRAPH_EMBEDDING_BACKEND {backend!r}, expected one of {sorted(EMBEDDERS)}"
        ) from exc
