"""
Subjects a user creates, and the files that fall into them.

A subject is words: a name, a description, and the files the user pinned to
it. Files are sorted into it in two steps ‒ the vectors draw up a shortlist of
what could plausibly belong, then a reranker reads the subject and each file
together and decides.

The reading decides alone because comparing vectors cannot: measured on a real
drive, cosine puts unrelated files between 0.45 and 0.60 of any subject, so no
line drawn in that band separates anything. A subject called "memory" took in
550 files, a dozen role-playing PDFs landed in "Abeilles", and Linux headers
in "cv" ‒ all of them read as irrelevant by the reranker, which was right.

Nothing here invents a subject or a name: that was tried, and it grouped
unrelated files under labels nobody asked for.
"""

import logging

from django.conf import settings
from django.core.cache import cache
from django.db import transaction

from graph.models import ItemChunk, ItemTopic
from graph.services import storage
from graph.services.albert import AlbertClient, AlbertError
from graph.services.linking import indexed_files

# How many files the reranker judges at once: the closest ones by content,
# so a large drive does not travel through the API on every write.
RERANK_CANDIDATES = 60
# How much of a file the reranker reads: its name and the start of its text.
RERANK_CHARS = 900
# How long a subject that has no bar yet waits before sorting itself whole
# again. Without it, dropping four thousand files in at once would sort
# such a subject four thousand times.
RESORT_DELAY = 60

logger = logging.getLogger(__name__)


def describe(topic):
    """The words defining the subject: its name, and its description if any."""
    return f"{topic.name}\n\n{topic.description}".strip()


def topic_vector(topic):
    """
    One unit vector standing for the subject, or None when it has nothing yet.

    The words weigh as much as all the pinned files together, so pinning one
    file by mistake tilts the shortlist instead of taking it over: a deer
    pinned to "Abeilles" pulled the subject far enough for a second deer to
    come out closer to it than the bees were.
    """
    words, pinned = None, []
    try:
        words = AlbertClient().embed([describe(topic)])[0]
    except AlbertError as exc:
        logger.warning("Albert could not embed topic %s: %s", topic.id, exc)

    for membership in topic.memberships.filter(pinned=True).select_related("item"):
        vector = storage.item_vector(membership.item)
        if vector is not None:
            pinned.append(vector)

    sides = [side for side in (words, average(pinned)) if side is not None]
    return normalize(average(sides))


def average(vectors):
    """The mean of the vectors, or None when there is none."""
    if not vectors:
        return None
    return [sum(values) / len(vectors) for values in zip(*vectors, strict=True)]


def normalize(vector):
    """The vector brought back to unit length, or None."""
    if vector is None:
        return None
    norm = sum(x * x for x in vector) ** 0.5
    return [x / norm for x in vector] if norm else vector


def read_files(topic, items):
    """
    How relevant each file is to the subject's words, as ``{item_id: score}``.

    A reranker reads the query and the file together, where a vector pair was
    computed apart. It is the only thing that answers "is this file a CV?"
    from the word "cv" alone, and the only thing that tells a deer from a bee
    once both are "faune, nature, animal" in a drive of photographs.
    """
    texts, ids = [], []
    for item in items:
        chunk = ItemChunk.objects.filter(item=item).order_by("index").first()
        if chunk:
            ids.append(item.id)
            texts.append(f"{item.title}\n{chunk.text[:RERANK_CHARS]}")
    if not texts:
        return {}
    try:
        scores = AlbertClient().rerank(describe(topic), texts)
    except AlbertError as exc:
        logger.warning("Albert could not judge topic %s: %s", topic.id, exc)
        return {}
    return dict(zip(ids, scores, strict=True))


def relevant_by_reading(topic, items):
    """
    The files worth keeping, as ``({item_id: score}, cut)``.

    The bar is a share of the best score of the batch, never a fixed value:
    the same reranker answers 0.53 to "cv" and 0.07 to "curriculum vitae" on
    the very same two files, so only the gap between the answers can be read.
    On a real drive the gap is plain ‒ "Abeilles" scored its six bee files
    from 0.62 down to 0.40, and everything else at 0.03 and below.

    A subject the reader does not recognise has no gap: its best answer sits
    near the middle of the batch, and it keeps nobody rather than crowning
    whatever came first.
    """
    scores = read_files(topic, items)
    if not scores:
        return {}, 0.0
    ranked = sorted(scores.values(), reverse=True)
    # Under three answers there is no batch to stand out from.
    best = ranked[0]
    middle = ranked[len(ranked) // 2] if len(ranked) >= 3 else 0.0
    if best <= 0 or best < middle * settings.GRAPH_TOPIC_RERANK_STANDOUT:
        return {}, 0.0
    cut = best * settings.GRAPH_TOPIC_RERANK_RATIO
    return {item_id: score for item_id, score in scores.items() if score >= cut}, cut


def sort_files_into(topic, candidates=None):
    """
    Put every file that reads like the subject in it, and take the others out.

    Returns the number of files in the subject. Pinned files are left alone:
    they are the subject's definition, not its result.
    """
    vector = topic_vector(topic)
    pinned = {
        str(item_id)
        for item_id in topic.memberships.filter(pinned=True).values_list("item_id", flat=True)
    }
    if vector is None:
        type(topic).objects.filter(id=topic.id).update(vector=None)
        return len(pinned)

    # The vectors only say which files are worth reading; the reading decides.
    files = list(indexed_files(candidates))
    similarities = {
        n.item_id: n.similarity for n in storage.item_similarities(vector, files, exclude_item=None)
    }
    shortlist = sorted(files, key=lambda i: -similarities.get(str(i.id), 0))[:RERANK_CANDIDATES]
    matched, cut = relevant_by_reading(topic, shortlist)
    matched = {str(item_id): round(score, 4) for item_id, score in matched.items()}

    type(topic).objects.filter(id=topic.id).update(vector=vector, cut=cut)
    with transaction.atomic():
        topic.memberships.filter(pinned=False).exclude(item_id__in=matched).delete()
        for item_id, score in matched.items():
            if item_id in pinned:
                continue
            ItemTopic.objects.update_or_create(
                item_id=item_id, topic=topic, defaults={"score": score, "pinned": False}
            )
    return topic.memberships.count()


def sort_into_subjects(item, topics):
    """
    Place one file in the subjects it belongs to, when it has just changed.

    Cheaper than recomputing every subject: only this file moves. The subject
    vector says which subjects are worth asking about, and each of those has
    the reranker read the file against its words, against the bar it kept
    from its last full sort.
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
        if similarity < settings.GRAPH_TOPIC_MIN_SIMILARITY:
            ItemTopic.objects.filter(item=item, topic=topic, pinned=False).delete()
            continue
        if topic.cut <= 0:
            # A subject nothing has ever answered to has no bar to judge this
            # file against. Sorting it whole gives it one, and places the file
            # on the way ‒ once in a while, not once per arrival.
            if cache.add(f"graph-topic-sorted-{topic.id}", "1", timeout=RESORT_DELAY):
                sort_files_into(topic)
            continue
        score = read_files(topic, [item]).get(item.id)
        if score is not None and score >= topic.cut:
            ItemTopic.objects.update_or_create(
                item=item, topic=topic, defaults={"score": round(score, 4)}
            )
            placed += 1
        else:
            ItemTopic.objects.filter(item=item, topic=topic, pinned=False).delete()
    return placed
