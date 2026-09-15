"""
Read side of the file graph: what the storage contains, for the frontend.

GET /api/v1.0/graph/ returns the files the user can read that are part of
the graph (they have passages or links), the links between those files and
the topics they belong to. Links to files the user cannot read are simply
left out: the graph never reveals the existence of a file to someone who
cannot open it.
"""

from django.db.models import Exists, OuterRef, Q

from rest_framework import views
from rest_framework.response import Response

from core import models
from core.api import permissions

from graph.models import ItemChunk, ItemLink, ItemTopic, Topic

MAX_NODES = 1000


def graph_items(user):
    """The items the user can read that take part in the graph."""
    in_graph = (
        Q(Exists(ItemChunk.objects.filter(item=OuterRef("pk"))))
        | Q(Exists(ItemLink.objects.filter(source=OuterRef("pk"))))
        | Q(Exists(ItemLink.objects.filter(target=OuterRef("pk"))))
    )
    return (
        models.Item.objects.readable_per_se(user)
        .filter_non_deleted()
        .filter(in_graph)
        .select_related("creator")
        .order_by("-updated_at")
        .distinct()[:MAX_NODES]
    )


def serialize_item(item, topic_by_item):
    """The node shape the graph page expects (same as its demo dataset)."""
    creator = item.creator
    return {
        "id": str(item.id),
        "title": item.title,
        "mimetype": item.mimetype or ("application/x-directory" if item.type == "folder" else ""),
        "size": item.size or 0,
        "updated_at": item.updated_at.isoformat(),
        "creator": (creator.full_name or creator.email) if creator else "",
        "cluster": topic_by_item.get(item.id),
    }


class GraphView(views.APIView):
    """Nodes, links and topics of the current user's file graph."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        """GET /api/v1.0/graph/"""
        items = list(graph_items(request.user))
        item_ids = {item.id for item in items}

        topic_by_item = {
            membership.item_id: str(membership.topic_id)
            for membership in ItemTopic.objects.filter(item_id__in=item_ids)
        }
        topics = Topic.objects.filter(id__in=set(topic_by_item.values())).order_by("label")

        links = ItemLink.objects.filter(source_id__in=item_ids, target_id__in=item_ids).order_by(
            "-weight"
        )

        return Response(
            {
                "files": [serialize_item(item, topic_by_item) for item in items],
                "links": [
                    {
                        "source": str(link.source_id),
                        "target": str(link.target_id),
                        "weight": link.weight,
                        "kind": link.kind,
                        "surprising": link.surprising,
                        "reason": link.reason,
                        "evidence": link.evidence,
                    }
                    for link in links
                ],
                "clusters": [
                    {"id": str(topic.id), "label": topic.label, "keywords": topic.keywords}
                    for topic in topics
                ],
            }
        )
