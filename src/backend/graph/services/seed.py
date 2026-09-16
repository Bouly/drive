"""
Fill a drive with public documents from the Albert API, as real files.

Albert holds the text of public corpora already extracted and cut into
passages (``/v1/documents``, ``/v1/documents/{id}/chunks``): Légifrance for
the law, and the Ministry of Labour's fiches for employment. Each document
becomes a file of the drive ‒ a text document, a spreadsheet or a scanned
page ‒ which the ordinary pipeline then reads, embeds and links like any
upload. Nothing is stored here that an uploaded file would not produce.

Three things are spread on purpose, because a bank where they are all equal
shows nothing of what the graph draws:

- the **format**, so the drive holds documents, sheets and scans;
- the **date** a file was added, which is the size of its dot;
- the **rights** the reader holds on it, which is its colour ‒ hence the
  three folders: one they own, one they may edit, one they only read.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import timedelta
from io import BytesIO
from itertools import zip_longest

from django.core.cache import cache
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone

from core import models

from graph.services.albert import AlbertError
from graph.services.documents import render
from graph.services.extraction import ExtractionError
from graph.services.linking import live_files, relink_all

logger = logging.getLogger(__name__)

# The bank is laid out in three folders, because the colour of a dot is what
# the reader may do with the file: a folder of their own, one a colleague
# lets them write in, one they only read.
FOLDERS = [
    ("Albert · Légifrance", models.RoleChoices.OWNER),
    ("Albert · Fiches travail-emploi", models.RoleChoices.EDITOR),
    ("Albert · Veille juridique partagée", models.RoleChoices.READER),
]

# The colleagues a shared drive has. They own the folders the reader does not,
# and they sign part of the files, so filtering by author answers something.
# The domain is plainly fictional: these accounts cannot log in.
COLLEAGUES = [
    ("camille.martin@graphe.demo", "Camille Martin"),
    ("noura.benali@graphe.demo", "Noura Benali"),
    ("lucas.ferrand@graphe.demo", "Lucas Ferrand"),
]

# Formats, in the order they are handed out: four documents, a sheet and a
# scan, over and over. Thirty documents come out as twenty, five and five.
FORMATS = ["document", "document", "sheet", "document", "picture", "document"]

# How far back the bank reaches. Dates are spread over this span rather than
# all set to today, so the dots differ in size and the date filter has
# something to cut ‒ a drive filled in one afternoon draws one single size.
SPAN_DAYS = 540

# Passages kept in a file. Albert cuts a code of law into hundreds of them;
# past a dozen the scan is a poster nobody reads and the sheet a wall.
MAX_PASSAGES = 12
# ...and for a scanned page, which has to stay a page.
MAX_PICTURE_PASSAGES = 5


@dataclass
class SeedReport:
    """What a seeding run produced."""

    items: int = 0
    chunks: int = 0
    links: int = 0
    skipped: int = 0
    failed: int = 0
    formats: dict = field(default_factory=dict)

    def count(self, kind):
        """Remember one more file written in that format."""
        self.formats[kind] = self.formats.get(kind, 0) + 1


def slugify(text, max_length=80):
    """A safe file name from a document title."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_length] or "document"


def readable_name(name):
    """
    A document name a reader would give a file.

    Albert names its documents after the file they came from, extension and
    all ("fiche-teletravail.pdf", "LEGITEXT000006072050.txt"): the extension
    of a source we do not keep has no business in the drive, and the graph
    draws that name next to its dot.
    """
    stem = re.sub(r"\.[A-Za-z0-9]{1,8}$", "", name).strip()
    stem = stem.replace("_", " ").replace("-", " ")
    stem = re.sub(r"\s+", " ", stem).strip()
    return (stem[:1].upper() + stem[1:]) if stem else "Document"


