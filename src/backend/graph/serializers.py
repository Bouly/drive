"""What the graph API accepts and returns for subjects."""

from rest_framework import serializers

from graph.models import Topic


class TopicSerializer(serializers.ModelSerializer):
    """A subject, with how many files ended up in it."""

    files = serializers.IntegerField(source="memberships.count", read_only=True)

    class Meta:
        model = Topic
        fields = ["id", "name", "description", "files", "created_at"]
        read_only_fields = ["id", "files", "created_at"]
