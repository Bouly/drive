"""
Fill the graph storage with public documents from the Albert API.

Each Albert document becomes a real text file in a "test data" folder of a
user's Drive (so it opens in the explorer), its chunks are stored with
vectors from Albert's embeddings endpoint, topics come from the documents'
themes and links from nearest-neighbour search in our own storage.
"""

import logging
import re
from dataclasses import dataclass, field
from io import BytesIO

from django.core.files.storage import default_storage
from django.db import transaction

from core import models

from graph.models import ItemLink, ItemTopic, Topic
from graph.services import storage
from graph.services.chunking import Chunk, hash_text

logger = logging.getLogger(__name__)

FOLDER_TITLE = "Albert · données de test"
LINKS_PER_ITEM = 4
# Same-topic neighbours are linked from this similarity...
MIN_SIMILARITY = 0.62
# ...cross-topic ones only when clearly related: those are the "unexpected" links.
SURPRISE_MIN_SIMILARITY = 0.7
# Readable topic names for collections whose documents carry no theme.
COLLECTION_LABELS = {
    "mediatech-fiches-travail-emploi": "Travail - Emploi",
    "mediatech-fiches-service-public": "Service public",
    "mediatech-decisions-cnil": "CNIL",
    "mediatech-dossiers-legislatifs": "Dossiers législatifs",
    "mediatech-decisions-conseil-constitutionnel": "Conseil constitutionnel",
    "mediatech-legifrance": "Légifrance",
}


@dataclass
class SeedReport:
    """What a seeding run produced."""

    items: int = 0
    chunks: int = 0
    links: int = 0
    topics: list = field(default_factory=list)
    skipped: int = 0


def slugify(text, max_length=80):
    """A safe file name from a document title."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_length] or "document"


def topic_label(collection_name, metadata):
    """The theme of a document when Albert gives one, else the collection name."""
    theme = (metadata or {}).get("theme") or ""
    first = theme.split(",")[0].strip()
    return first or COLLECTION_LABELS.get(collection_name, collection_name)


def get_or_create_folder(user):
    """The folder holding the seeded files, owned by the user."""
    folder = models.Item.objects.filter(
        creator=user, title=FOLDER_TITLE, type=models.ItemTypeChoices.FOLDER, deleted_at=None
    ).first()
    if folder is None:
        folder = models.Item.objects.create_child(
            title=FOLDER_TITLE, type=models.ItemTypeChoices.FOLDER, creator=user
        )
        models.ItemAccess.objects.create(item=folder, user=user, role=models.RoleChoices.OWNER)
    return folder


def create_file(user, folder, title, text):
    """A ready text file in the folder, content stored in object storage."""
    filename = f"{slugify(title)}.txt"
    content = text.encode("utf-8")
    item = models.Item.objects.create_child(
        parent=folder,
        title=title[:255],
        type=models.ItemTypeChoices.FILE,
        filename=filename,
        mimetype="text/plain",
        size=len(content),
        creator=user,
        upload_state=models.ItemUploadStateChoices.READY,
    )
    models.ItemAccess.objects.create(item=item, user=user, role=models.RoleChoices.OWNER)
    default_storage.save(item.file_key, BytesIO(content))
    # New files start pending until an upload completes; ours is already there.
    item.upload_state = models.ItemUploadStateChoices.READY
    item.save(update_fields=["upload_state"])
    return item


def seed_documents(user, client, collection, documents, report):
    """Create one file per Albert document of ``collection`` with its chunks."""
    folder = get_or_create_folder(user)
    existing = set(
        models.Item.objects.filter(creator=user, deleted_at=None).values_list("title", flat=True)
    )
    for document in documents:
        title = document["name"][:255]
        if title in existing:
            report.skipped += 1
            continue
        chunks = client.chunks(document["id"])
        texts = [c["content"] for c in chunks if c.get("content", "").strip()]
        if not texts:
            report.skipped += 1
            continue
        vectors = client.embed(texts)
        metadata = chunks[0].get("metadata") or {}

        with transaction.atomic():
            item = create_file(user, folder, title, "\n\n".join(texts))
            report.chunks += storage.save_chunks(
                item,
                [
                    Chunk(index=i, text=text, text_hash=hash_text(text), embedding=vector)
                    for i, (text, vector) in enumerate(zip(texts, vectors, strict=True))
                ],
            )
            label = topic_label(collection["name"], metadata)
            topic, _ = Topic.objects.get_or_create(
                label=label, defaults={"keywords": [collection["name"]]}
            )
            ItemTopic.objects.update_or_create(item=item, defaults={"topic": topic})
            if label not in report.topics:
                report.topics.append(label)
        existing.add(title)
        report.items += 1
        logger.info("Seeded %s (%d chunks)", title, len(texts))


def link_seeded_items(user, report):
    """Semantic links between the user's seeded files, from our own storage."""
    folder = get_or_create_folder(user)
    items = list(
        models.Item.objects.children(folder.path).filter(
            type=models.ItemTypeChoices.FILE, deleted_at=None
        )
    )
    # Keys as strings: Neighbour.item_id is a string, Item.id a UUID.
    topic_of = {
        str(membership.item_id): membership.topic_id
        for membership in ItemTopic.objects.filter(item__in=items)
    }
    for item in items:
        vector = storage.item_vector(item)
        if vector is None:
            continue
        neighbours = storage.nearest_items(
            vector, items, k=LINKS_PER_ITEM, min_similarity=MIN_SIMILARITY, exclude_item=item
        )
        links = []
        for neighbour in neighbours:
            surprising = topic_of.get(str(item.id)) != topic_of.get(neighbour.item_id)
            if surprising and neighbour.similarity < SURPRISE_MIN_SIMILARITY:
                continue
            evidence = storage.nearest_chunks(
                vector, models.Item.objects.filter(id=neighbour.item_id), k=1
            )
            links.append(
                {
                    "target": neighbour.item_id,
                    "weight": round(neighbour.similarity, 3),
                    "kind": ItemLink.Kind.SEMANTIC,
                    "surprising": surprising,
                    "reason": (
                        f"Contenus proches ({round(neighbour.similarity * 100)} % de similarité)"
                    ),
                    "evidence": evidence[0].text[:300] if evidence else "",
                }
            )
        report.links += storage.replace_links(item, links)


def seed_from_albert(user, client, collection_ids, documents_per_collection=30):
    """Seed the user's graph from the given Albert public collections."""
    report = SeedReport()
    collections = {c["id"]: c for c in client.collections()}
    for collection_id in collection_ids:
        collection = collections.get(collection_id) or {
            "id": collection_id,
            "name": str(collection_id),
        }
        documents = client.documents(collection_id, limit=documents_per_collection)
        seed_documents(user, client, collection, documents, report)
    link_seeded_items(user, report)
    return report
