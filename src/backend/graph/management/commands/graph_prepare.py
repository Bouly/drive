"""
Run the extraction, chunking and embedding steps on real files and show the
result, without storing anything. Handy to check the services and to pick
chunk sizes:

    python manage.py graph_prepare --latest 3
    python manage.py graph_prepare <item id> --no-embed --show 2
"""

from django.core.management.base import BaseCommand, CommandError

from core import models

from graph.pipeline import prepare_item
from graph.services.embeddings import EmbeddingError, get_embedder
from graph.services.extraction import ExtractionError, is_extractable


class Command(BaseCommand):
    """Prepare items through the graph pipeline and print what came out."""

    help = "Extract, chunk and embed items to check the graph pipeline."

    def add_arguments(self, parser):
        parser.add_argument("item_ids", nargs="*", help="Item ids to prepare")
        parser.add_argument("--latest", type=int, default=0, help="Prepare the N latest files")
        parser.add_argument("--no-embed", action="store_true", help="Skip the embedding step")
        parser.add_argument("--show", type=int, default=1, help="Print the first N chunks")

    def handle(self, *args, **options):
        items = list(models.Item.objects.filter(id__in=options["item_ids"]))
        if options["latest"]:
            latest = models.Item.objects.filter(
                type=models.ItemTypeChoices.FILE,
                upload_state=models.ItemUploadStateChoices.READY,
            ).order_by("-created_at")[: options["latest"]]
            items.extend(latest)
        if not items:
            raise CommandError("No item to prepare: pass ids or --latest N")

        embedder = None
        if not options["no_embed"]:
            embedder = get_embedder()
            try:
                info = embedder.info()
                self.stdout.write(
                    f"Embedding model: {info.get('model_id')} ({embedder.dimension} dims)"
                )
            except Exception as exc:
                raise CommandError(f"Embedding service not reachable: {exc}") from exc

        for item in items:
            self.stdout.write(
                self.style.MIGRATE_HEADING(
                    f"{item.title} ({item.mimetype}, {item.size or 0} bytes)"
                )
            )
            if not is_extractable(item):
                self.stdout.write("  skipped: not a ready file, or type/size not allowed")
                continue
            try:
                prepared = prepare_item(item, embed=not options["no_embed"], embedder=embedder)
            except (ExtractionError, EmbeddingError) as exc:
                self.stdout.write(self.style.ERROR(f"  failed: {exc}"))
                continue
            words = len(prepared.text.split())
            self.stdout.write(f"  {words} words -> {len(prepared.chunks)} chunks")
            for chunk in prepared.chunks[: options["show"]]:
                preview = chunk.text[:300].replace("\n", " ")
                dims = f", {len(chunk.embedding)} dims" if chunk.embedding else ""
                self.stdout.write(f"  [{chunk.index}] {chunk.word_count} words{dims}: {preview}…")
