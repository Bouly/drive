"""Tests for seeding the graph from Albert (client mocked, storage real)."""

import zipfile
from io import BytesIO
from unittest import mock

import pytest
import responses

from core import factories, models

from graph.models import ItemChunk, ItemLink
from graph.services import documents
from graph.services.albert import AlbertClient, AlbertError
from graph.services.scope import readable_by
from graph.services.seed import FOLDERS, readable_name, seed_from_albert, slugify

pytestmark = pytest.mark.django_db

ALBERT = "https://albert.test/v1"


def unit(axis, dim=1024):
    """A unit vector along one axis."""
    vector = [0.0] * dim
    vector[axis] = 1.0
    return vector


def mix(a, b, weight, dim=1024):
    """A unit vector between two axes."""
    vector = [0.0] * dim
    vector[a] = weight
    vector[b] = (1 - weight**2) ** 0.5
    return vector


class FakeAlbert:
    """Two collections of three documents each, as Albert hands them over."""

    def __init__(self):
        self.docs = {
            i: (
                f"fiche-{i}.pdf",
                [f"Passage {j} du document {i}, sur le droit du travail." for j in range(3)],
            )
            for i in range(1, 7)
        }

    def collections(self):
        """Two public collections."""
        return [
            {"id": 139226, "name": "mediatech-legifrance"},
            {"id": 150277, "name": "mediatech-fiches-travail-emploi"},
        ]

    def documents(self, collection_id, limit=50, offset=0):  # pylint: disable=unused-argument
        """Documents of a collection: the first three, then the last three."""
        ids = [1, 2, 3] if collection_id == 139226 else [4, 5, 6]
        return [{"id": i, "name": self.docs[i][0]} for i in ids][:limit]

    def chunks(self, document_id):
        """Chunks of a document."""
        return [{"content": text} for text in self.docs[document_id][1]]


def seed(user, per_collection=3, vector=None):
    """
    Seed with Albert mocked and the pipeline reading back what was written.

    The files are written for real ‒ an odt is a zip, a scan a PNG ‒ and the
    extraction step is handed the text those files carry, which is what Tika
    returns for the two office formats and what OCR reads off the scan.
    """
    text_by_file = {}
    real_render = documents.render

    def render(kind, title, paragraphs):
        extension, mimetype, content = real_render(kind, title, paragraphs)
        text_by_file[f"{slugify(title)}.{extension}"] = "\n\n".join(paragraphs)
        return extension, mimetype, content

    with (
        mock.patch("graph.services.seed.render", side_effect=render),
        mock.patch("graph.services.seed.default_storage.save", side_effect=lambda key, _: key),
        mock.patch(
            "graph.tasks.extract_text", side_effect=lambda item: text_by_file[item.filename]
        ),
        mock.patch("graph.tasks.AlbertClient") as client,
    ):
        client.return_value.embed.side_effect = lambda texts: [
            vector(text) if vector else unit(0) for text in texts
        ]
        return seed_from_albert(
            user, FakeAlbert(), [139226, 150277], documents_per_collection=per_collection
        )


def test_seed_writes_documents_sheets_and_scans():
    """The bank holds the three formats, as real office files."""
    user = factories.UserFactory()
    report = seed(user)

    assert report.items == 6
    assert report.skipped == 0
    assert report.formats == {"document": 4, "sheet": 1, "picture": 1}

    files = models.Item.objects.filter(type="file").order_by("created_at")
    assert [file.mimetype for file in files] == [
        documents.ODT_MIMETYPE,
        documents.ODT_MIMETYPE,
        documents.ODS_MIMETYPE,
        documents.ODT_MIMETYPE,
        documents.PNG_MIMETYPE,
        documents.ODT_MIMETYPE,
    ]
    # The name a reader sees carries the format, not the Albert file name.
    assert [file.title for file in files][:3] == ["Fiche 1.odt", "Fiche 4.odt", "Fiche 2.ods"]
    assert all(file.upload_state == "ready" and file.size > 0 for file in files)


def test_seed_spreads_dates_rights_and_authors():
    """A bank where everything is equal shows nothing: the three vary."""
    user = factories.UserFactory()
    seed(user)

    files = list(models.Item.objects.filter(type="file").order_by("created_at"))
    # Dates are spread rather than all set to today, so dots differ in size.
    days = {file.created_at.date() for file in files}
    assert len(days) == len(files)
    assert (files[-1].created_at - files[0].created_at).days > 300

    # The three folders exist, and the reader holds a different right on each.
    folders = models.Item.objects.filter(type="folder").order_by("title")
    assert [folder.title for folder in folders] == sorted(title for title, _ in FOLDERS)
    roles = {
        folder.title: models.Item.objects.filter(pk=folder.pk)
        .annotate_user_roles(user)
        .first()
        .get_role(user)
        for folder in folders
    }
    assert set(roles.values()) == {"owner", "editor", "reader"}
    # ...and every one of them is readable by the reader, whoever owns it.
    assert readable_by(user).filter(id__in=[file.id for file in files]).count() == len(files)

    # More than one author signs the bank, so filtering by author answers.
    assert len({file.creator_id for file in files}) > 1


