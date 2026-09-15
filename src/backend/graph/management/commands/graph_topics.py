"""
Recompute the automatic topics of the graph now (step 6).

    python manage.py graph_topics

Groups indexed files by community, names new groups with Albert's chat model
(or their title keywords without a key) and rewrites the links.
"""

from django.core.management.base import BaseCommand

from graph.services.topics import assign_topics


class Command(BaseCommand):
    """Group indexed files into named topics."""

    help = "Recompute the automatic topics of the file graph."

    def handle(self, *args, **options):
        report = assign_topics()
        self.stdout.write(
            self.style.SUCCESS(
                f"{report.files} files, {len(report.topics)} topics "
                f"({report.named} named, {report.reused} kept): {', '.join(report.topics)}"
            )
        )
