"""
Read side of the file graph: what the storage contains, for the frontend.

GET /api/v1.0/graph/ returns the files the user can read, the links between
them and the subjects their owner created, with the files each one holds.
Every indexed pair is linked, the weight saying how close the two contents
are. A file appears as soon as it is uploaded, with a status saying whether
its content is analysed yet. Links to files the user cannot read are simply
left out: the graph never reveals the existence of a file to someone who
cannot open it.

``?folder=<id>`` draws one folder instead of the whole drive: the files it
holds at any depth, and only the links between those. A tie to a file left
outside the folder is dropped rather than drawn towards nothing, so the
answer reads as a drive of its own.
"""

import hashlib
from collections import Counter
from datetime import timedelta

from django.core.exceptions import ValidationError as DjangoValidationError
from django.conf import settings
from django.db.models import Exists, OuterRef, Q, Subquery
from django.utils import timezone

from rest_framework import views
from rest_framework.exceptions import NotFound
from rest_framework.response import Response

from core import models
from core.api import permissions

from graph.models import ItemChunk, ItemIndex, ItemLink, ItemTopic, Topic
from graph.services.brief import MAX_NEIGHBOURS, brief
from graph.services.chunking import hash_text, readable_title
from graph.services.extraction import is_extractable
from graph.services.scope import readable_by

# The graph draws at most this many files. Measured: 4 000 nodes cost 2.8 MB
# of JSON (400 KB once nginx compresses it) and 10 ms a frame in the browser.
MAX_NODES = 4000
# How many neighbours of a file travel to the page. The storage keeps more
# (GRAPH_LINKS_PER_FILE); beyond ten the page draws threads nobody reads and
# the answer doubles in size.
LINKS_SENT_PER_FILE = 10
# A file waiting longer than this was never queued for analysis: the page
# stops showing it as being analysed, and stops polling for it.
PENDING_GRACE = timedelta(minutes=15)


def scope_folder(user, folder_id):
    """
    The folder the graph is restricted to, or None for the whole drive.

    A folder the user cannot read is answered as a missing one: whether it
    exists is itself something the graph must not reveal.
    """
    if not folder_id:
        return None
    try:
        folder = (
            readable_by(user)
            .filter(
                id=folder_id,
                type=models.ItemTypeChoices.FOLDER,
                ancestors_deleted_at__isnull=True,
            )
            .first()
        )
    except (DjangoValidationError, ValueError) as exc:
        # Not even an id: the same answer as an id pointing at nothing.
        raise NotFound("No such folder.") from exc
    if folder is None:
        raise NotFound("No such folder.")
    return folder


def scope_trail(user, folder):
    """
    The folder's name, preceded by the folders above it, root first.

    "Divers" says little when three drives hold one. Only the ancestors the
    reader can open are named: a folder shared on its own sits under parents
    whose very names they have no right to know, so the trail starts where
    their access starts.
    """
    above = (
        folder.ancestors()
        .filter(id__in=readable_by(user).values("id"))
        .order_by("path")
        .values_list("title", flat=True)
    )
    return [*above, folder.title]


def graph_folders(user):
    """
    Every folder the reader can open, each named by the trail above it.

    The list is the whole drive's, whatever the graph is currently scoped to:
    it exists so the reader can step sideways into another folder without
    leaving the page, not only downwards into this one.

    The trails are built from the paths the folders already carry rather than
    asked per folder, so the whole list costs one query. A folder whose parent
    the reader cannot open simply starts its trail lower, the way
    ``scope_trail`` does.
    """
    folders = list(
        readable_by(user)
        .filter(
            type=models.ItemTypeChoices.FOLDER,
            ancestors_deleted_at__isnull=True,
        )
        .only("id", "title", "path")
        .order_by("path")
    )
    titles = {str(folder.id): folder.title for folder in folders}
    return [
        {
            "id": str(folder.id),
            "title": folder.title,
            "trail": [titles[label] for label in str(folder.path).split(".") if label in titles],
        }
        for folder in folders
    ]


