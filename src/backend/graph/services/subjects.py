"""
Subjects a user creates, and the files that fall into them.

A subject has a vector: the mean of its description and of the files pinned
to it. Every indexed file close enough to that vector joins the subject, with
the similarity as its score. Pinned files stay whatever the numbers say, so
what the user decided always wins over what is computed.

Nothing here invents a subject or a name: that was tried, and it grouped
unrelated files under labels nobody asked for.
"""

import logging

from django.conf import settings
from django.db import transaction

from graph.models import ItemTopic
from graph.services import storage
from graph.services.albert import AlbertClient, AlbertError
from graph.services.linking import indexed_files

logger = logging.getLogger(__name__)


def describe(topic):
    """The words defining the subject: its name, and its description if any."""
    return f"{topic.name}\n\n{topic.description}".strip()


def topic_vector(topic):
    """
    One unit vector standing for the subject, or None when it has nothing yet.

    The words weigh as much as one pinned file: a subject described in a
    sentence works alone, and each file pinned afterwards sharpens it.
    """
    vectors = []
    try:
        vectors.append(AlbertClient().embed([describe(topic)])[0])
    except AlbertError as exc:
        logger.warning("Albert could not embed topic %s: %s", topic.id, exc)

    for membership in topic.memberships.filter(pinned=True).select_related("item"):
        vector = storage.item_vector(membership.item)
        if vector is not None:
            vectors.append(vector)

    if not vectors:
        return None
    mean = [sum(values) / len(vectors) for values in zip(*vectors, strict=True)]
    norm = sum(x * x for x in mean) ** 0.5
    return [x / norm for x in mean] if norm else mean


def sort_files_into(topic, candidates=None):
    """
    Put every file close enough to the subject in it, and take the others out.

    Returns the number of files in the subject. Pinned files are left alone:
    they are the subject's definition, not its result.
    """
    vector = topic_vector(topic)
    Topic = type(topic)  # pylint: disable=invalid-name
    Topic.objects.filter(id=topic.id).update(vector=vector)
    pinned = set(topic.memberships.filter(pinned=True).values_list("item_id", flat=True))
    if vector is None:
        return len(pinned)

    matched = {}
    for neighbour in storage.item_similarities(
        vector, indexed_files(candidates), exclude_item=None
    ):
        if neighbour.similarity >= settings.GRAPH_TOPIC_MIN_SIMILARITY:
            matched[neighbour.item_id] = round(neighbour.similarity, 3)

    with transaction.atomic():
        topic.memberships.filter(pinned=False).exclude(item_id__in=matched).delete()
        for item_id, score in matched.items():
            if item_id in {str(i) for i in pinned}:
                continue
            ItemTopic.objects.update_or_create(
                item_id=item_id, topic=topic, defaults={"score": score, "pinned": False}
            )
    return topic.memberships.count()


def sort_into_subjects(item, topics):
    """
    Place one file in the subjects it belongs to, when it has just changed.

    Cheaper than recomputing every subject: only this file moves.
    """
    vector = storage.item_vector(item)
    if vector is None:
        ItemTopic.objects.filter(item=item, pinned=False).delete()
        return 0
    placed = 0
    for topic in topics:
        if topic.vector is None:
            continue
        similarity = sum(a * b for a, b in zip(vector, list(topic.vector), strict=True))
        if similarity >= settings.GRAPH_TOPIC_MIN_SIMILARITY:
            ItemTopic.objects.update_or_create(
                item=item, topic=topic, defaults={"score": round(similarity, 3)}
            )
            placed += 1
        else:
            ItemTopic.objects.filter(item=item, topic=topic, pinned=False).delete()
    return placed
