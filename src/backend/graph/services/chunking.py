"""
Split an extracted text into overlapping chunks (step 2 of the pipeline).

Embedding models read a bounded window, and links are more precise when they
are computed between passages rather than whole documents: a 20-page report
becomes ~40 chunks of a few hundred words, each embedded on its own.
"""

import hashlib
import re
from dataclasses import dataclass, field

from django.conf import settings

_WHITESPACE = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES = re.compile(r"\n\s*\n+")


@dataclass
class Chunk:
    """A passage of an item's text, ready to be embedded and stored."""

    index: int
    text: str
    # sha256 of the normalized text: exact duplicates share a hash.
    text_hash: str
    embedding: list[float] | None = field(default=None, repr=False)

    @property
    def word_count(self):
        """Number of words in the chunk."""
        return len(self.text.split())


def normalize_text(text):
    """Collapse whitespace while keeping paragraph breaks, and strip control noise."""
    text = text.replace("\x00", "")
    text = _WHITESPACE.sub(" ", text)
    text = _BLANK_LINES.sub("\n\n", text)
    return "\n".join(line.strip() for line in text.split("\n")).strip()


def readable_title(title):
    """
    A file name as words: no extension, no dashes or underscores.

    "Poop-emoji-scaled.jpg" becomes "Poop emoji scaled", so files are not
    drawn together by a shared extension. It is prepended to a file's text
    before chunking: a photo named after what it shows is placed by its name
    when it holds nothing else.
    """
    return re.sub(r"\.[A-Za-z0-9]{1,8}$", "", title).replace("-", " ").replace("_", " ").strip()


def hash_text(text):
    """Stable identifier of a passage, used to spot duplicated content."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sliding_windows(words, size, overlap):
    """Windows of ``size`` words, each starting ``overlap`` words before the previous end."""
    windows = []
    start = 0
    while True:
        windows.append(words[start : start + size])
        if start + size >= len(words):
            return windows
        start += size - overlap


def chunk_text(text, size=None, overlap=None):
    """
    Cut a text into chunks of at most ``size`` words.

    Paragraphs are the unit: short ones are grouped together up to the size,
    and a paragraph longer than the size is cut into windows sharing
    ``overlap`` words, so a sentence cut at a boundary is whole in one of them.
    """
    size = size or settings.GRAPH_CHUNK_WORDS
    overlap = overlap if overlap is not None else settings.GRAPH_CHUNK_OVERLAP
    if overlap >= size:
        raise ValueError("GRAPH_CHUNK_OVERLAP must be smaller than GRAPH_CHUNK_WORDS")

    text = normalize_text(text)
    if not text:
        return []

    passages = []
    group = []

    def flush():
        if group:
            passages.append(" ".join(group))
            group.clear()

    for paragraph in text.split("\n\n"):
        words = paragraph.split()
        if len(words) > size:
            # What was waiting is a lead-in to this paragraph, not a passage:
            # a title followed by a long text used to be stored on its own,
            # and a passage holding only "video" resembles nothing and hides
            # what the file really says from anything that reads one passage.
            words = group + words
            group.clear()
            passages.extend(" ".join(window) for window in _sliding_windows(words, size, overlap))
        elif len(group) + len(words) > size:
            flush()
            group.extend(words)
        else:
            group.extend(words)
    flush()

    return [
        Chunk(index=index, text=passage, text_hash=hash_text(passage))
        for index, passage in enumerate(passages)
    ]