def graph_items(user, root=None):
    """
    The items the user can read that belong in the graph.

    Every readable file is there, even one whose text is not analysed yet:
    it shows up as soon as it is uploaded and gains its links afterwards.
    Items of another type only appear once they carry chunks or links.

    ``root`` narrows the graph to what that folder holds, at any depth. The
    folder itself stays out: it is the frame of the drawing, not a node of it.
    """
    in_graph = (
        Q(type=models.ItemTypeChoices.FILE)
        | Q(Exists(ItemChunk.objects.filter(item=OuterRef("pk"))))
        | Q(Exists(ItemLink.objects.filter(source=OuterRef("pk"))))
        | Q(Exists(ItemLink.objects.filter(target=OuterRef("pk"))))
    )
    items = readable_by(user).annotate_user_roles(user)
    if root is not None:
        # The materialised path answers in one index scan, so a folder ten
        # levels down costs what the whole drive costs. The folder itself is
        # left out: it frames the drawing rather than sitting in it.
        items = items.filter(path__descendants=root.path).exclude(id=root.id)
    return (
        items
        # Not filter_non_deleted: files inside a trashed folder only carry
        # ancestors_deleted_at, and must leave the graph with their folder.
        .filter(ancestors_deleted_at__isnull=True)
        .filter(in_graph)
        .annotate(
            indexed=Exists(ItemChunk.objects.filter(item=OuterRef("pk"))),
            index_state=Subquery(ItemIndex.objects.filter(item=OuterRef("pk")).values("state")[:1]),
        )
        .select_related("creator")
        .order_by("-updated_at")
        .distinct()[:MAX_NODES]
    )


# How much of a file's first passage the fingerprint reads. Passages are cut
# at GRAPH_CHUNK_WORDS, which is well under this: it is a bound, not a cut.
CONTENT_CHARS = 8000


def content_keys(items):
    """
    A fingerprint of each file's content, as ``{item_id: key}``.

    Two files holding the same passages carry the same key, and that is what
    "the same document twice" means ‒ the weight of a link cannot say it. A
    link's weight is the whole of one file against the nearest passage of the
    other, so two copies of a twelve-passage document sit at 0.96 and never
    reach 1. The card offers to drop a copy, which is not something to offer
    on a resemblance.

    The file's name is taken back out of its first passage before hashing.
    The pipeline reads a file as "name, then text", so that a photo named
    after what it shows is placed by that name when it holds nothing else ‒
    but here the name is exactly what has to go: the same note saved under
    two names is the duplicate a reader actually has, and with the name left
    in, the two came out as different documents.
    """
    heads = dict(
        ItemChunk.objects.filter(item__in=items, index=0).values_list("item_id", "text")
    )
    tails = {}
    for item_id, text_hash in (
        ItemChunk.objects.filter(item__in=items, index__gt=0)
        .order_by("item_id", "text_hash")
        .values_list("item_id", "text_hash")
    ):
        tails.setdefault(item_id, []).append(text_hash)

    keys = {}
    for item in items:
        head = heads.get(item.id)
        if head is None:
            continue
        name = readable_title(item.title)
        body = head[len(name) :].lstrip() if head.startswith(name) else head
        parts = [hash_text(body[:CONTENT_CHARS]), *tails.get(item.id, [])]
        # Not a secret: a short digest that two identical files agree on.
        keys[item.id] = hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]
    return keys


def item_status(item):
    """
    Where an item stands in the pipeline, for the page to show it right.

    ``indexed``: its passages are stored, links and topic follow.
    ``pending``: being analysed, or waiting for the worker to pick it up.
    ``empty``: analysed, but it holds no text to compare (a photo).
    ``failed``: the analysis broke; it will be retried on the next save.
    ``skipped``: nothing to analyse (video, archive, file too big).
    ``idle``: never analysed and not waiting for it either, so the page stops
    expecting links: a file written before indexing existed, or one whose
    analysis was never queued.
    """
    if item.indexed:
        return "indexed"
    state = item.index_state
    if state in (ItemIndex.State.EMPTY, ItemIndex.State.FAILED, ItemIndex.State.SKIPPED):
        return state
    if not is_extractable(item):
        return "skipped"
    if state == ItemIndex.State.PENDING or item.updated_at > timezone.now() - PENDING_GRACE:
        return "pending"
    return "idle"


def serialize_item(item, topics_by_item, user, keys):
    """The node shape the graph page expects."""
    creator = item.creator
    return {
        "id": str(item.id),
        "title": item.title,
        "mimetype": item.mimetype or ("application/x-directory" if item.type == "folder" else ""),
        "size": item.size or 0,
        # When the file landed in the drive, and when it was last touched: the
        # page draws a dot by its age and lets the reader filter on it, which
        # is a question about the arrival, not about the last save.
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
        "creator": (creator.full_name or creator.email) if creator else "",
        # Who the author is, as an id: two colleagues can share a name, and
        # the page groups the files by author.
        "creator_id": str(creator.id) if creator else "",
        # What the reader holds on this file ‒ "owner", "administrator",
        # "editor", "reader", or "" when they only reach it through a link.
        # The page colours a dot by it: whose file this is, at a glance.
        "role": item.get_role(user) or "",
        "status": item_status(item),
        # Files holding exactly the same passages share this key: the page
        # marks them as the same document and offers to keep one.
        "content": keys.get(item.id, ""),
        # The subjects this file fell into, closest first.
        "topics": topics_by_item.get(item.id, []),
    }


