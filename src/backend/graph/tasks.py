from celery import shared_task

from core.models import Item
from graph.services import storage
from graph.services.albert import AlbertClient, AlbertError
from graph.services.chunking import chunk_text
from graph.services.extraction import ExtractionSkipped, extract_text, is_extractable


@shared_task(bind=True, autoretry_for=(AlbertError,), retry_backoff=True, max_retries=5)
def index_item(self, item_id):
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

    vector = storage.item_vector(item)
    neighbours = storage.nearest_items(vector, Item.objects.all(), exclude_item=item)
    storage.replace_links(item, neighbours)