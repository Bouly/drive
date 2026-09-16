"""Tests for the graph storage (step 4): chunks, vectors, neighbours, links."""

import pytest

from core import factories, models

from graph.models import ItemChunk, ItemLink
from graph.services import storage
from graph.services.chunking import Chunk, hash_text

pytestmark = pytest.mark.django_db

DIM = 1024


def unit(axis, dim=DIM):
    """A unit vector along one axis; two different axes are orthogonal."""
    vector = [0.0] * dim
    vector[axis] = 1.0
    return vector


def mix(a, b, weight):
    """A unit vector between axes ``a`` and ``b``: closer to ``a`` when weight is high."""
    vector = [0.0] * DIM
    vector[a] = weight
    vector[b] = (1 - weight**2) ** 0.5
    return vector


def make_file(title):
    """A ready file item."""
    return factories.ItemFactory(
        title=title,
        type=models.ItemTypeChoices.FILE,
        update_upload_state=models.ItemUploadStateChoices.READY,
    )


def make_chunks(*vectors):
    """Chunk objects carrying the given embeddings."""
    return [
        Chunk(index=i, text=f"passage {i}", text_hash=hash_text(f"passage {i}"), embedding=v)
        for i, v in enumerate(vectors)
    ]


def test_save_chunks_replaces_previous_ones():
    """Saving twice keeps only the latest chunks; unembedded chunks are skipped."""
    item = make_file("a")
    assert storage.save_chunks(item, make_chunks(unit(0), unit(1))) == 2
    assert ItemChunk.objects.filter(item=item).count() == 2

    chunks = make_chunks(unit(2))
    chunks.append(Chunk(index=1, text="no vector", text_hash="x"))
    assert storage.save_chunks(item, chunks) == 1
    stored = list(ItemChunk.objects.filter(item=item))
    assert len(stored) == 1
    assert stored[0].index == 0
    assert list(stored[0].embedding)[:3] == [0.0, 0.0, 1.0]


def test_chunks_are_deleted_with_their_item():
    """Deleting an item removes its chunks (database cascade)."""
    item = make_file("a")
    storage.save_chunks(item, make_chunks(unit(0)))
    # Drive items go through the trash first; chunks survive that step so a
    # restored file keeps its place in the graph, and go when the row does.
    item.soft_delete()
    assert ItemChunk.objects.count() == 1
    item.delete()
    assert ItemChunk.objects.count() == 0


def test_item_vector_is_the_normalized_mean():
    """The item vector averages its chunks and has unit length."""
    item = make_file("a")
    assert storage.item_vector(item) is None
    storage.save_chunks(item, make_chunks(unit(0), unit(1)))
    vector = storage.item_vector(item)
    assert vector[0] == pytest.approx(0.7071, abs=1e-3)
    assert vector[1] == pytest.approx(0.7071, abs=1e-3)
    assert sum(x * x for x in vector) == pytest.approx(1.0)


def test_nearest_items_orders_by_best_chunk_and_applies_threshold():
    """Items come back most similar first, one row per item, below a distance cutoff."""
    close = make_file("close")
    far = make_file("far")
    other = make_file("other")
    storage.save_chunks(close, make_chunks(unit(1), mix(0, 1, 0.95)))  # best chunk ~0.95
    storage.save_chunks(far, make_chunks(mix(0, 1, 0.6)))  # ~0.6
    storage.save_chunks(other, make_chunks(unit(2)))  # 0.0

    items = models.Item.objects.all()
    found = storage.nearest_items(unit(0), items, k=5, min_similarity=0.5)
    assert [n.item_id for n in found] == [str(close.id), str(far.id)]
    assert found[0].similarity == pytest.approx(0.95, abs=1e-3)

    # The candidate set restricts the search (this is where access rights go).
    found = storage.nearest_items(unit(0), models.Item.objects.filter(id=far.id), k=5)
    assert [n.item_id for n in found] == [str(far.id)]

    # A file is never its own neighbour.
    found = storage.nearest_items(unit(0), items, k=5, exclude_item=close)
    assert [n.item_id for n in found] == [str(far.id)]


def test_nearest_chunks_returns_passages():
    """Chunk search gives the passage text and its similarity."""
    item = make_file("a")
    storage.save_chunks(item, make_chunks(unit(0), unit(1)))
    found = storage.nearest_chunks(unit(1), models.Item.objects.all(), k=1)
    assert len(found) == 1
    assert found[0].text == "passage 1"
    assert found[0].similarity == pytest.approx(1.0)
    assert found[0].chunk_id


def test_replace_links_rewrites_outgoing_links():
    """Links from an item are replaced as a whole; other items' links stay."""
    a, b, c = make_file("a"), make_file("b"), make_file("c")
    storage.replace_links(a, [{"target": b, "weight": 0.8, "kind": ItemLink.Kind.SEMANTIC}])
    storage.replace_links(b, [{"target": a, "weight": 0.8, "kind": ItemLink.Kind.SEMANTIC}])
    count = storage.replace_links(
        a,
        [
            {
                "target": c.id,
                "weight": 0.7,
                "kind": ItemLink.Kind.COPY,
                "reason": "Passage repris",
                "evidence": "même clause",
            }
        ],
    )
    assert count == 1
    link = ItemLink.objects.get(source=a)
    assert link.target == c
    assert link.kind == "copy"
    assert link.evidence == "même clause"
    assert ItemLink.objects.filter(source=b).count() == 1


def test_link_cannot_point_to_itself():
    """The database refuses a self-link."""
    a = make_file("a")
    with pytest.raises(Exception, match="link_not_to_self"):
        storage.replace_links(a, [{"target": a, "weight": 1, "kind": ItemLink.Kind.SEMANTIC}])
