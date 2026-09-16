"""Tests for the graph admin: vectors and neighbours shown on a passage."""

from django.contrib import admin
from django.urls import reverse

import pytest

from core import factories

from graph.admin import ItemChunkAdmin
from graph.models import ItemChunk, ItemLink
from graph.services import storage
from graph.tests.test_storage import make_chunks, make_file, mix, unit

pytestmark = pytest.mark.django_db


@pytest.fixture(name="chunk_admin")
def fixture_chunk_admin():
    """The admin class of the passages."""
    return ItemChunkAdmin(ItemChunk, admin.site)


def test_admin_chunk_embedding_preview(chunk_admin):
    """The vector shows its dimension, its norm and its values."""
    item = make_file("report")
    storage.save_chunks(item, make_chunks(unit(2)))
    chunk = ItemChunk.objects.get(item=item)

    html = chunk_admin.embedding_preview(chunk)

    assert "1024 dimensions, norm 1.0000" in html
    assert "[0.0000, 0.0000, 1.0000, 0.0000" in html
    assert "<details>" in html


def test_admin_chunk_nearest_passages(chunk_admin):
    """Closest passages of other files come first; the file's own passages are left out."""
    source = make_file("source")
    close = make_file("close")
    far = make_file("far")
    storage.save_chunks(source, make_chunks(unit(0), mix(0, 1, 0.99)))
    storage.save_chunks(close, make_chunks(mix(0, 1, 0.9)))
    storage.save_chunks(far, make_chunks(unit(1)))
    chunk = ItemChunk.objects.get(item=source, index=0)

    html = chunk_admin.nearest_passages(chunk)

    assert html.index("close") < html.index("far")
    assert "source" not in html
    assert "0.900" in html


def test_admin_chunk_nearest_passages_empty(chunk_admin):
    """A passage alone in the storage has no neighbour."""
    item = make_file("alone")
    storage.save_chunks(item, make_chunks(unit(0)))

    assert chunk_admin.nearest_passages(ItemChunk.objects.get(item=item)) == "-"


def test_admin_chunk_item_neighbours(chunk_admin):
    """The stored links of the passage's file are listed by weight."""
    source = make_file("source")
    storage.save_chunks(source, make_chunks(unit(0)))
    storage.replace_links(
        source,
        [
            {"target": make_file("weak"), "weight": 0.6, "kind": ItemLink.Kind.SEMANTIC},
            {"target": make_file("strong"), "weight": 0.9, "kind": ItemLink.Kind.SEMANTIC},
        ],
    )

    html = chunk_admin.item_neighbours(ItemChunk.objects.get(item=source))

    assert html.index("strong") < html.index("weak")
    assert "0.900" in html


def test_admin_chunk_change_page(client, settings):
    """A superuser can open a passage page with its vector and neighbours."""
    # The manifest storage needs collectstatic, which tests do not run.
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
    user = factories.UserFactory(is_staff=True, is_superuser=True)
    client.force_login(user)
    item = make_file("report")
    storage.save_chunks(item, make_chunks(unit(0)))
    chunk = ItemChunk.objects.get(item=item)

    response = client.get(reverse("admin:graph_itemchunk_change", args=[chunk.pk]))

    assert response.status_code == 200
    content = response.content.decode()
    assert "1024 dimensions" in content
    assert "Closest passages in other files" in content
    assert "Neighbours of the file (stored links)" in content
