"""What the graph API accepts and returns for subjects."""

from django.utils.translation import gettext_lazy as _

from rest_framework import serializers

from graph.models import Topic


class TopicSerializer(serializers.ModelSerializer):
    """A subject, with how many files ended up in it."""

    files = serializers.IntegerField(source="memberships.count", read_only=True)

    def validate_name(self, value):
        """Report a duplicate before the database rejects the write."""
        # Existing case variants may predate this validation. A description
        # edit should remain possible without forcing their owner to rename.
        if self.instance and value == self.instance.name:
            return value
        subjects = Topic.objects.filter(
            creator=self.context["request"].user, name__iexact=value
        )
        if self.instance:
            subjects = subjects.exclude(pk=self.instance.pk)
        if subjects.exists():
            raise serializers.ValidationError(
                _("A subject with this name already exists."), code="unique"
            )
        return value

    class Meta:
        model = Topic
        fields = ["id", "name", "description", "files", "created_at"]
        read_only_fields = ["id", "files", "created_at"]
