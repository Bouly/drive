"""
Client of the Albert API (DINUM), used to seed the graph with public data.

Albert keeps its own vectors; what it gives us is documents already
extracted and cut into chunks (``/v1/documents``, ``/v1/documents/{id}/chunks``)
and an embeddings endpoint (``/v1/embeddings``) running the same model as
our storage (bge-m3, 1024 dimensions, unit vectors).
"""

import math

from django.conf import settings

import requests


class AlbertError(Exception):
    """The Albert API refused or failed a request."""


class AlbertClient:
    """Minimal HTTP client; every method raises AlbertError on failure."""

    def __init__(self, api_key=None, base_url=None, model=None, timeout=60):
        self.api_key = api_key or settings.GRAPH_ALBERT_API_KEY
        if not self.api_key:
            raise AlbertError("ALBERT_API_KEY is not set")
        self.base_url = (base_url or settings.GRAPH_ALBERT_URL).rstrip("/")
        self.model = model or settings.GRAPH_ALBERT_MODEL
        self.timeout = timeout

    def _request(self, method, path, **kwargs):
        try:
            response = requests.request(
                method,
                f"{self.base_url}{path}",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise AlbertError(f"Albert unreachable: {exc}") from exc
        if not response.ok:
            raise AlbertError(
                f"Albert answered {response.status_code} on {path}: {response.text[:200]}"
            )
        return response.json()

    def collections(self, visibility="public"):
        """Public (or private) collections, as dicts with id, name, documents."""
        return self._request(
            "GET", "/collections", params={"visibility": visibility, "limit": 100}
        )["data"]

    def documents(self, collection_id, limit=50, offset=0):
        """Documents of a collection: id, name, chunks (count), size."""
        params = {"collection_id": collection_id, "limit": limit, "offset": offset}
        return self._request("GET", "/documents", params=params)["data"]

    def chunks(self, document_id):
        """All chunks of a document, in order: content and metadata."""
        chunks = []
        offset = 0
        while True:
            page = self._request(
                "GET", f"/documents/{document_id}/chunks", params={"limit": 100, "offset": offset}
            )["data"]
            chunks.extend(page)
            if len(page) < 100:
                return chunks
            offset += 100

    def embed(self, texts, batch_size=32):
        """Unit vectors for the texts, in the same order, sized GRAPH_EMBEDDING_DIM."""
        texts = list(texts)
        vectors = []
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            data = self._request("POST", "/embeddings", json={"model": self.model, "input": batch})
            vectors.extend(
                item["embedding"] for item in sorted(data["data"], key=lambda x: x["index"])
            )
        for vector in vectors:
            if len(vector) != settings.GRAPH_EMBEDDING_DIM:
                raise AlbertError(
                    f"Albert returned {len(vector)} dimensions, storage expects "
                    f"{settings.GRAPH_EMBEDDING_DIM}"
                )
            norm = math.sqrt(sum(x * x for x in vector))
            if abs(norm - 1) > 1e-3:
                raise AlbertError("Albert returned a vector that is not unit length")
        return vectors

    def chat(self, prompt, max_tokens=40):
        """The answer of the chat model to a single user prompt, stripped."""
        data = self._request(
            "POST",
            "/chat/completions",
            json={
                "model": settings.GRAPH_ALBERT_CHAT_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0.2,
            },
        )
        try:
            return (data["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError) as exc:
            raise AlbertError("Albert returned no chat answer") from exc
