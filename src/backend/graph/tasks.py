"""
Celery tasks of the file graph: index a file once its upload is safe.

Chained here: extraction (1), chunking (2), embeddings (3), storage (4) and
semantic links (5); topics (6) are regrouped by a follow-up task. Albert
errors are retried with a backoff.
"""

import re

from django.core.cache import cache

from celery import shared_task

from core.models import Item

from graph.models import ItemIndex
from graph.services import storage
from graph.services.albert import AlbertClient, AlbertError
from graph.services.chunking import chunk_text
from graph.services.extraction import (
    ExtractionError,
    ExtractionSkipped,
    extract_text,
    is_extractable,
)
from graph.services.linking import link_item, live_files, relink_neighbours
from graph.services.topics import assign_topics

TOPICS_LOCK = "graph-refresh-topics"
# Seconds to wait before regrouping, so several uploads are grouped in one run.
TOPICS_DELAY = 10


def readable_title(title):
    """
    A file name as words: no extension, no dashes or underscores.

    "Poop-emoji-scaled.jpg" becomes "Poop emoji scaled", so files are not
    drawn together by a shared extension.
    """
    return re.sub(r"\.[A-Za-z0-9]{1,8}$", "", title).replace("-", " ").replace("_", " ").strip()


def _remember(item, state, detail=""):
    """Record where the item stands, so the page never waits on it forever."""
    ItemIndex.objects.update_or_create(item=item, defaults={"state": state, "detail": detail})


@shared_task(autoretry_for=(AlbertError,), retry_backoff=True, max_retries=5)
def index_item(item_id):
    """Extract, chunk, embed and link an item. Idempotent: rerunning replaces its chunks."""
    item = Item.objects.get(pk=item_id)

    if not is_extractable(item):
        _remember(
            item, ItemIndex.State.SKIPPED, f"{item.mimetype or 'unknown type'}, {item.size} B"
        )
        return

    _remember(item, ItemIndex.State.PENDING)
    try:
        text = extract_text(item)
    except ExtractionSkipped as exc:
        _remember(item, ItemIndex.State.SKIPPED, str(exc))
        return
    except ExtractionError as exc:
        _remember(item, ItemIndex.State.FAILED, str(exc))
        raise

    # The title is part of what a file is about, and it is all a photo has.
    title = readable_title(item.title)
    chunks = chunk_text(f"{title}\n\n{text}" if text.strip() else title)
    if not chunks:
        # No text, no title: nothing to compare this file with.
        _remember(item, ItemIndex.State.EMPTY, f"{len(text)} characters extracted")
        storage.delete_chunks(item)
        return
    detail = f"{len(chunks)} passages" if text.strip() else "title only"

    for chunk, vector in zip(chunks, AlbertClient().embed([c.text for c in chunks]), strict=True):
        chunk.embedding = vector

    storage.save_chunks(item, chunks)
    _remember(item, ItemIndex.State.DONE, detail)

    # Links are stored for everyone; the API filters by access rights when
    # reading. Trashed files must not become targets though.
    candidates = live_files()
    link_item(item, candidates)
    # Files indexed earlier may now have this one among their closest.
    relink_neighbours(item, candidates)
    # Topics depend on the whole graph: regroup once a burst of uploads settles.
    refresh_topics.apply_async(countdown=TOPICS_DELAY)


@shared_task(bind=True, max_retries=30)
def refresh_topics(self):
    """Recompute the automatic topics; one run at a time, others wait their turn."""
    if not cache.add(TOPICS_LOCK, "1", timeout=600):
        raise self.retry(countdown=TOPICS_DELAY)
    try:
        assign_topics()
    finally:
        cache.delete(TOPICS_LOCK)
