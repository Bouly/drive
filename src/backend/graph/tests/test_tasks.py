"""Tests for the indexing task: extraction, embedding (mocked) and linking through storage."""

from io import BytesIO
from unittest import mock

from django.core.files.storage import default_storage

import pytest

from core import factories, models

from graph.models import ItemChunk, ItemLink
from graph.services import storage
from graph.services.chunking import Chunk, hash_text
from graph.tasks import index_item

pytestmark = pytest.mark.django_db


def unit(axis, dim=1024):
    """A unit vector along one axis."""
    vector = [0.0] * dim
    vector[axis] = 1.0
    return vector


def make_text_file(title, text):
    """A ready text file whose content is in object storage."""
    content = text.encode("utf-8")
    item = factories.ItemFactory(
        title=title,
        type=models.ItemTypeChoices.FILE,
        filename=f"{title}.txt",
        mimetype="text/plain",
        size=len(content),
        update_upload_state=models.ItemUploadStateChoices.READY,
    )
    default_storage.save(item.file_key, BytesIO(content))
    return item


def test_index_item_stores_chunks_and_links(settings):
    """A text file is chunked, embedded, stored and linked to its closest neighbour."""
    settings.GRAPH_CHUNK_WORDS = 350
    neighbour = make_text_file("voisin", "Le préavis de démission est fixé par la convention.")
    storage.save_chunks(neighbour, [Chunk(0, "Le préavis…", hash_text("Le préavis…"), unit(0))])
    stranger = make_text_file("autre", "Les cotisations syndicales ouvrent un crédit d'impôt.")
    storage.save_chunks(stranger, [Chunk(0, "Les cotisations…", hash_text("x"), unit(1))])
    item = make_text_file(
        "nouveau", "La démission met fin au CDI.\n\nLe préavis dépend de la convention."
    )

    with mock.patch("graph.tasks.AlbertClient") as client:
        client.return_value.embed.side_effect = lambda texts: [unit(0) for _ in texts]
        index_item.apply(args=[item.id], throw=True)

    assert ItemChunk.objects.filter(item=item).count() == 1
    links = list(ItemLink.objects.filter(source=item))
    assert [link.target_id for link in links] == [neighbour.id]
    assert links[0].kind == ItemLink.Kind.SEMANTIC
    assert links[0].weight == 1.0
    assert links[0].surprising is False
    assert "Le préavis" in links[0].evidence
    assert "100 %" in links[0].reason


def test_index_item_ignores_trashed_candidates(settings):
    """A file in the trash never becomes the target of a new link."""
    settings.GRAPH_CHUNK_WORDS = 350
    trashed = make_text_file("poubelle", "texte")
    storage.save_chunks(trashed, [Chunk(0, "texte", hash_text("texte"), unit(0))])
    trashed.soft_delete()
    item = make_text_file("nouveau", "Un texte tout neuf.")

    with mock.patch("graph.tasks.AlbertClient") as client:
        client.return_value.embed.side_effect = lambda texts: [unit(0) for _ in texts]
        index_item.apply(args=[item.id], throw=True)

    assert ItemChunk.objects.filter(item=item).count() == 1
    assert not ItemLink.objects.filter(source=item).exists()


def test_index_item_skips_non_extractable_items():
    """A folder is left alone: no chunk, no call to Albert."""
    folder = factories.ItemFactory(type=models.ItemTypeChoices.FOLDER)
    with mock.patch("graph.tasks.AlbertClient") as client:
        index_item.apply(args=[folder.id], throw=True)
    client.assert_not_called()
    assert not ItemChunk.objects.filter(item=folder).exists()
