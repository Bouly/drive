"""
Client of the Albert API (DINUM), used to seed the graph with public data.

Albert keeps its own vectors; what it gives us is documents already
extracted and cut into chunks (``/v1/documents``, ``/v1/documents/{id}/chunks``)
and an embeddings endpoint (``/v1/embeddings``) running the same model as
our storage (bge-m3, 1024 dimensions, unit vectors).
"""

import base64
import math
import mimetypes

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

    def _request(self, method, path, timeout=None, **kwargs):
        try:
            response = requests.request(
                method,
                f"{self.base_url}{path}",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=timeout or self.timeout,
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

    def documents(self, collection_id, limit=50, offset=0, page_size=10):
        """
        Documents of a collection: id, name, chunks (count), size.

        Asked page by page, as the chunks are. The API answers a page at a
        time whatever ``limit`` says ‒ mediatech-legifrance handed back six
        documents to a request for forty-five, and a bank meant to draw half
        of itself from that collection came out with six.
        """
        documents = []
        while len(documents) < limit:
            page = self._request(
                "GET",
                "/documents",
                params={
                    "collection_id": collection_id,
                    "limit": min(page_size, limit - len(documents)),
                    "offset": offset + len(documents),
                },
            )["data"]
            documents.extend(page)
            # A short page is the last one: asking again answers the same.
            if len(page) < page_size:
                break
        return documents[:limit]

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

    def chat(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self, prompt, max_tokens=40, image=None, model=None, temperature=0.2
    ):
        """
        The answer of the chat model to a single user prompt, stripped.

        ``image`` is an ``(bytes, mimetype)`` pair sent along with the prompt,
        for the models that can look at a picture.
        """
        content = prompt
        if image is not None:
            raw, mimetype = image
            encoded = base64.b64encode(raw).decode()
            content = [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mimetype};base64,{encoded}"},
                },
            ]
        data = self._request(
            "POST",
            "/chat/completions",
            json={
                "model": model or settings.GRAPH_ALBERT_CHAT_MODEL,
                "messages": [{"role": "user", "content": content}],
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
        )
        try:
            return (data["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError) as exc:
            raise AlbertError("Albert returned no chat answer") from exc

    def rerank(self, query, documents):
        """
        How relevant each document is to ``query``, as a list of scores.

        A reranker reads the query and the document together, where an
        embedding compares two vectors computed apart. It is the only way to
        answer "is this file a CV?" from the word "cv" alone: measured on a
        real drive, cosine ranked the two CVs below unrelated files, the
        reranker put them first by a wide margin.
        """
        data = self._request(
            "POST",
            "/rerank",
            json={
                "model": settings.GRAPH_ALBERT_RERANK_MODEL,
                "query": query,
                "documents": list(documents),
            },
        )
        scores = [0.0] * len(list(documents))
        for row in data.get("results", []):
            scores[row["index"]] = row["relevance_score"]
        return scores

    def describe_image(self, raw, mimetype):
        """
        One sentence describing a picture, in French, or "" when refused.

        Used for images holding no readable text: the description is what the
        graph compares, so a photo is placed by what it shows.
        """
        # Keywords matter as much as the sentence: they carry the subject of
        # the picture, which is what brings it next to documents about it.
        prompt = (
            "Regarde cette image. Réponds en français, en deux lignes :\n"
            "1. une phrase nommant le sujet, sans commencer par « on y voit » "
            "ni « l'image montre » ;\n"
            "2. six mots-clés du sujet, séparés par des virgules, du plus précis "
            "au plus général.\n"
            "N'emploie jamais les mots image, photo, illustration, dessin, "
            "représentation : décris le sujet, pas le support. "
            "Pas d'introduction, pas de numérotation."
        )
        return self.chat(
            prompt,
            max_tokens=160,
            image=(raw, mimetype),
            model=settings.GRAPH_ALBERT_VISION_MODEL,
            # No sampling: the same picture must come back with the same
            # words. Re-indexing one with a different wording moves its
            # vector, and a file whose two closest neighbours sit within
            # 0.01 of each other then changes group for no reason.
            temperature=0,
        )

    def transcribe(self, audio, filename, language=None):
        """The speech of an audio file (an open binary file), as text."""
        fields = {"model": settings.GRAPH_ALBERT_AUDIO_MODEL, "response_format": "json"}
        language = language if language is not None else settings.GRAPH_TRANSCRIPTION_LANGUAGE
        if language:
            fields["language"] = language
        # Albert answers 500 to a file part without a content type.
        content_type = mimetypes.guess_type(filename)[0] or "audio/mpeg"
        data = self._request(
            "POST",
            "/audio/transcriptions",
            files={"file": (filename, audio, content_type)},
            data=fields,
            timeout=settings.GRAPH_TRANSCRIPTION_TIMEOUT,
        )
        return (data.get("text") or "").strip()
