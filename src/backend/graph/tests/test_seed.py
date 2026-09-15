"""Tests for seeding the graph from Albert (client mocked, storage real)."""

from unittest import mock

import pytest
import responses

from core import factories, models

from graph.models import ItemChunk, ItemLink, ItemTopic, Topic
from graph.services.albert import AlbertClient, AlbertError
from graph.services.seed import FOLDER_TITLE, seed_from_albert, slugify, topic_label

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
    """Two collections, three documents; vectors chosen so links are predictable."""

    def __init__(self):
        self.docs = {
            1: (
                "Démission d'un salarié",
                "Travail",
                ["La démission met fin au CDI.", "Le préavis dépend de la convention."],
            ),
            2: (
                "Préavis de démission",
                "Travail",
                ["Le préavis de démission est fixé par la convention."],
            ),
            3: (
                "Crédit d'impôt syndical",
                "Argent - Impôts",
                ["Les cotisations syndicales ouvrent un crédit d'impôt."],
            ),
        }
        self.vectors = {
            "La démission met fin au CDI.": unit(0),
            "Le préavis dépend de la convention.": mix(0, 1, 0.9),
            "Le préavis de démission est fixé par la convention.": mix(0, 1, 0.8),
            "Les cotisations syndicales ouvrent un crédit d'impôt.": unit(2),
        }

    def collections(self):
        """Two public collections."""
        return [{"id": 10, "name": "fiches-travail"}, {"id": 20, "name": "fiches-impots"}]

    def documents(self, collection_id, limit=50, offset=0):  # pylint: disable=unused-argument
        """Documents of a collection."""
        ids = [1, 2] if collection_id == 10 else [3]
        return [{"id": i, "name": self.docs[i][0], "chunks": len(self.docs[i][2])} for i in ids][
            :limit
        ]

    def chunks(self, document_id):
        """Chunks of a document, all tagged with its theme."""
        _, theme, texts = self.docs[document_id]
        return [{"content": text, "metadata": {"theme": f"{theme}, {theme}"}} for text in texts]

    def embed(self, texts):
        """Fixed vectors per text."""
        return [self.vectors[text] for text in texts]


def test_seed_creates_files_chunks_topics_and_links():
    """Documents become readable files with vectors, themed topics and semantic links."""
    user = factories.UserFactory()
    with mock.patch("graph.services.seed.default_storage.save") as save:
        report = seed_from_albert(user, FakeAlbert(), [10, 20], documents_per_collection=5)

    assert report.items == 3
    assert report.chunks == 4
    assert report.skipped == 0
    assert sorted(report.topics) == ["Argent - Impôts", "Travail"]

    folder = models.Item.objects.get(title=FOLDER_TITLE)
    files = models.Item.objects.children(folder.path).filter(type="file").order_by("title")
    assert [f.title for f in files] == [
        "Crédit d'impôt syndical",
        "Démission d'un salarié",
        "Préavis de démission",
    ]
    assert all(f.mimetype == "text/plain" and f.upload_state == "ready" for f in files)
    assert save.call_count == 3
    # Every file is readable by its owner, so it shows in the graph API...
    assert models.Item.objects.readable_per_se(user).filter(id__in=files).count() == 3
    # ...and by nobody else: seeded files are restricted, not shared by link.
    assert all(f.link_reach == models.LinkReachChoices.RESTRICTED for f in files)
    stranger = factories.UserFactory()
    assert models.Item.objects.readable_per_se(stranger).filter(id__in=files).count() == 0

    assert ItemChunk.objects.count() == 4
    assert Topic.objects.count() == 2
    assert (
        ItemTopic.objects.get(item__title="Crédit d'impôt syndical").topic.label
        == "Argent - Impôts"
    )

    # The two "Travail" documents are close; the tax one is orthogonal to both.
    demission = models.Item.objects.get(title="Démission d'un salarié")
    preavis = models.Item.objects.get(title="Préavis de démission")
    links = {(l.source.title, l.target.title): l for l in ItemLink.objects.all()}
    assert set(links) == {
        ("Démission d'un salarié", "Préavis de démission"),
        ("Préavis de démission", "Démission d'un salarié"),
    }
    link = links[("Démission d'un salarié", "Préavis de démission")]
    assert link.kind == "semantic"
    assert link.surprising is False
    # Item vectors are chunk means: (0.99, 0.16) vs (0.80, 0.60) -> cosine ~0.89.
    assert 0.85 < link.weight < 0.92
    assert "similarité" in link.reason
    assert link.evidence == "Le préavis de démission est fixé par la convention."
    assert report.links == 2
    assert {demission.id, preavis.id} == {link.source_id, link.target_id}


def test_seed_is_idempotent():
    """Running the seed twice does not duplicate files."""
    user = factories.UserFactory()
    with mock.patch("graph.services.seed.default_storage.save"):
        seed_from_albert(user, FakeAlbert(), [10])
        report = seed_from_albert(user, FakeAlbert(), [10])
    assert report.items == 0
    assert report.skipped == 2
    assert models.Item.objects.filter(type="file").count() == 2


def test_helpers():
    """File names and topic labels are derived safely."""
    assert slugify("Occupation du domaine public (AOT) !") == "occupation-du-domaine-public-aot"
    assert topic_label("fiches", {"theme": "Argent - Impôts, Argent - Impôts"}) == "Argent - Impôts"
    assert topic_label("fiches", {}) == "fiches"
    assert topic_label("mediatech-fiches-travail-emploi", {}) == "Travail - Emploi"


@responses.activate
def test_albert_client_embeds_and_checks_vectors(settings):
    """The client sends the model, keeps the order and validates the vectors."""
    settings.GRAPH_ALBERT_URL = ALBERT
    settings.GRAPH_ALBERT_API_KEY = "sk-test"
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


def test_albert_client_needs_a_key(settings):
    """Without ALBERT_API_KEY the client refuses to start."""
    settings.GRAPH_ALBERT_API_KEY = None
    with pytest.raises(AlbertError, match="ALBERT_API_KEY"):
        AlbertClient()
