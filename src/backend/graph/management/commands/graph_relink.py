"""
Recompute the semantic links of every indexed file, without calling Albert.

    python manage.py graph_relink

Useful after changing the linking rules: vectors are already stored, only
the neighbour search and the links are redone.
"""

from django.core.management.base import BaseCommand

from graph.services.linking import indexed_files, relink_all


class Command(BaseCommand):
    """Rewrite the links of all live files that have chunks."""

    help = "Recompute the semantic links of every indexed file."

    def handle(self, *args, **options):
        links = relink_all()
        self.stdout.write(
            self.style.SUCCESS(f"{indexed_files().count()} files relinked, {links} links")
        )
