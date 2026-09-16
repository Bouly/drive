"""
Tests for the links following what happens to the files.

A link is stored per file, so a file that arrives, changes, goes to the trash
or comes back must rewrite the links of the files around it, not only its own.
"""

from io import BytesIO
from unittest import mock

from django.core.files.storage import default_storage

import pytest

from core import factories, models

from graph.models import ItemLink
from graph.services import storage
from graph.services.chunking import Chunk, hash_text
from graph.services.linking import relink_all
from graph.tasks import index_item

pytestmark = pytest.mark.django_db


def unit(axis, dim=1024):
    """A unit vector along one axis."""
    vector = [0.0] * dim
    vector[axis] = 1.0
    return vector


def mix(a, b, weight, dim=1024):
    """A unit vector between two axes: its similarity to axis ``a`` is ``weight``."""
    vector = [0.0] * dim
    vector[a] = weight
    vector[b] = (1 - weight**2) ** 0.5
    return vector


def make_file(title, text, vector=None, parent=None):
    """A ready text file, already indexed when a vector is given."""
    content = text.encode("utf-8")
    item = factories.ItemFactory(
        title=title,
        type=models.ItemTypeChoices.FILE,
        filename=f"{title}.txt",
        mimetype="text/plain",
        size=len(content),
        update_upload_state=models.ItemUploadStateChoices.READY,
        **({"parent": parent} if parent else {}),
    )
    default_storage.save(item.file_key, BytesIO(content))
    if vector is not None:
        passage = f"{title} {text}"
        storage.save_chunks(item, [Chunk(0, passage, hash_text(passage), vector)])
    return item


def links_of(item):
    """Ids the item points to, strongest first."""
    return list(
        ItemLink.objects.filter(source=item).order_by("-weight").values_list("target_id", flat=True)
    )


def test_a_new_file_relinks_the_files_it_is_now_closest_to(
    settings, django_capture_on_commit_callbacks
):
    """The whole neighbourhood of a new subject is rewritten, not only the newcomer."""
    settings.GRAPH_CHUNK_WORDS = 350
    old = make_file("ancien", "sujet X", unit(0))
    far = make_file("lointain", "autre chose", mix(0, 1, 0.52))
    storage.replace_links(old, [{"target": far, "weight": 0.52, "kind": ItemLink.Kind.SEMANTIC}])
    newcomer = make_file("nouveau", "sujet X aussi")

    with (
        mock.patch("graph.tasks.AlbertClient") as client,
        django_capture_on_commit_callbacks(execute=True),
    ):
        client.return_value.embed.side_effect = lambda texts: [unit(0) for _ in texts]
        index_item.apply(args=[newcomer.id], throw=True)

    assert links_of(old)[0] == newcomer.id
    assert links_of(newcomer)[0] == old.id


def test_changing_a_file_drops_the_weight_of_the_links_that_pointed_at_it(
    settings, django_capture_on_commit_callbacks
):
    """A file that moves to another subject is still linked, but barely."""
    settings.GRAPH_CHUNK_WORDS = 350
    neighbour = make_file("voisin", "sujet X", unit(0))
    moving = make_file("qui change", "sujet X", unit(0))
    storage.replace_links(
        neighbour, [{"target": moving, "weight": 1.0, "kind": ItemLink.Kind.SEMANTIC}]
    )
    storage.replace_links(
        moving, [{"target": neighbour, "weight": 1.0, "kind": ItemLink.Kind.SEMANTIC}]
    )

    # Its content really changes: a same text would rightly keep its vector.
    default_storage.delete(moving.file_key)
    default_storage.save(moving.file_key, BytesIO("un tout autre sujet".encode()))

    # Re-indexed with a content far from the one it had.
    with (
        mock.patch("graph.tasks.AlbertClient") as client,
        django_capture_on_commit_callbacks(execute=True),
    ):
        client.return_value.embed.side_effect = lambda texts: [unit(5) for _ in texts]
        index_item.apply(args=[moving.id], throw=True)

    # Every pair stays linked: what changes is the weight, from 1 to 0.
    assert ItemLink.objects.get(source=neighbour, target=moving).weight == 0.0
    assert ItemLink.objects.get(source=moving, target=neighbour).weight == 0.0


