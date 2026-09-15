"""
The passage contract between the chunking step (2) and the storage (4).

The code that cuts a text into passages lives on the ``graph-extraction``
branch; here only the shape of what storage accepts is defined.
"""

import hashlib
from dataclasses import dataclass, field


@dataclass
class Chunk:
    """A passage of an item's text, ready to be stored."""

    # Position of the passage in the document, from 0.
    index: int
    text: str
    # sha256 of the text: exact duplicates share a hash (see hash_text).
    text_hash: str
    # Unit vector of GRAPH_EMBEDDING_DIM floats; None until embedded.
    embedding: list[float] | None = field(default=None, repr=False)

    @property
    def word_count(self):
        """Number of words in the chunk."""
        return len(self.text.split())


def hash_text(text):
    """Stable identifier of a passage, used to spot duplicated content."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
