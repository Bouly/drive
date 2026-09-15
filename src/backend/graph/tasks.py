"""
Celery tasks of the file graph: index a file once its upload is safe.

Chained here: extraction (1), chunking (2), embeddings (3), storage (4) and
semantic links (5). Albert errors are retried with a backoff.
"""

from celery import shared_task

from core.models import Item, ItemTypeChoices

from graph.services import storage
from graph.services.albert import AlbertClient, AlbertError
from graph.services.chunking import chunk_text
from graph.services.extraction import ExtractionSkipped, extract_text, is_extractable
from graph.services.linking import link_item


@shared_task(autoretry_for=(AlbertError,), retry_backoff=True, max_retries=5)
def index_item(item_id):
    """Extract, chunk, embed and link an item. Idempotent: rerunning replaces its chunks."""
    item = Item.objects.get(pk=item_id)

    if not is_extractable(item):
        return

    try:
        text = extract_text(item)
    except ExtractionSkipped:
        return

    chunks = chunk_text(text)
    if not chunks:
        return

    for chunk, vector in zip(chunks, AlbertClient().embed([c.text for c in chunks]), strict=True):
        chunk.embedding = vector

    storage.save_chunks(item, chunks)

    # Links are stored for everyone; the API filters by access rights when
    # reading. Trashed files must not become targets though.
    link_item(item, Item.objects.filter_non_deleted().filter(type=ItemTypeChoices.FILE))
