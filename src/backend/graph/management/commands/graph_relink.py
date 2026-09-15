"""
Recompute the semantic links of every indexed file, without calling Albert.

    python manage.py graph_relink

Useful after changing the linking rules: vectors are already stored, only
the neighbour search and the links are redone.
"""

from django.core.management.base import BaseCommand

from graph.models import ItemChunk
from graph.services.linking import link_item, live_files


class Command(BaseCommand):
    """Rewrite the links of all live files that have chunks."""

    help = "Recompute the semantic links of every indexed file."

    def handle(self, *args, **options):
        candidates = live_files()
        items = candidates.filter(id__in=ItemChunk.objects.values("item_id"))
        links = 0
        for item in items:
            links += link_item(item, candidates)
        self.stdout.write(self.style.SUCCESS(f"{items.count()} files relinked, {links} links"))
