"""
Automatic topics of the file graph (step 6).

Files are grouped by community detection (Louvain) on their nearest-neighbour
graph: a topic is a set of files more linked to each other than to the rest.
Each new group is named by Albert's chat model from its titles and passages;
a group that mostly matches an existing topic keeps its name, so topics stay
stable as files arrive and Albert is only asked about new groups.

Topics given by a source (Albert's themes on seeded files) are left alone.
"""

import logging
import re
from collections import Counter
from dataclasses import dataclass, field

from django.db import transaction

from graph.models import ItemChunk, ItemTopic, Topic
from graph.services import storage
from graph.services.albert import AlbertClient, AlbertError
from graph.services.linking import link_item, live_files

logger = logging.getLogger(__name__)

# Neighbours taken per file to build the community graph, and the similarity
# a pair needs to count. Higher than the linking floor on purpose: a weak tie
# is worth drawing, not founding a topic on. A file whose only ties are weak
# stays on its own, without a topic, rather than joining a group it has
# nothing to do with.
NEIGHBOURS = 4
FLOOR = 0.55
# Below this, two files have nothing to do with each other at all.
WEAK_FLOOR = 0.5
# A group keeps the name of the topic it shares at least this share of files
# with. Kept high: a name inherited by a group that drifted is worse than a
# new one, and Albert is only asked about groups that really changed.
REUSE_OVERLAP = 0.7
MAX_LABEL_LENGTH = 60
STOPWORDS = set(
    "le la les de des du un une et en à au aux pour par sur dans avec sans ou d l "
    "ce cet cette ces son sa ses leur leurs est sont docx odt pdf png txt xlsx pptx".split()
)


@dataclass
class TopicReport:
    """What a topic refresh produced."""

    files: int = 0
    topics: list = field(default_factory=list)
    named: int = 0
    reused: int = 0


def _local_moves(adjacency, resolution):
    """
    One Louvain phase: move each node to the neighbouring community that most
    increases modularity, until nothing moves. Returns (community, improved).
    """
    total_weight = sum(sum(neighbours.values()) for neighbours in adjacency)
    degree = [
        sum(neighbours.values()) + neighbours.get(i, 0) for i, neighbours in enumerate(adjacency)
    ]
    community = list(range(len(adjacency)))
    community_degree = degree[:]
    improved = moved = False
    while True:
        for node, neighbours in enumerate(adjacency):
            current = community[node]
            weight_to = {}
            for neighbour, weight in neighbours.items():
                if neighbour != node:
                    weight_to[community[neighbour]] = (
                        weight_to.get(community[neighbour], 0) + weight
                    )
            community_degree[current] -= degree[node]
            scale = resolution * degree[node] / total_weight
            gains = {
                target: weight_to.get(target, 0) - scale * community_degree[target]
                for target in {current, *weight_to}
            }
            best = current
            for target in sorted(weight_to):
                if gains[target] > gains[best] + 1e-12:
                    best = target
            community_degree[best] += degree[node]
            if best != current:
                community[node] = best
                moved = improved = True
        if not moved:
            return community, improved
        moved = False


def _aggregate(adjacency, members, community):
    """Collapse each community into one node, keeping the weights between them."""
    index = {c: i for i, c in enumerate(sorted(set(community)))}
    next_members = [[] for _ in index]
    next_adjacency = [{} for _ in index]
    for node, neighbours in enumerate(adjacency):
        source = index[community[node]]
        next_members[source] += members[node]
        for neighbour, weight in neighbours.items():
            target = index[community[neighbour]]
            next_adjacency[source][target] = next_adjacency[source].get(target, 0) + weight
    # Internal edges were counted from both ends.
    for i, neighbours in enumerate(next_adjacency):
        if i in neighbours:
            neighbours[i] /= 2
    return next_adjacency, next_members


def louvain(size, edges, resolution=1.0):
    """
    Communities of an undirected weighted graph, as lists of node indices.

    ``edges`` maps ``(a, b)`` pairs of indices in ``range(size)`` to positive
    weights. Deterministic: nodes and communities are visited in index order.
    """
    adjacency = [{} for _ in range(size)]
    for (a, b), weight in edges.items():
        adjacency[a][b] = adjacency[a].get(b, 0) + weight
        adjacency[b][a] = adjacency[b].get(a, 0) + weight
    members = [[i] for i in range(size)]
    if not edges:
        return members
    while True:
        community, improved = _local_moves(adjacency, resolution)
        if not improved:
            return members
        adjacency, members = _aggregate(adjacency, members, community)


