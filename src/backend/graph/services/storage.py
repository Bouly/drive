"""
Read and write the file graph storage (step 4).

This is the only module the other steps need: it hides pgvector behind a
few functions.

- ``save_chunks(item, chunks)``: replace the chunks of an item.
- ``delete_chunks(item)``: forget an item.
- ``item_vector(item)``: one vector for the whole item.
- ``nearest_items(vector, items, k)``: the k closest items among ``items``.
- ``nearest_chunks(vector, items, k)``: the k closest passages, with text.
- ``replace_links(item, links)``: rewrite the links starting from an item.
"""

from dataclasses import dataclass

from django.db import transaction
from django.db.models import Min

from pgvector.django import CosineDistance

from graph.models import ItemChunk, ItemLink


@dataclass(frozen=True)
class Neighbour:
    """An item close to a vector, with the passage that made it close."""

    item_id: str
    # Cosine similarity, 1.0 for identical content.
    similarity: float
    chunk_id: str | None = None
    text: str = ""


def save_chunks(item, chunks):
    """
    Store the chunks of an item, replacing any previous ones.

    ``chunks`` are ``graph.services.chunking.Chunk`` objects whose
    ``embedding`` is set. Returns the number of chunks stored.
    """
    rows = [
        ItemChunk(
            item=item,
            index=chunk.index,
            text=chunk.text,
            text_hash=chunk.text_hash,
            embedding=chunk.embedding,
        )
        for chunk in chunks
        if chunk.embedding is not None
    ]
    with transaction.atomic():
        ItemChunk.objects.filter(item=item).delete()
        ItemChunk.objects.bulk_create(rows)
    return len(rows)


def delete_chunks(item):
    """Remove everything stored about an item's content."""
    ItemChunk.objects.filter(item=item).delete()


def embeddings_by_hash(text_hashes):
    """
    Vectors already stored for passages with these hashes, as {hash: vector}.

    The same text always gets the same vector: a re-indexed or duplicated
    file does not need to be embedded again.
    """
    rows = ItemChunk.objects.filter(text_hash__in=set(text_hashes)).values_list(
        "text_hash", "embedding"
    )
    return {text_hash: list(embedding) for text_hash, embedding in rows}


def item_vector(item):
    """
    One unit vector standing for the whole item: the normalized mean of its
    chunk vectors. None when the item has no chunk.
    """
    vectors = [
        list(vector)
        for vector in ItemChunk.objects.filter(item=item).values_list("embedding", flat=True)
    ]
    if not vectors:
        return None
    mean = [sum(values) / len(vectors) for values in zip(*vectors, strict=True)]
    norm = sum(x * x for x in mean) ** 0.5
    return [x / norm for x in mean] if norm else mean


def nearest_chunks(vector, items, k=10, min_similarity=0.0, exclude_item=None):
    """
    The k passages closest to ``vector`` among the chunks of ``items``
    (a queryset or list of Item, typically the ones a user can read).
    """
    queryset = ItemChunk.objects.filter(item__in=items)
    if exclude_item is not None:
        queryset = queryset.exclude(item=exclude_item)
    rows = (
        queryset.annotate(distance=CosineDistance("embedding", vector))
        .order_by("distance")
        .values("id", "item_id", "text", "distance")[:k]
    )
    return [
        Neighbour(
            item_id=str(row["item_id"]),
            similarity=1 - row["distance"],
            chunk_id=str(row["id"]),
            text=row["text"],
        )
        for row in rows
        if 1 - row["distance"] >= min_similarity
    ]


def nearest_items(vector, items, k=6, min_similarity=0.55, exclude_item=None):
    """
    The k items closest to ``vector`` among ``items``: an item's distance is
    that of its best chunk. Only items at or above ``min_similarity`` are
    returned, most similar first.
    """
    queryset = ItemChunk.objects.filter(item__in=items)
    if exclude_item is not None:
        queryset = queryset.exclude(item=exclude_item)
    rows = (
        queryset.annotate(distance=CosineDistance("embedding", vector))
        .values("item_id")
        .annotate(best=Min("distance"))
        .filter(best__lte=1 - min_similarity)
        .order_by("best")[:k]
    )
    return [Neighbour(item_id=str(row["item_id"]), similarity=1 - row["best"]) for row in rows]


def replace_links(item, links):
    """
    Rewrite the links starting from ``item``.

    ``links`` is an iterable of dicts with ``target`` (Item or id),
    ``weight``, ``kind`` and optionally ``reason``, ``evidence``,
    ``surprising``. Returns the number of links stored.
    """
    rows = []
    for link in links:
        target = link["target"]
        rows.append(
            ItemLink(
                source=item,
                target_id=getattr(target, "id", target),
                weight=link["weight"],
                kind=link["kind"],
                reason=link.get("reason", ""),
                evidence=link.get("evidence", ""),
                surprising=link.get("surprising", False),
            )
        )
    with transaction.atomic():
        ItemLink.objects.filter(source=item).delete()
        ItemLink.objects.bulk_create(rows)
    return len(rows)
