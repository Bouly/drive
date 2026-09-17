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
from graph.services.scope import live_files_of

# How many files the reranker judges at once: the closest ones by content,
# so a large drive does not travel through the API on every write.
RERANK_CANDIDATES = 60
# ...of which this many are kept for the files closest to the subject's
# words alone, whatever its pinned files did to its vector.
WORDS_SLOTS = 20
# How much of a file the reranker reads: its name and the start of its text.
RERANK_CHARS = 900
# Read over several passages, not just the first one: a video whose first
# passage was its name scored 0.0002 against the subject it belonged to,
# and 0.77 once the passages after it were read too.
RERANK_PASSAGES = 4
# How many questions a subject may ask. Each one is a call to the reranker,
# and past a handful a description is prose, not a list of subjects.
MAX_QUESTIONS = 8
# How long a subject that has no bar yet waits before sorting itself whole
# again. Without it, dropping four thousand files in at once would sort
# such a subject four thousand times.
RESORT_DELAY = 60

logger = logging.getLogger(__name__)


def describe(topic):
    """The words defining the subject: its name, and its description if any."""
    return f"{topic.name}\n\n{topic.description}".strip()


def questions(topic):
    """
    The questions the subject asks of a file: its name, then each line of its
    description, each on its own.

    One idea per question. A reranker answers "does this file answer that?",
    and everything glued into one query drowns the answer: a drive whose
    subject "abeille" was described as "frelon / ruche / guêpe / nid /
    apiculture" scored its bee documentary 0.013 with the whole block as the
    query, 0.12 with the words strung on one line, and 0.77 with "abeille"
    alone ‒ while unrelated files stayed at 0.0006 either way.
    """
    lines = [line.strip(" -•\t") for line in topic.description.splitlines()]
    asked = [topic.name.strip(), *[line for line in lines if line]]
    return list(dict.fromkeys(question for question in asked if question))[:MAX_QUESTIONS]


def words_vector(topic):
    """One unit vector for what the subject says of itself, or None."""
    try:
        return AlbertClient().embed([describe(topic)])[0]
    except AlbertError as exc:
        logger.warning("Albert could not embed topic %s: %s", topic.id, exc)
        return None


def pinned_vector(topic):
    """One unit vector for the files pinned to the subject, or None."""
    vectors = []
    for membership in topic.memberships.filter(pinned=True).select_related("item"):
        vector = storage.item_vector(membership.item)
        if vector is not None:
            vectors.append(vector)
    return normalize(average(vectors))


def topic_vector(topic, words=None):
    """
    One unit vector standing for the subject, or None when it has nothing yet.

    The words weigh as much as all the pinned files together, so pinning one
    file by mistake tilts the subject instead of taking it over: a deer
    pinned to "Abeilles" pulled it far enough for a second deer to come out
    closer to it than the bees were.
    """
    words = words_vector(topic) if words is None else words
    sides = [side for side in (words, pinned_vector(topic)) if side is not None]
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


def beginnings_of(items):
    """
    What is given to the reader for each file: its name and its first passages.

    Several passages, because the first one can say nothing: a video whose
    first passage was its own name scored 0.0002 against the subject it
    belonged to, and 0.77 once what followed was read too.
    """
    starts = {}
    rows = ItemChunk.objects.filter(item__in=items, index__lt=RERANK_PASSAGES).order_by(
        "item_id", "index"
    )
    for item_id, text in rows.values_list("item_id", "text"):
        start = starts.get(item_id, "")
        if len(start) < RERANK_CHARS:
            starts[item_id] = f"{start} {text}".strip()
    return {
        item.id: f"{item.title}\n{starts[item.id][:RERANK_CHARS]}"
        for item in items
        if starts.get(item.id)
    }


