"""
Steps 1 to 3 of the file graph pipeline: extract, chunk, embed.

``prepare_item`` returns chunks carrying their embedding; storing them
(step 4) and linking items (step 5) build on this.
"""

import logging
from dataclasses import dataclass

from graph.services.chunking import Chunk, chunk_text
from graph.services.embeddings import get_embedder
from graph.services.extraction import extract_text

logger = logging.getLogger(__name__)


@dataclass
class PreparedItem:
    """What the pipeline knows about an item once its text is processed."""

    item_id: str
    text: str
    chunks: list[Chunk]

    @property
    def is_empty(self):
        """True when nothing worth indexing came out of the file."""
        return not self.chunks


def prepare_item(item, embed=True, extractor=None, embedder=None):
    """
    Extract the text of ``item``, cut it into chunks and embed them.

    Raises graph.services.extraction.ExtractionSkipped / ExtractionError and
    graph.services.embeddings.EmbeddingError; callers decide how to react.
    """
    text = extract_text(item, extractor=extractor)
    chunks = chunk_text(text)
    if embed and chunks:
        embedder = embedder or get_embedder()
        vectors = embedder.embed_passages(chunk.text for chunk in chunks)
        for chunk, vector in zip(chunks, vectors, strict=True):
            chunk.embedding = vector
    logger.info("Prepared item %s: %d chunks", item.id, len(chunks))
    return PreparedItem(item_id=str(item.id), text=text, chunks=chunks)
