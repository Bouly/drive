"""
Semantic links of an item (a minimal step 5), built only on ``storage``.

An item is linked to its closest neighbours; a strong link to a file of
another topic is flagged as "surprising" (an unexpected connection).
"""

from core import models

from graph.models import ItemLink, ItemTopic
from graph.services import storage

LINKS_PER_ITEM = 4
# Neighbours are linked from this similarity...
MIN_SIMILARITY = 0.62
# ...and a link between two different topics that strong is "unexpected".
SURPRISE_MIN_SIMILARITY = 0.62
# The single closest neighbour is kept from this lower similarity, so a file
# whose content relates to something is not left alone in the graph.
NEAREST_MIN_SIMILARITY = 0.5


def live_files():
    """
    Files that are neither in the trash nor inside a trashed folder.

    ``filter_non_deleted`` is not enough: trashing a folder only marks its
    descendants with ``ancestors_deleted_at``, which is what Drive checks too.
    """
    return models.Item.objects.filter(
        type=models.ItemTypeChoices.FILE, ancestors_deleted_at__isnull=True
    )


def semantic_links(item, candidates, topic_of=None):
    """
    The links to store for ``item``, as dicts for ``storage.replace_links``.

    ``candidates`` are the items a link may point to; ``topic_of`` maps item
    ids (as strings) to topic ids and is read from storage when not given.
    Returns an empty list when the item has no chunk.
    """
    vector = storage.item_vector(item)
    if vector is None:
        return []
    neighbours = storage.nearest_items(
        vector,
        candidates,
        k=LINKS_PER_ITEM,
        min_similarity=NEAREST_MIN_SIMILARITY,
        exclude_item=item,
    )
    neighbours = [
        neighbour
        for rank, neighbour in enumerate(neighbours)
        if rank == 0 or neighbour.similarity >= MIN_SIMILARITY
    ]
    if topic_of is None:
        ids = [item.id, *(neighbour.item_id for neighbour in neighbours)]
        topic_of = {
            str(membership.item_id): membership.topic_id
            for membership in ItemTopic.objects.filter(item_id__in=ids)
        }
    links = []
    for neighbour in neighbours:
        # Only two known, different topics make a link unexpected: an uploaded
        # file has no topic yet, which says nothing about its neighbours.
        source_topic = topic_of.get(str(item.id))
        target_topic = topic_of.get(neighbour.item_id)
        surprising = (
            None not in (source_topic, target_topic)
            and source_topic != target_topic
            and neighbour.similarity >= SURPRISE_MIN_SIMILARITY
        )
        evidence = storage.nearest_chunks(
            vector, models.Item.objects.filter(id=neighbour.item_id), k=1
        )
        links.append(
            {
                "target": neighbour.item_id,
                "weight": round(neighbour.similarity, 3),
                "kind": ItemLink.Kind.SEMANTIC,
                "surprising": surprising,
                "reason": f"Contenus proches ({round(neighbour.similarity * 100)} % de similarité)",
                "evidence": evidence[0].text[:300] if evidence else "",
            }
        )
    return links


def link_item(item, candidates, topic_of=None):
    """Rewrite the links of ``item`` among ``candidates``; returns how many were stored."""
    return storage.replace_links(item, semantic_links(item, candidates, topic_of))


def relink_neighbours(item, candidates):
    """
    Rewrite the links of the items close to ``item``.

    Links are computed when a file is indexed: without this, a file indexed
    earlier would never point to a closer file that arrived after it.
    Returns the number of items relinked.
    """
    vector = storage.item_vector(item)
    if vector is None:
        return 0
    neighbours = storage.nearest_items(
        vector,
        candidates,
        k=2 * LINKS_PER_ITEM,
        min_similarity=NEAREST_MIN_SIMILARITY,
        exclude_item=item,
    )
    for neighbour in models.Item.objects.filter(id__in=[n.item_id for n in neighbours]):
        link_item(neighbour, candidates)
    return len(neighbours)