def answers_to(question, texts):
    """
    How each text answers one question, as raw scores and their best.

    A question whose best answer sits near the middle of the batch is
    dropped: nothing in there answers it, and what the reader would hand back
    is the order it happened to put a batch of strangers in.
    """
    try:
        scores = AlbertClient().rerank(question, texts)
    except AlbertError as exc:
        logger.warning("Albert could not answer %r: %s", question, exc)
        return None
    ranked = sorted(scores, reverse=True)
    best = ranked[0]
    # Under three answers there is no batch to stand out from.
    middle = ranked[len(ranked) // 2] if len(ranked) >= 3 else 0.0
    if best <= 0 or best < middle * settings.GRAPH_TOPIC_RERANK_STANDOUT:
        return None
    return scores, best


def read_files(topic, items):
    """
    How much each file belongs to the subject, from 0 to 1, as ``{item_id: …}``.

    Every question of the subject is asked in turn and a file keeps its best
    answer: it belongs here if it is about any one of the things the subject
    names, as well as the subject's best file is about what it answers to.
    """
    return read_and_lead(topic, items)[0]


def read_and_lead(topic, items):
    """
    The same, with the question that discriminated best and the raw score it
    takes to pass it: what an uploaded file is judged against later, on the
    scale of that one question.
    """
    beginnings = beginnings_of(items)
    ids = list(beginnings)
    texts = [beginnings[item_id] for item_id in ids]
    if not texts:
        return {}, "", 0.0

    answered = []
    for question in questions(topic):
        answer = answers_to(question, texts)
        if answer is not None:
            answered.append((question, *answer))
    if not answered:
        return {}, "", 0.0

    # Every question is measured against the best answer of the strongest
    # one, never against its own: a word nothing here is about would
    # otherwise crown whatever came closest to it, and "abeille" described
    # with "frelon" and "guêpe" took in a role-playing sheet at full score
    # beside its film about bees.
    lead, _, lead_best = max(answered, key=lambda answer: answer[2])
    belonging = {}
    for _, scores, _best in answered:
        for item_id, score in zip(ids, scores, strict=True):
            belonging[item_id] = min(1.0, max(belonging.get(item_id, 0.0), score / lead_best))
    return belonging, lead, lead_best * settings.GRAPH_TOPIC_RERANK_RATIO


def relevant_by_reading(topic, items):
    """
    The files worth keeping, as ``({item_id: share}, question, cut)``.

    A file stays when it reaches a quarter of the best answer to at least one
    of the subject's questions. On a real drive the gap is plain ‒ "Abeilles"
    scored its six bee files from 0.62 down to 0.40, and everything else at
    0.03 and below.
    """
    belonging, lead, cut = read_and_lead(topic, items)
    # A subject the drive does not answer keeps nobody.
    #
    # A share is measured against the subject's own best answer, so a subject
    # nothing here is about still crowns its least bad file at 1.00 and then
    # admits a quarter of that: a folder of case law came out holding a file
    # named "ghjgh" under "Public procurement". Measured on that drive, the
    # subjects it is really about answered between 0.09 and 0.99, and the six
    # nothing answered between 0.002 and 0.024 ‒ two orders of magnitude, with
    # no subject anywhere near the line between them.
    if cut / settings.GRAPH_TOPIC_RERANK_RATIO < settings.GRAPH_TOPIC_RERANK_FLOOR:
        return {}, lead, cut
    keep = settings.GRAPH_TOPIC_RERANK_RATIO
    return (
        {item_id: share for item_id, share in belonging.items() if share >= keep},
        lead,
        cut,
    )


def worth_reading(files, vector, words):
    """
    The files the reader is shown: those closest to the subject, and those
    closest to its words alone.

    The words always have their say. A file pinned by mistake moves the
    subject's vector, and on a real drive one pinned header was enough to
    fill the shortlist of a subject called "abeille" with network headers ‒
    the only video about bees never even reached the reader.
    """
    by_id = {str(item.id): item for item in files}
    sides = [(vector, RERANK_CANDIDATES - WORDS_SLOTS), (words, WORDS_SLOTS)]
    if vector is None or words is None:
        sides = [(vector or words, RERANK_CANDIDATES)]
    chosen = {}
    # Each side has its own seats. Ranking the two together would give them
    # all to the subject's vector, whose files are simply closer to it than
    # the ones the words alone bring in ‒ which is the whole point of asking
    # the words separately.
    for side, seats in sides:
        if side is None:
            continue
        for neighbour in storage.item_similarities(side, files, exclude_item=None, k=seats):
            item = by_id.get(neighbour.item_id)
            if item is not None:
                chosen.setdefault(neighbour.item_id, item)
    return list(chosen.values())[:RERANK_CANDIDATES]


def sort_files_into(topic, candidates=None):
    """
    Put every file that reads like the subject in it, and take the others out.

    Returns the number of files in the subject. Pinned files are left alone:
    they are the subject's definition, not its result.
    """
    words = words_vector(topic)
    vector = topic_vector(topic, words=words)
    pinned = {
        str(item_id)
        for item_id in topic.memberships.filter(pinned=True).values_list("item_id", flat=True)
    }
    if vector is None:
        type(topic).objects.filter(id=topic.id).update(vector=None)
        return len(pinned)

    # A subject sorts its owner's drive, never the whole instance: another
    # user's files have nothing to do in it, and the reranker has no business
    # reading them.
    if candidates is None:
        candidates = live_files_of(topic.creator)
    # The vectors only say which files are worth reading; the reading decides.
    files = list(indexed_files(candidates))
    shortlist = worth_reading(files, vector, words)
    matched, lead, cut = relevant_by_reading(topic, shortlist)
    matched = {str(item_id): round(share, 4) for item_id, share in matched.items()}

    type(topic).objects.filter(id=topic.id).update(vector=vector, cut=cut, question=lead[:255])
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
    vector says which subjects are worth asking about, and each of those puts
    to the file the one question that told its files apart best, against the
    score it took to pass, both kept from its last full sort.
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
        if topic.cut <= 0 or not topic.question:
            # A subject nothing has ever answered to has no bar to judge this
            # file against. Sorting it whole gives it one, and places the file
            # on the way ‒ once in a while, not once per arrival.
            if cache.add(f"graph-topic-sorted-{topic.id}", "1", timeout=RESORT_DELAY):
                sort_files_into(topic)
            continue
        text = beginnings_of([item]).get(item.id)
        score = None
        if text:
            try:
                score = AlbertClient().rerank(topic.question, [text])[0]
            except AlbertError as exc:
                logger.warning("Albert could not judge item %s: %s", item.id, exc)
                continue
        if score is not None and score >= topic.cut:
            peak = topic.cut / settings.GRAPH_TOPIC_RERANK_RATIO
            share = min(1.0, score / peak) if peak else 1.0
            ItemTopic.objects.update_or_create(
                item=item, topic=topic, defaults={"score": round(share, 4)}
            )
            placed += 1
        else:
            ItemTopic.objects.filter(item=item, topic=topic, pinned=False).delete()
    return placed