def test_relinking_drops_the_links_of_a_file_that_left_the_graph():
    """A link kept from a file no longer indexed is swept away on the next pass."""
    kept = make_file("gardé", "sujet X", unit(0))
    gone = make_file("parti", "sujet X", unit(0))
    storage.replace_links(kept, [{"target": gone, "weight": 1.0, "kind": ItemLink.Kind.SEMANTIC}])
    # Its passages are gone (emptied, or trashed without passing by the signal).
    storage.delete_chunks(gone)

    relink_all()

    assert not ItemLink.objects.filter(target=gone).exists()
    assert links_of(kept) == []


def test_trashing_a_file_removes_its_links_both_ways(django_capture_on_commit_callbacks):
    """Nothing points at a trashed file any more."""
    kept = make_file("gardé", "sujet X", unit(0))
    trashed = make_file("jeté", "sujet X", unit(0))
    storage.replace_links(
        kept, [{"target": trashed, "weight": 1.0, "kind": ItemLink.Kind.SEMANTIC}]
    )
    storage.replace_links(
        trashed, [{"target": kept, "weight": 1.0, "kind": ItemLink.Kind.SEMANTIC}]
    )

    with django_capture_on_commit_callbacks(execute=True):
        trashed.soft_delete()

    assert not ItemLink.objects.filter(source=trashed).exists()
    assert not ItemLink.objects.filter(target=trashed).exists()


def test_trashing_a_file_gives_its_neighbour_a_replacement(django_capture_on_commit_callbacks):
    """The file left behind takes its next closest file instead of losing a link."""
    orphan = make_file("orphelin", "sujet X", unit(0))
    trashed = make_file("jeté", "sujet X", unit(0))
    second = make_file("second choix", "sujet X moins proche", mix(0, 1, 0.8))
    storage.replace_links(
        orphan, [{"target": trashed, "weight": 1.0, "kind": ItemLink.Kind.SEMANTIC}]
    )

    with django_capture_on_commit_callbacks(execute=True):
        trashed.soft_delete()

    assert links_of(orphan) == [second.id]


def test_trashing_a_folder_forgets_the_files_inside(django_capture_on_commit_callbacks):
    """Files of a trashed folder leave the graph with it, signal or not."""
    folder = factories.ItemFactory(type=models.ItemTypeChoices.FOLDER)
    inside = make_file("dedans", "sujet X", unit(0), parent=folder)
    outside = make_file("dehors", "sujet X", unit(0))
    storage.replace_links(
        outside, [{"target": inside, "weight": 1.0, "kind": ItemLink.Kind.SEMANTIC}]
    )

    with django_capture_on_commit_callbacks(execute=True):
        folder.soft_delete()

    assert not ItemLink.objects.filter(target=inside).exists()
    assert links_of(outside) == []


def test_restoring_a_file_puts_it_back_in_the_graph(settings, django_capture_on_commit_callbacks):
    """A file out of the trash is indexed again and linked back."""
    settings.GRAPH_CHUNK_WORDS = 350
    kept = make_file("gardé", "sujet X", unit(0))
    restored = make_file("revenu", "sujet X")

    with django_capture_on_commit_callbacks(execute=True):
        restored.soft_delete()
    with (
        mock.patch("graph.tasks.AlbertClient") as client,
        django_capture_on_commit_callbacks(execute=True),
    ):
        client.return_value.embed.side_effect = lambda texts: [unit(0) for _ in texts]
        restored.restore()

    assert links_of(restored) == [kept.id]
    assert links_of(kept) == [restored.id]
