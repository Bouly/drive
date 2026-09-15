"""Tests for the indexing task: extraction, embedding (mocked) and linking through storage."""

from io import BytesIO, StringIO
from unittest import mock

from django.core.files.storage import default_storage
from django.core.management import call_command

import pytest

from core import factories, models

from graph.models import ItemChunk, ItemIndex, ItemLink, ItemTopic, Topic
from graph.services import storage
from graph.services.albert import AlbertError
from graph.services.chunking import Chunk, hash_text
from graph.tasks import index_item, readable_title

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


def test_index_item_links_to_other_topics_are_not_surprising_without_topic(settings):
    """An upload has no topic: its links to a file with a topic are plain links."""
    settings.GRAPH_CHUNK_WORDS = 350
    neighbour = make_text_file("fiche", "Licenciement économique.")
    storage.save_chunks(neighbour, [Chunk(0, "Licenciement", hash_text("l"), unit(0))])
    topic = Topic.objects.create(label="Travail - Emploi")
    ItemTopic.objects.create(item=neighbour, topic=topic)
    item = make_text_file("upload", "Réorganisation et suppression de postes.")

    with mock.patch("graph.tasks.AlbertClient") as client:
        client.return_value.embed.side_effect = lambda texts: [mix(0, 1, 0.8) for _ in texts]
        index_item.apply(args=[item.id], throw=True)

    link = ItemLink.objects.get(source=item)
    assert link.target_id == neighbour.id
    assert link.surprising is False


def test_index_item_keeps_the_closest_neighbour_below_the_threshold(settings):
    """A file whose best neighbour is only fairly close still gets that one link."""
    settings.GRAPH_CHUNK_WORDS = 350
    fair = make_text_file("assez proche", "a")
    storage.save_chunks(fair, [Chunk(0, "a", hash_text("a"), unit(0))])
    weak = make_text_file("un peu proche", "b")
    storage.save_chunks(weak, [Chunk(0, "b", hash_text("b"), mix(0, 2, 0.4))])
    item = make_text_file("nouveau", "c")

    with mock.patch("graph.tasks.AlbertClient") as client:
        client.return_value.embed.side_effect = lambda texts: [mix(0, 1, 0.55) for _ in texts]
        index_item.apply(args=[item.id], throw=True)

    targets = list(ItemLink.objects.filter(source=item).values_list("target_id", flat=True))
    assert targets == [fair.id]


def test_index_item_relinks_files_indexed_before(settings):
    """An older file points to a closer newcomer once the newcomer is indexed."""
    settings.GRAPH_CHUNK_WORDS = 350
    old = make_text_file("ancien", "a")
    storage.save_chunks(old, [Chunk(0, "a", hash_text("a"), unit(0))])
    far = make_text_file("lointain", "b")
    storage.save_chunks(far, [Chunk(0, "b", hash_text("b"), mix(0, 1, 0.52))])
    storage.replace_links(old, [{"target": far, "weight": 0.52, "kind": ItemLink.Kind.SEMANTIC}])
    item = make_text_file("nouveau", "c")

    with mock.patch("graph.tasks.AlbertClient") as client:
        client.return_value.embed.side_effect = lambda texts: [mix(0, 2, 0.9) for _ in texts]
        index_item.apply(args=[item.id], throw=True)

    old_targets = list(
        ItemLink.objects.filter(source=old).order_by("-weight").values_list("target_id", flat=True)
    )
    assert old_targets[0] == item.id


def test_graph_relink_command_rewrites_links():
    """The command links stored files together without any embedding call."""
    first = make_text_file("premier", "a")
    storage.save_chunks(first, [Chunk(0, "a", hash_text("a"), unit(0))])
    second = make_text_file("second", "b")
    storage.save_chunks(second, [Chunk(0, "b", hash_text("b"), mix(0, 1, 0.8))])

    call_command("graph_relink", stdout=StringIO())

    assert set(ItemLink.objects.values_list("source_id", "target_id")) == {
        (first.id, second.id),
        (second.id, first.id),
    }


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


def test_index_item_falls_back_to_the_title_of_a_file_without_text(settings):
    """A photo whose OCR finds nothing is placed by its name, not left pending."""
    settings.GRAPH_CHUNK_WORDS = 350
    item = make_text_file("Chat roux sur un canapé", "   ")

    with mock.patch("graph.tasks.AlbertClient") as client:
        client.return_value.embed.side_effect = lambda texts: [unit(0) for _ in texts]
        index_item.apply(args=[item.id], throw=True)

    chunk = ItemChunk.objects.get(item=item)
    assert chunk.text == "Chat roux sur un canapé"
    index = ItemIndex.objects.get(item=item)
    assert index.state == ItemIndex.State.DONE
    assert index.detail == "title only"


