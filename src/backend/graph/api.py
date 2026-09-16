"""
Read side of the file graph: what the storage contains, for the frontend.

GET /api/v1.0/graph/ returns the files the user can read and the links
between them. Every indexed pair is linked, the weight saying how close the
two contents are. A file appears as soon as it is uploaded, with a status
saying whether its content is analysed yet. Links to files the user cannot
read are simply left out: the graph never reveals the existence of a file to
someone who cannot open it.
"""

from django.db.models import Exists, OuterRef, Q, Subquery

from rest_framework import views
from rest_framework.response import Response

from core import models
from core.api import permissions

from graph.models import ItemChunk, ItemIndex, ItemLink
from graph.services.extraction import is_extractable

MAX_NODES = 1000


def graph_items(user):
    """
    The items the user can read that belong in the graph.

    Every readable file is there, even one whose text is not analysed yet:
    it shows up as soon as it is uploaded and gains its links afterwards.
    Items of another type only appear once they carry chunks or links.
    """
    in_graph = (
        Q(type=models.ItemTypeChoices.FILE)
        | Q(Exists(ItemChunk.objects.filter(item=OuterRef("pk"))))
        | Q(Exists(ItemLink.objects.filter(source=OuterRef("pk"))))
        | Q(Exists(ItemLink.objects.filter(target=OuterRef("pk"))))
    )
    return (
        models.Item.objects.readable_per_se(user)
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


def item_status(item):
    """
    Where an item stands in the pipeline, for the page to show it right.

    ``indexed``: its passages are stored, links and topic follow.
    ``pending``: being analysed, or waiting for the worker to pick it up.
    ``empty``: analysed, but it holds no text to compare (a photo).
    ``failed``: the analysis broke; it will be retried on the next save.
    ``skipped``: nothing to analyse (video, archive, file too big).
    """
    if item.indexed:
        return "indexed"
    state = item.index_state
    if state in (ItemIndex.State.EMPTY, ItemIndex.State.FAILED, ItemIndex.State.SKIPPED):
        return state
    return "pending" if is_extractable(item) else "skipped"


def serialize_item(item):
    """The node shape the graph page expects."""
    creator = item.creator
    return {
        "id": str(item.id),
        "title": item.title,
        "mimetype": item.mimetype or ("application/x-directory" if item.type == "folder" else ""),
        "size": item.size or 0,
        "updated_at": item.updated_at.isoformat(),
        "creator": (creator.full_name or creator.email) if creator else "",
        "status": item_status(item),
    }


class GraphView(views.APIView):
    """Nodes and weighted links of the current user's file graph."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        """GET /api/v1.0/graph/"""
        items = list(graph_items(request.user))
        item_ids = {item.id for item in items}

        links = ItemLink.objects.filter(source_id__in=item_ids, target_id__in=item_ids).order_by(
            "-weight"
        )

        return Response(
            {
                "files": [serialize_item(item) for item in items],
                "links": [
                    {
                        "source": str(link.source_id),
                        "target": str(link.target_id),
                        "weight": link.weight,
                        "kind": link.kind,
                        "reason": link.reason,
                        "evidence": link.evidence,
                    }
                    for link in links
                ],
            }
        )
