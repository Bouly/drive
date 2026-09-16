"""Tests for the automatic topics (step 6): communities, naming, stability, links."""

from io import StringIO
from unittest import mock

from django.core.management import call_command

import pytest

from core import factories, models

from graph.models import ItemLink, ItemTopic, Topic
from graph.services import storage
from graph.services.albert import AlbertError
from graph.services.chunking import Chunk, hash_text
from graph.services.topics import assign_topics, keywords, louvain

pytestmark = pytest.mark.django_db


def mix(weights, dim=1024):
    """A unit vector from {axis: weight}."""
    vector = [0.0] * dim
    for axis, weight in weights.items():
        vector[axis] = weight
    norm = sum(x * x for x in vector) ** 0.5
    return [x / norm for x in vector]


def indexed_file(title, vector):
    """A ready file with one stored chunk."""
    item = factories.ItemFactory(
        title=title,
        type=models.ItemTypeChoices.FILE,
        update_upload_state=models.ItemUploadStateChoices.READY,
    )
    storage.save_chunks(item, [Chunk(0, f"Texte de {title}", hash_text(title), vector)])
    return item


class FakeChat:
    """Names topics after the first title it is shown, and counts calls."""

    def __init__(self):
        self.prompts = []

    def chat(self, prompt):
        """Albert's answer: the first title, as a topic name."""
        self.prompts.append(prompt)
        return f"« Sujet {prompt.splitlines()[1][2:].split(' : ')[0]} »."


def two_groups():
    """Three recruitment files and three budget files, the groups far apart."""
    hr = [
        indexed_file("Fiche de poste", mix({0: 1.0, 1: 0.2})),
        indexed_file("Grille d'entretien", mix({0: 1.0, 2: 0.25})),
        indexed_file("Comité de recrutement", mix({0: 1.0, 3: 0.3})),
    ]
    budget = [
        indexed_file("Budget 2027", mix({10: 1.0, 11: 0.2})),
        indexed_file("Note licences", mix({10: 1.0, 12: 0.25})),
        indexed_file("Marché hébergement", mix({10: 1.0, 13: 0.3})),
    ]
    return hr, budget


def test_louvain_separates_two_cliques_joined_by_one_edge():
    """Two triangles joined by a weak edge are two communities."""
    edges = {(0, 1): 1, (1, 2): 1, (0, 2): 1, (3, 4): 1, (4, 5): 1, (3, 5): 1, (2, 3): 0.1}
    assert sorted(sorted(group) for group in louvain(6, edges)) == [[0, 1, 2], [3, 4, 5]]


def test_assign_topics_groups_and_names_files():
    """Each group gets one topic named by the chat model; files of a group share it."""
    hr, budget = two_groups()
    chat = FakeChat()

    report = assign_topics(client=chat)

    assert report.files == 6
    assert report.named == 2
    assert len(chat.prompts) == 2
    topics = {item.title: item.topic_membership.topic for item in hr + budget}
    assert len({topics[item.title].id for item in hr}) == 1
    assert len({topics[item.title].id for item in budget}) == 1
    assert topics["Fiche de poste"] != topics["Budget 2027"]
    assert all(topic.automatic for topic in topics.values())
    # Quotes and final dot are stripped from the model's answer.
    assert not topics["Fiche de poste"].label.startswith("«")
    assert not topics["Fiche de poste"].label.endswith(".")


def test_assign_topics_keeps_names_of_unchanged_groups():
    """A second run reuses the topics without asking the model again."""
    two_groups()
    assign_topics(client=FakeChat())
    labels = set(Topic.objects.values_list("label", flat=True))

    chat = FakeChat()
    report = assign_topics(client=chat)

    assert report.reused == 2
    assert not chat.prompts
    assert set(Topic.objects.values_list("label", flat=True)) == labels


def test_assign_topics_falls_back_to_title_keywords():
    """Without Albert, a topic is named after its most frequent title words."""
    indexed_file("Séminaire programme", mix({0: 1.0, 1: 0.2}))
    indexed_file("Séminaire logistique", mix({0: 1.0, 2: 0.2}))
    failing = mock.Mock()
    failing.chat.side_effect = AlbertError("down")

    assign_topics(client=failing)

    assert Topic.objects.get().label.startswith("Séminaire")


def test_a_weak_tie_does_not_found_a_topic():
    """A file whose only neighbour is far stays without a topic."""
    hr, _ = two_groups()
    # Similar enough to be linked (0.52), too far to belong to the group.
    stranger = indexed_file("Autre chose", mix({0: 0.52, 40: 1.0}))

    assign_topics(client=FakeChat())

    assert not ItemTopic.objects.filter(item=stranger).exists()
    assert ItemTopic.objects.filter(item=hr[0]).exists()


def test_two_files_that_choose_each_other_make_a_topic():
    """Two lonely files closest to each other are grouped, even below the floor."""
    two_groups()
    first = indexed_file("Vélo de route", mix({40: 1.0, 41: 0.62}))
    second = indexed_file("Entretien du vélo", mix({40: 1.0, 42: 0.62}))

    assign_topics(client=FakeChat())

    assert ItemTopic.objects.get(item=first).topic == ItemTopic.objects.get(item=second).topic


def test_assign_topics_leaves_given_topics_and_lonely_files_alone():
    """Albert-themed files keep their topic; a file related to nothing gets none."""
    hr, _ = two_groups()
    themed = Topic.objects.create(label="Travail - Emploi")
    ItemTopic.objects.create(item=hr[0], topic=themed)
    alone = indexed_file("Recette de cuisine", mix({50: 1.0}))

    assign_topics(client=FakeChat())

    hr[0].refresh_from_db()
    assert hr[0].topic_membership.topic == themed
    assert not ItemTopic.objects.filter(item=alone).exists()


def test_assign_topics_flags_strong_links_across_topics():
    """Once topics exist, a strong link between two of them is unexpected."""
    hr, budget = two_groups()
    # Equally close to both groups (cosine ~0.7 with each): it joins one of them.
    indexed_file("Budget des recrutements", mix({0: 1.0, 10: 1.0}))

    assign_topics(client=FakeChat())

    surprising = ItemLink.objects.filter(surprising=True)
    assert surprising.exists()
    for link in surprising:
        source = ItemTopic.objects.get(item_id=link.source_id).topic_id
        target = ItemTopic.objects.get(item_id=link.target_id).topic_id
        assert source != target
    assert not ItemLink.objects.filter(source__in=hr, target__in=hr, surprising=True).exists()
    assert not ItemLink.objects.filter(
        source__in=budget, target__in=budget, surprising=True
    ).exists()


def test_keywords_ignore_stopwords_and_extensions():
    """Title keywords skip small words and file extensions."""
    items = [
        mock.Mock(title="Programme du séminaire.docx"),
        mock.Mock(title="Séminaire - logistique"),
    ]
    assert keywords(items)[0] == "séminaire"
    assert "docx" not in keywords(items)


def test_graph_topics_command(settings):
    """The command groups files and reports the topics."""
    settings.GRAPH_ALBERT_API_KEY = None
    two_groups()
    out = StringIO()
    call_command("graph_topics", stdout=out)
    assert "6 files, 2 topics" in out.getvalue()