def communities(items):
    """Groups of related items; an item related to nothing stays alone."""
    position = {str(item.id): i for i, item in enumerate(items)}
    close, best = {}, {}
    for i, item in enumerate(items):
        vector = storage.item_vector(item)
        if vector is None:
            continue
        neighbours = storage.nearest_items(
            vector, items, k=NEIGHBOURS, min_similarity=WEAK_FLOOR, exclude_item=item
        )
        if neighbours:
            best[i] = position[neighbours[0].item_id]
        for neighbour in neighbours:
            close[(i, position[neighbour.item_id])] = neighbour.similarity

    edges = {}
    for (i, j), similarity in close.items():
        # A pair counts when it is close enough, or when the two files are
        # each other's closest: two lonely files on the same subject make a
        # topic, a file merely less far than the rest does not.
        if similarity >= FLOOR or (best.get(i) == j and best.get(j) == i):
            # Weights start near zero at the floor so weak ties barely count.
            edges[(min(i, j), max(i, j))] = similarity - WEAK_FLOOR + 0.05
    return [[items[i] for i in sorted(group)] for group in louvain(len(items), edges)]


def keywords(items, limit=3):
    """The most frequent meaningful words of the items' titles."""
    words = Counter()
    for item in items:
        for token in re.findall(r"[\wÀ-ÿ'-]+", item.title.lower()):
            word = token.split("'")[-1]
            if len(word) > 2 and word not in STOPWORDS and not word.isdigit():
                words[word] += 1
    return [word for word, _ in words.most_common(limit)]


def name_topic(items, client):
    """A short French name for a group of items, from Albert or its keywords."""
    lines = []
    for item in items[:8]:
        chunk = ItemChunk.objects.filter(item=item).order_by("index").first()
        excerpt = " ".join(chunk.text.split()[:40]) if chunk else ""
        lines.append(f"- {item.title} : {excerpt}")
    prompt = (
        "Voici des documents d'un même dossier de travail, avec un extrait de chacun.\n"
        + "\n".join(lines)
        + "\n\nDonne le sujet commun à ces documents en 2 à 4 mots, en français, "
        "comme un nom de dossier. Réponds uniquement par ce nom, sans guillemets ni point."
    )
    if client is not None:
        try:
            answer = client.chat(prompt).splitlines()[0].strip(" \"'«».")
            if answer:
                return answer[:MAX_LABEL_LENGTH]
        except (AlbertError, IndexError):
            logger.warning("Albert could not name a topic, falling back to keywords")
    words = keywords(items)
    return " · ".join(word.capitalize() for word in words) or "Divers"


def _matching_topic(previous, ids, used):
    """The automatic topic whose former files overlap most with ``ids``, if enough."""
    best, best_overlap = None, REUSE_OVERLAP
    for topic_id, members in previous.items():
        overlap = len(members & ids) / len(members | ids)
        if topic_id not in used and overlap >= best_overlap:
            best, best_overlap = topic_id, overlap
    return Topic.objects.get(id=best) if best else None


def assign_topics(client=None):
    """
    Recompute the automatic topics of all indexed files, then their links.

    ``client`` names new topics (an AlbertClient by default, none when the key
    is missing). Returns a TopicReport.
    """
    if client is None:
        try:
            client = AlbertClient()
        except AlbertError:
            client = None

    report = TopicReport()
    files = live_files()
    given = ItemTopic.objects.filter(topic__automatic=False).values("item_id")
    items = list(files.filter(id__in=ItemChunk.objects.values("item_id")).exclude(id__in=given))
    report.files = len(items)

    previous = {}
    for membership in ItemTopic.objects.filter(item__in=items, topic__automatic=True):
        previous.setdefault(membership.topic_id, set()).add(membership.item_id)

    with transaction.atomic():
        used = set()
        for group in communities(items):
            ids = {item.id for item in group}
            if len(group) < 2:
                ItemTopic.objects.filter(item__in=group, topic__automatic=True).delete()
                continue
            topic = _matching_topic(previous, ids, used)
            if topic is not None:
                report.reused += 1
            else:
                topic = Topic.objects.create(
                    label=name_topic(group, client), keywords=keywords(group), automatic=True
                )
                report.named += 1
            used.add(topic.id)
            for item in group:
                ItemTopic.objects.update_or_create(item=item, defaults={"topic": topic})
            report.topics.append(topic.label)
        Topic.objects.filter(automatic=True, memberships__isnull=True).delete()

    # "Unexpected" flags depend on topics: rewrite the links with the new ones.
    for item in items:
        link_item(item, files)
    return report
