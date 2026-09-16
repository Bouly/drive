"""
Celery tasks of the file graph: index a file once its upload is safe.

Chained here: extraction (1), chunking (2), embeddings (3), storage (4) and
semantic links (5). Albert errors are retried with a backoff.
"""

import logging
import re

from django.conf import settings
from django.core.files.storage import default_storage

from celery import shared_task

from core.models import Item, ItemTypeChoices

from graph.models import ItemIndex, Topic
from graph.services import storage
from graph.services.albert import AlbertClient, AlbertError
from graph.services.chunking import Chunk, chunk_text, hash_text
from graph.services.extraction import (
    ExtractionError,
    ExtractionSkipped,
    extract_text,
    is_extractable,
)
from graph.services.linking import forget_item, live_files, relink_around
from graph.services.subjects import sort_into_subjects

logger = logging.getLogger(__name__)


def readable_title(title):
    """
    A file name as words: no extension, no dashes or underscores.

    "Poop-emoji-scaled.jpg" becomes "Poop emoji scaled", so files are not
    drawn together by a shared extension.
    """
    return re.sub(r"\.[A-Za-z0-9]{1,8}$", "", title).replace("-", " ").replace("_", " ").strip()


def describe_picture(item):
    """
    What an image shows, in one sentence, or "" when it cannot be described.

    An OCR finds no text in a photo or a drawing; Albert's vision model says
    what is on it, and that sentence is what the graph compares.
    """
    mimetype = item.mimetype or ""
    if not mimetype.startswith("image/") or (item.size or 0) > settings.GRAPH_VISION_MAX_FILE_SIZE:
        return ""
    try:
        with default_storage.open(item.file_key, "rb") as fd:
            raw = fd.read()
        description = AlbertClient().describe_image(raw, mimetype)
    except AlbertError as exc:
        logger.warning("Albert could not describe item %s: %s", item.id, exc)
        return ""
    logger.info("Described item %s: %s", item.id, description)
    return description


def picture_chunks(title, description):
    """
    The one passage of a described picture: its name, what Albert sees on it
    and the keywords of its subject, held together.

    The sentence and the keywords used to be two passages. That gave every
    picture a passage made of the wording shared by all descriptions ("on y
    voit…", "l'image montre…"), and since files are compared passage by
    passage, pictures were drawn to each other by that wording alone: on this
    drive a photo of bees sat closer to a photo of a bicycle (0.59) than to a
    PDF about bees (0.55). Stripping the wording changes nothing, as it is the
    shape of the sentence that carries; only the keywords standing next to it
    in the same passage do. They now win: that photo of a bicycle is closer to
    the PDF on bicycle upkeep (0.44) than to the photo of bees (0.42).
    """
    lines = [line.strip(" -•\t") for line in description.splitlines() if line.strip()]
    text = " ".join(part for part in (title, *lines) if part).strip()
    if not text:
        return []
    return [Chunk(index=0, text=text, text_hash=hash_text(text))]


def _remember(item, state, detail=""):
    """Record where the item stands, so the page never waits on it forever."""
    ItemIndex.objects.update_or_create(item=item, defaults={"state": state, "detail": detail})


def embed_chunks(chunks):
    """
    Set the vector of each chunk, asking Albert only for passages whose text
    has never been embedded: a re-indexed file or a copy costs no call, and a
    passage repeated in a file is sent once.
    """
    vectors = storage.embeddings_by_hash(chunk.text_hash for chunk in chunks)
    missing = {}
    for chunk in chunks:
        if chunk.text_hash not in vectors:
            missing.setdefault(chunk.text_hash, chunk.text)
    if missing:
        embedded = AlbertClient().embed(list(missing.values()))
        vectors.update(zip(missing, embedded, strict=True))
    for chunk in chunks:
        chunk.embedding = vectors[chunk.text_hash]


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
    described = ""
    if not text.strip():
        described = describe_picture(item)
    if described:
        chunks = picture_chunks(title, described)
    else:
        chunks = chunk_text(f"{title}\n\n{text}" if text.strip() else title)
    if not chunks:
        # No text, no title: nothing to compare this file with.
        _remember(item, ItemIndex.State.EMPTY, f"{len(text)} characters extracted")
        storage.delete_chunks(item)
        return
    if described:
        detail = "described by Albert"
    elif text.strip():
        detail = f"{len(chunks)} passages"
    else:
        detail = "title only"

    embed_chunks(chunks)
    storage.save_chunks(item, chunks)
    _remember(item, ItemIndex.State.DONE, detail)

    # A newcomer only shifts the neighbourhood it lands in, so only those
    # files are relinked. Links are stored for everyone; the API filters by
    # access rights when reading, and trashed files are never targets.
    relink_around(item, live_files())
    # The file also falls into the subjects it fits, without touching theirs.
    sort_into_subjects(item, Topic.objects.exclude(vector=None))


@shared_task
def forget_from_graph(item_id, restore=False):
    """
    Take a file (or every file of a folder) out of the graph, or bring it back.

    Trashing a file must not leave its neighbours pointing at it. Descendants
    of a folder are updated in bulk by ``soft_delete``, which sends no signal
    of its own, hence the walk here.
    """
    item = Item.objects.get(pk=item_id)
    if item.type == ItemTypeChoices.FOLDER:
        files = list(item.descendants().filter(type=ItemTypeChoices.FILE))
    else:
        files = [item]

    for file in files:
        if restore:
            index_item.delay(file.id)
        else:
            forget_item(file)
