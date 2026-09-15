"""Tests for the chunking step of the graph pipeline."""

import pytest

from graph.services.chunking import chunk_text, hash_text, normalize_text


def test_normalize_text_keeps_paragraphs():
    """Runs of spaces collapse, paragraph breaks survive, control chars go."""
    text = "Titre  du   doc\n\n\n\nPremier   para.\x00\nSuite.  "
    assert normalize_text(text) == "Titre du doc\n\nPremier para.\nSuite."


def test_chunk_text_empty():
    """Nothing to chunk gives no chunk."""
    assert chunk_text("   \n\n ") == []


def test_chunk_text_short_paragraphs_are_grouped():
    """Short paragraphs are packed together up to the chunk size."""
    text = "\n\n".join(f"para {i} mot mot mot" for i in range(10))  # 5 words each
    chunks = chunk_text(text, size=12, overlap=2)
    assert [c.index for c in chunks] == list(range(len(chunks)))
    assert all(c.word_count <= 12 for c in chunks)
    # Every word of the text ends up in some chunk.
    assert set(text.split()) <= set(" ".join(c.text for c in chunks).split())


def test_chunk_text_long_paragraph_overlaps():
    """A long paragraph is cut with the requested overlap."""
    words = [f"w{i}" for i in range(100)]
    chunks = chunk_text(" ".join(words), size=40, overlap=10)
    assert [c.word_count for c in chunks] == [40, 40, 40]
    assert chunks[0].text.split()[-10:] == chunks[1].text.split()[:10]
    assert chunks[-1].text.split()[-1] == "w99"


def test_chunk_text_rejects_bad_overlap():
    """An overlap as large as the size would loop forever."""
    with pytest.raises(ValueError):
        chunk_text("a b c", size=5, overlap=5)


def test_hash_is_stable_and_content_based():
    """Identical passages share a hash, different ones do not."""
    assert hash_text("même texte") == hash_text("même texte")
    assert hash_text("même texte") != hash_text("autre texte")
    chunks = chunk_text("Bonjour le monde")
    assert chunks[0].text_hash == hash_text("Bonjour le monde")