def serialize_links(item_ids):
    """
    The links between those files, the closest ones of each.

    Read as plain rows rather than model instances: on a drive of 1 700 files
    that is 0.3 second instead of 1.4 for the same answer.
    """
    rows = (
        ItemLink.objects.filter(source_id__in=item_ids, target_id__in=item_ids)
        .order_by("-weight")
        .values_list("source_id", "target_id", "weight", "kind", "evidence")
    )
    sent = Counter()
    links = []
    for source, target, weight, kind, evidence in rows.iterator(chunk_size=2000):
        if sent[source] >= LINKS_SENT_PER_FILE:
            continue
        sent[source] += 1
        link = {
            "source": str(source),
            "target": str(target),
            "weight": weight,
            "kind": kind,
        }
        if evidence:
            link["evidence"] = evidence
        links.append(link)
    return links


class GraphView(views.APIView):
    """Nodes and weighted links of the current user's file graph."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        """GET /api/v1.0/graph/?folder=<id>"""
        folder = scope_folder(request.user, request.query_params.get("folder"))
        items = list(graph_items(request.user, root=folder))
        item_ids = {item.id for item in items}

        topics = Topic.objects.filter(creator=request.user)
        topics_by_item = {}
        # The score is a share of the best answer of its subject, so the
        # scores of two subjects compare: a file in several is drawn in the
        # one it belongs to most.
        for membership in ItemTopic.objects.filter(item_id__in=item_ids, topic__in=topics).order_by(
            "-score"
        ):
            topics_by_item.setdefault(membership.item_id, []).append(
                {
                    "id": str(membership.topic_id),
                    "score": membership.score,
                    "pinned": membership.pinned,
                }
            )

        keys = content_keys(items)
        return Response(
            {
                "files": [
                    serialize_item(item, topics_by_item, request.user, keys) for item in items
                ],
                "links": serialize_links(item_ids),
                # Every folder that can be drawn, so the page can offer a
                # different one without sending the reader back to the explorer.
                "folders": graph_folders(request.user),
                "topics": [
                    {
                        "id": str(topic.id),
                        "name": topic.name,
                        "description": topic.description,
                        # How well the drive answers this subject at all, in the
                        # reranker's own units. A file's score is a share of its
                        # subject's best answer, so it says nothing about whether
                        # that best answer was any good: a subject nobody's drive
                        # is about still crowns its least bad file at 1.00. These
                        # compare between subjects, which is the only way to tell
                        # "Events" on a drive of case law from "droit" on it.
                        "strength": (topic.cut or 0) / settings.GRAPH_TOPIC_RERANK_RATIO,
                    }
                    for topic in topics
                ],
                # What the page puts in its title, and what it offers to leave.
                "scope": (
                    {
                        "id": str(folder.id),
                        "title": folder.title,
                        "path": scope_trail(request.user, folder),
                    }
                    if folder is not None
                    else None
                ),
            }
        )


class FileBriefView(views.APIView):
    """
    What a file says about a subject, and what ties it to its neighbours.

    GET /api/v1.0/graph/files/<id>/brief/?subject=<words>&with=<id>,<id>

    Read on its own rather than sent with the graph: it costs a reading of
    the files, and a drive of nine hundred would spend it on the eight
    hundred cards nobody opens. The card asks for it when it opens.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, item_id):
        """The summary of one file and a sentence per neighbour."""
        readable = readable_by(request.user).filter(ancestors_deleted_at__isnull=True)
        item = readable.filter(pk=item_id).first()
        if item is None:
            raise NotFound("No such file.")

        asked = [one for one in request.query_params.get("with", "").split(",") if one]
        # Only the files the reader can open: the card must not name, nor
        # summarise, a file the graph would never have shown them.
        try:
            found = {str(one.id): one for one in readable.filter(pk__in=asked[:MAX_NEIGHBOURS])}
        except (DjangoValidationError, ValueError):
            found = {}
        neighbours = [found[one] for one in asked if one in found]

        return Response(brief(item, neighbours, subject=request.query_params.get("subject", "")))