def test_seed_indexes_and_links_every_file():
    """Seeded files go through the ordinary pipeline: passages, then links."""
    user = factories.UserFactory()
    # Two documents close to each other, the rest orthogonal to them.
    report = seed(user, vector=lambda text: unit(0) if "document 1" in text else unit(1))

    assert report.chunks == ItemChunk.objects.count() > 0
    assert report.failed == 0
    # Every indexed pair is linked, the weight telling a close pair apart.
    assert report.links == ItemLink.objects.count() > 0
    weights = sorted(link.weight for link in ItemLink.objects.all())
    assert weights[-1] == pytest.approx(1.0)
    assert weights[0] == pytest.approx(0.0)


def test_seed_skips_what_the_drive_already_holds():
    """Running the seed twice does not duplicate files."""
    user = factories.UserFactory()
    seed(user)
    report = seed(user)
    assert report.items == 0
    assert report.skipped == 6
    assert models.Item.objects.filter(type="file").count() == 6


def test_documents_are_real_office_files():
    """An OpenDocument is a zip whose content.xml holds the text, a scan a PNG."""
    passages = ["Le télétravail est volontaire.", "Le préavis est d'un mois."]

    extension, mimetype, raw = documents.render("document", "Fiche & télétravail", passages)
    assert (extension, mimetype) == ("odt", documents.ODT_MIMETYPE)
    with zipfile.ZipFile(BytesIO(raw)) as archive:
        assert archive.namelist()[0] == "mimetype"
        assert archive.read("mimetype").decode() == documents.ODT_MIMETYPE
        body = archive.read("content.xml").decode("utf-8")
    # The title is escaped, not dropped, and every passage is in there.
    assert "Fiche &amp; télétravail" in body
    assert all(passage in body for passage in passages)

    extension, mimetype, raw = documents.render("sheet", "Extraits", passages)
    assert (extension, mimetype) == ("ods", documents.ODS_MIMETYPE)
    with zipfile.ZipFile(BytesIO(raw)) as archive:
        body = archive.read("content.xml").decode("utf-8")
    assert body.count("<table:table-row>") == len(passages) + 2  # title, headers, rows
    assert "Extrait 2" in body

    extension, mimetype, raw = documents.render("picture", "Fiche pratique", passages)
    assert (extension, mimetype) == ("png", documents.PNG_MIMETYPE)
    assert raw[:4] == b"\x89PNG"
    assert len(raw) > 1000


def test_helpers():
    """File names are derived safely, and Albert's own names made readable."""
    assert slugify("Occupation du domaine public (AOT) !") == "occupation-du-domaine-public-aot"
    assert readable_name("fiche_teletravail-2024.pdf") == "Fiche teletravail 2024"
    assert readable_name("LEGITEXT000006072050.txt") == "LEGITEXT000006072050"
    assert readable_name("") == "Document"


@responses.activate
def test_albert_client_embeds_and_checks_vectors(settings):
    """The client sends the model, keeps the order and validates the vectors."""
    settings.GRAPH_ALBERT_URL = ALBERT
    settings.GRAPH_ALBERT_API_KEY = "sk-test"
    # Set here rather than read from the environment: a developer pointing
    # their own stack at another model must not fail this.
    settings.GRAPH_ALBERT_MODEL = "openweight-embeddings"
    settings.GRAPH_EMBEDDING_DIM = 2
    responses.post(
        f"{ALBERT}/embeddings",
        json={
            "data": [{"index": 1, "embedding": [0.0, 1.0]}, {"index": 0, "embedding": [1.0, 0.0]}]
        },
    )
    client = AlbertClient()
    assert client.embed(["a", "b"]) == [[1.0, 0.0], [0.0, 1.0]]
    assert responses.calls[0].request.headers["Authorization"] == "Bearer sk-test"
    assert '"model": "openweight-embeddings"' in responses.calls[0].request.body.decode()

    responses.post(f"{ALBERT}/embeddings", json={"data": [{"index": 0, "embedding": [3.0, 4.0]}]})
    with pytest.raises(AlbertError, match="unit length"):
        client.embed(["c"])

    responses.get(f"{ALBERT}/documents/7/chunks", json={"data": [{"content": "x", "metadata": {}}]})
    assert client.chunks(7) == [{"content": "x", "metadata": {}}]


@responses.activate
def test_albert_client_reads_every_page_of_a_collection(settings):
    """A collection is listed page by page: one request sees ten documents."""
    settings.GRAPH_ALBERT_URL = ALBERT
    settings.GRAPH_ALBERT_API_KEY = "sk-test"
    pages = [
        {"data": [{"id": i, "name": f"doc-{i}.pdf"} for i in range(10)]},
        {"data": [{"id": i, "name": f"doc-{i}.pdf"} for i in range(10, 14)]},
    ]
    responses.get(f"{ALBERT}/documents", json=pages[0])
    responses.get(f"{ALBERT}/documents", json=pages[1])

    documents = AlbertClient().documents(139226, limit=45)

    assert [document["id"] for document in documents] == list(range(14))
    # The second page is asked for from where the first one stopped, and a
    # short page ends it rather than being asked for again.
    assert len(responses.calls) == 2
    assert "offset=10" in responses.calls[1].request.url


def test_albert_client_needs_a_key(settings):
    """Without ALBERT_API_KEY the client refuses to start."""
    settings.GRAPH_ALBERT_API_KEY = None
    with pytest.raises(AlbertError, match="ALBERT_API_KEY"):
        AlbertClient()