def get_or_create_user(email, full_name):
    """A colleague of the seeded drive, created once."""
    user = models.User.objects.filter(email=email).first()
    if user is None:
        user = models.User(
            email=email,
            full_name=full_name,
            short_name=full_name.split(" ")[0],
            is_active=True,
        )
        # No one logs in as them: they exist to own files and sign them.
        user.set_unusable_password()
        user.save()
    return user


def get_or_create_folder(title, owner, guests=()):
    """
    The folder holding part of the bank, owned by ``owner``.

    ``guests`` are ``(user, role)`` pairs given access to it: that is how the
    reader ends up with a folder they may only read, next to one of theirs.
    """
    folder = models.Item.objects.filter(
        creator=owner, title=title, type=models.ItemTypeChoices.FOLDER, deleted_at=None
    ).first()
    if folder is None:
        folder = models.Item.objects.create_child(
            title=title,
            type=models.ItemTypeChoices.FOLDER,
            creator=owner,
            link_reach=models.LinkReachChoices.RESTRICTED,
        )
    models.ItemAccess.objects.get_or_create(
        item=folder, user=owner, defaults={"role": models.RoleChoices.OWNER}
    )
    for guest, role in guests:
        if guest.id == owner.id:
            continue
        models.ItemAccess.objects.update_or_create(item=folder, user=guest, defaults={"role": role})
    return folder


def create_file(place, title, filename, rendered):
    """
    A ready file in the folder, its bytes in object storage.

    ``place`` is where it lands and whose it is ‒ folder, author, day added ‒
    and ``rendered`` what ``documents.render`` gave back for it.
    """
    folder, creator, added_at = place
    _extension, mimetype, content = rendered
    item = models.Item.objects.create_child(
        parent=folder,
        title=title[:255],
        type=models.ItemTypeChoices.FILE,
        filename=filename,
        mimetype=mimetype,
        size=len(content),
        creator=creator,
        upload_state=models.ItemUploadStateChoices.READY,
        # Explicit: the model has no default and an empty reach reads as "not
        # restricted", which would show the file to every logged-in user.
        link_reach=models.LinkReachChoices.RESTRICTED,
    )
    default_storage.save(item.file_key, BytesIO(content))
    # Two things are written after the fact. A new file is held pending until
    # an upload completes, and ours is already there. And the dates are the
    # point of the bank: they are what the dot's size says and what the date
    # filter cuts on ‒ ``update`` writes them without waking ``auto_now``,
    # which would put every file back to today.
    models.Item.objects.filter(pk=item.pk).update(
        upload_state=models.ItemUploadStateChoices.READY,
        created_at=added_at,
        updated_at=added_at,
    )
    item.refresh_from_db()
    return item


def passages_of(client, document, limit):
    """The text of an Albert document, passage by passage, longest kept first."""
    chunks = client.chunks(document["id"])
    texts = [chunk["content"].strip() for chunk in chunks if chunk.get("content", "").strip()]
    # A code of law starts on its table of contents: the passages that carry
    # something are the long ones, and their order in the document is kept.
    ranked = sorted(range(len(texts)), key=lambda i: len(texts[i]), reverse=True)[:limit]
    return [texts[i] for i in sorted(ranked)]


def seed_document(index, document, client, place, report):
    """
    Write one Albert document as a file of the drive, and return it.

    ``place`` says where it lands and whose it is: the folder, the colleague
    signing it, and the day it was added.
    """
    kind = FORMATS[index % len(FORMATS)]
    limit = MAX_PICTURE_PASSAGES if kind == "picture" else MAX_PASSAGES
    paragraphs = passages_of(client, document, limit)
    if not paragraphs:
        report.skipped += 1
        return None

    title = readable_name(document["name"])
    rendered = render(kind, title, paragraphs)
    extension = rendered[0]
    item = create_file(place, f"{title}.{extension}", f"{slugify(title)}.{extension}", rendered)
    report.items += 1
    report.count(kind)
    logger.info("Seeded %s as %s (%d passages)", title, extension, len(paragraphs))
    return item


