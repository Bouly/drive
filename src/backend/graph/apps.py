"""Graph app configuration."""

from django.apps import AppConfig


class GraphConfig(AppConfig):
    """Configuration class for the graph app (file graph: extraction, embeddings, links)."""

    name = "graph"

    def ready(self):
        """Connect the receivers keeping the links in step with the trash."""
        from graph import signals  # noqa: PLC0415, F401  pylint: disable=unused-import
