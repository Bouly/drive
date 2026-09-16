"""
Subjects of the file graph: the user writes them, the files sort themselves.

Creating or editing a subject recomputes it at once: the answer already
carries how many files fell into it. Pinning a file both places it and
sharpens the subject, since pinned files define it.
"""

from django.shortcuts import get_object_or_404

from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from core import models
from core.api import permissions

from graph.models import ItemTopic, Topic
from graph.serializers import TopicSerializer
from graph.services.subjects import sort_files_into


class TopicViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """The subjects of the current user: /api/v1.0/graph/topics/."""

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = TopicSerializer

    def get_queryset(self):
        """A user only ever sees the subjects they created."""
        return Topic.objects.filter(creator=self.request.user)

    def perform_create(self, serializer):
        """Create the subject, then let the files fall into it."""
        serializer.save(creator=self.request.user)
        sort_files_into(serializer.instance)

    def perform_update(self, serializer):
        """Words changed: the subject moves, and so does what it holds."""
        serializer.save()
        sort_files_into(serializer.instance)

    @action(detail=True, methods=["post"], url_path="files")
    def pin(self, request, pk=None):  # pylint: disable=unused-argument
        """Pin a file to the subject: it stays, and it defines the subject."""
        topic = self.get_object()
        item = get_object_or_404(
            models.Item.objects.readable_per_se(request.user), pk=request.data.get("item")
        )
        ItemTopic.objects.update_or_create(
            item=item, topic=topic, defaults={"pinned": True, "score": 1.0}
        )
        sort_files_into(topic)
        return Response(TopicSerializer(topic).data)

    @action(detail=True, methods=["delete"], url_path=r"files/(?P<item_id>[^/.]+)")
    def unpin(self, request, pk=None, item_id=None):  # pylint: disable=unused-argument
        """Take a file out of the subject, and let the subject settle again."""
        topic = self.get_object()
        ItemTopic.objects.filter(topic=topic, item_id=item_id).delete()
        sort_files_into(topic)
        return Response(TopicSerializer(topic).data)