def index_seeded(items, report):
    """
    Read every seeded file the way an upload is read, then link them all.

    The pipeline is not shortcut here: the sheet and the scan go through Tika
    like any file, so what the graph holds of a file is what that file says ‒
    a scan is placed by the text OCR reads on it, not by the text we happened
    to draw it from.
    """
    # Imported here: the tasks import models from apps this module is loaded by.
    from graph.tasks import (  # noqa: PLC0415  pylint: disable=import-outside-toplevel
        MEND_KEY,
        MEND_SCHEDULED,
        index_item,
    )

    for item in items:
        try:
            index_item(item.id)
        except (AlbertError, ExtractionError, OSError) as exc:
            # One document failing to be read is not worth losing the bank for.
            report.failed += 1
            logger.warning("Could not index seeded item %s: %s", item.id, exc)
    report.chunks = sum(item.chunks.count() for item in items)
    # Each file asked for its neighbourhood to be mended in a moment; the whole
    # web is about to be rewritten instead, so that request is called off
    # rather than left to run behind us on a graph that is already right.
    cache.delete(MEND_KEY)
    cache.delete(MEND_SCHEDULED)
    report.links = relink_all(live_files())


def places(user, colleagues, count):
    """
    Where each of the ``count`` files lands: folder, author and date added.

    The three folders take the files in turn, so the bank is spread over the
    rights the reader holds rather than piled in one place, and the author
    changes every few files: both are questions the graph can be filtered on,
    and a bank with one answer to them shows nothing.
    """
    folders = [
        get_or_create_folder(
            title,
            user if role == models.RoleChoices.OWNER else colleagues[i % len(colleagues)],
            guests=[] if role == models.RoleChoices.OWNER else [(user, role)],
        )
        for i, (title, role) in enumerate(FOLDERS)
    ]
    authors = [user, *colleagues]
    now = timezone.now()
    laid = []
    for i in range(count):
        # Spread from the oldest to the most recent, one step per file, with
        # the hour of the day moved along so two files never share a date.
        age = SPAN_DAYS - round(i * SPAN_DAYS / max(1, count - 1))
        added_at = now - timedelta(days=age, hours=(i * 7) % 24, minutes=(i * 13) % 60)
        laid.append((folders[i % len(folders)], authors[(i // 2) % len(authors)], added_at))
    return laid


def seed_from_albert(user, client, collection_ids, documents_per_collection=15):
    """
    Seed the user's graph from the given Albert public collections.

    Documents are taken collection by collection but laid out interleaved, so
    each folder, each author and each format holds both corpora rather than
    one each: the graph is then a drive, not two piles side by side.
    """
    report = SeedReport()
    collections = {str(c["id"]): c for c in client.collections()}
    known = set(
        models.Item.objects.filter(deleted_at=None, ancestors_deleted_at=None).values_list(
            "title", flat=True
        )
    )

    corpora = []
    for collection_id in collection_ids:
        name = collections.get(str(collection_id), {}).get("name", str(collection_id))
        kept = []
        # Asked in bulk, then filtered: Albert has no "skip the ones I hold".
        for document in client.documents(collection_id, limit=documents_per_collection * 3):
            title = readable_name(document["name"])
            if any(held == title or held.startswith(f"{title}.") for held in known):
                report.skipped += 1
                continue
            known.add(title)
            kept.append(document)
            if len(kept) >= documents_per_collection:
                break
        logger.info("Albert collection %s: %d documents kept", name, len(kept))
        corpora.append(kept)

    # Interleaved: one from each corpus in turn, so neither owns a folder,
    # an author or a format.
    ordered = [
        document for row in zip_longest(*corpora) for document in row if document is not None
    ]

    colleagues = [get_or_create_user(email, name) for email, name in COLLEAGUES]
    laid = places(user, colleagues, len(ordered))
    items = []
    for index, document in enumerate(ordered):
        with transaction.atomic():
            item = seed_document(index, document, client, laid[index], report)
        if item is not None:
            items.append(item)

    index_seeded(items, report)
    return report