def no_text():
    """Tika is not running in tests: pretend it found nothing in the image."""
    return mock.patch("graph.tasks.extract_text", return_value="")


def make_image(title, mimetype="image/jpeg"):
    """A ready image file with bytes in object storage and no readable text."""
    item = factories.ItemFactory(
        title=title,
        type=models.ItemTypeChoices.FILE,
        filename=title,
        mimetype=mimetype,
        size=32,
        update_upload_state=models.ItemUploadStateChoices.READY,
    )
    default_storage.save(item.file_key, BytesIO(b"\xff\xd8\xff" + b"0" * 29))
    return item


def test_index_item_describes_a_picture_with_albert(settings):
    """An image without text is placed by what Albert sees on it."""
    settings.GRAPH_CHUNK_WORDS = 350
    item = make_image("IMG_4032.jpg")

    with mock.patch("graph.tasks.AlbertClient") as client, no_text():
        client.return_value.describe_image.return_value = "Un chat roux dort sur un canapé."
        client.return_value.embed.side_effect = lambda texts: [unit(0) for _ in texts]
        index_item.apply(args=[item.id], throw=True)

    raw, mimetype = client.return_value.describe_image.call_args.args
    assert mimetype == "image/jpeg"
    assert raw.startswith(b"\xff\xd8\xff")
    assert "chat roux" in ItemChunk.objects.get(item=item).text
    assert ItemIndex.objects.get(item=item).detail == "described by Albert"


def test_index_item_falls_back_to_the_title_when_albert_cannot_describe(settings):
    """Albert down or refusing: the image is still placed by its name."""
    settings.GRAPH_CHUNK_WORDS = 350
    item = make_image("Chat roux.jpg")

    with mock.patch("graph.tasks.AlbertClient") as client, no_text():
        client.return_value.describe_image.side_effect = AlbertError("down")
        client.return_value.embed.side_effect = lambda texts: [unit(0) for _ in texts]
        index_item.apply(args=[item.id], throw=True)

    assert ItemChunk.objects.get(item=item).text == "Chat roux"
    assert ItemIndex.objects.get(item=item).detail == "title only"


def test_index_item_does_not_describe_a_huge_picture(settings):
    """A picture too heavy to send is not described, only named."""
    settings.GRAPH_CHUNK_WORDS = 350
    settings.GRAPH_VISION_MAX_FILE_SIZE = 10
    item = make_image("Panorama.jpg")

    with mock.patch("graph.tasks.AlbertClient") as client, no_text():
        client.return_value.embed.side_effect = lambda texts: [unit(0) for _ in texts]
        index_item.apply(args=[item.id], throw=True)

    client.return_value.describe_image.assert_not_called()
    assert ItemIndex.objects.get(item=item).detail == "title only"


def test_readable_title_drops_extension_and_dashes():
    """A file name becomes words before it is analysed."""
    assert readable_title("Poop-emoji-scaled.jpg") == "Poop emoji scaled"
    assert readable_title("Budget_2027.xlsx") == "Budget 2027"
    assert readable_title("Note de cadrage") == "Note de cadrage"


def test_index_item_indexes_the_title_with_the_text(settings):
    """The name of a file counts as part of its content."""
    settings.GRAPH_CHUNK_WORDS = 350
    item = make_text_file("Budget 2027", "Les crédits de fonctionnement augmentent.")

    with mock.patch("graph.tasks.AlbertClient") as client:
        client.return_value.embed.side_effect = lambda texts: [unit(0) for _ in texts]
        index_item.apply(args=[item.id], throw=True)

    assert ItemChunk.objects.get(item=item).text.startswith("Budget 2027")


def test_index_item_remembers_what_it_skipped():
    """A video is recorded as skipped, so the page stops waiting for it."""
    video = factories.ItemFactory(
        title="film.mp4",
        type=models.ItemTypeChoices.FILE,
        update_upload_state=models.ItemUploadStateChoices.READY,
        mimetype="video/mp4",
        size=10,
    )
    index_item.apply(args=[video.id], throw=True)
    assert ItemIndex.objects.get(item=video).state == ItemIndex.State.SKIPPED


def test_index_item_skips_non_extractable_items():
    """A folder is left alone: no chunk, no call to Albert."""
    folder = factories.ItemFactory(type=models.ItemTypeChoices.FOLDER)
    with mock.patch("graph.tasks.AlbertClient") as client:
        index_item.apply(args=[folder.id], throw=True)
    client.assert_not_called()
    assert not ItemChunk.objects.filter(item=folder).exists()
