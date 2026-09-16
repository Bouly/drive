"""Tests for the subjects a user creates: files fall in, pinned files define them."""

from contextlib import contextmanager
from unittest import mock

import pytest
from rest_framework.test import APIClient

from core import factories, models

from graph.models import ItemTopic, Topic
from graph.services import storage
from graph.services.chunking import Chunk, hash_text
from graph.services.subjects import sort_files_into, topic_vector

pytestmark = pytest.mark.django_db

TOPICS = "/api/v1.0/graph/topics/"


def mix(weights, dim=1024):
    """A unit vector from {axis: weight}."""
    vector = [0.0] * dim
    for axis, weight in weights.items():
        vector[axis] = weight
    norm = sum(x * x for x in vector) ** 0.5
    return [x / norm for x in vector]


def indexed_file(title, vector, user=None):
    """A ready file with one stored passage."""
    item = factories.ItemFactory(
        title=title,
        type=models.ItemTypeChoices.FILE,
        update_upload_state=models.ItemUploadStateChoices.READY,
        users=[user] if user else [],
    )
    storage.save_chunks(item, [Chunk(0, f"Texte de {title}", hash_text(title), vector)])
    return item


@contextmanager
def albert(vector, rerank=None):
    """Albert answering with one vector for the words, and reranker scores."""
    with mock.patch("graph.services.subjects.AlbertClient") as client:
        client.return_value.embed.return_value = [vector]
        client.return_value.rerank.side_effect = lambda query, documents: [
            (rerank or {}).get(text.split("\n")[0], 0.0) for text in documents
        ]
        yield client


def test_the_files_that_read_like_the_subject_fall_into_it():
    """A subject gathers the files the reader says are about it."""
    user = factories.UserFactory()
    close = indexed_file("Marché hébergement", mix({0: 1.0, 1: 0.2}), user)
    far = indexed_file("Recette de cuisine", mix({50: 1.0}), user)
    topic = Topic.objects.create(name="Marchés publics", creator=user)

    scores = {"Marché hébergement": 0.62, "Recette de cuisine": 0.01}
    with albert(mix({0: 1.0}), rerank=scores):
        sort_files_into(topic)

    assert set(topic.memberships.values_list("item_id", flat=True)) == {close.id}
    # The score is a share of the best answer, which compares across subjects.
    assert ItemTopic.objects.get(item=close).score == pytest.approx(1.0)
    assert not ItemTopic.objects.filter(item=far).exists()
    topic.refresh_from_db()
    assert topic.vector is not None
    # The question and the bar are kept, so the next uploaded file is judged
    # on the same scale.
    assert topic.question == "Marchés publics"
    assert topic.cut == pytest.approx(0.62 * 0.25)


def test_a_file_that_looks_close_but_reads_wrong_stays_out():
    """Vectors only draw up the shortlist; the reading decides.

    A deer photo and a bee photo are both "faune, nature, animal" to a
    vector, which is how two deer ended up in a subject about bees on a real
    drive. Read against the words of the subject, the deer is plainly out.
    """
    user = factories.UserFactory()
    bee = indexed_file("essaim-abeilles.jpg", mix({0: 1.0, 1: 0.3}), user)
    deer = indexed_file("cerf-prairie.jpg", mix({0: 1.0, 1: 0.25}), user)
    indexed_file("Facture EDF", mix({40: 1.0}), user)
    topic = Topic.objects.create(name="Abeilles", description="ruches, miel", creator=user)

    scores = {"essaim-abeilles.jpg": 0.62, "cerf-prairie.jpg": 0.006, "Facture EDF": 0.001}
    with albert(mix({0: 1.0}), rerank=scores):
        sort_files_into(topic)

    assert set(topic.memberships.values_list("item_id", flat=True)) == {bee.id}
    assert not ItemTopic.objects.filter(item=deer).exists()


def test_the_words_weigh_as_much_as_all_the_pinned_files():
    """One file pinned by mistake tilts the subject, it does not take it over."""
    user = factories.UserFactory()
    topic = Topic.objects.create(name="Abeilles", creator=user)
    for title in ("cerf-1.jpg", "cerf-2.jpg"):
        ItemTopic.objects.create(
            item=indexed_file(title, mix({10: 1.0}), user), topic=topic, pinned=True, score=1.0
        )

    with albert(mix({0: 1.0})):
        vector = topic_vector(topic)

    # Half the words, half the pinned files, however many there are.
    assert vector[0] == pytest.approx(vector[10])


def test_a_pinned_file_defines_the_subject_and_stays_in_it():
    """What the user pins moves the subject and is never taken out."""
    user = factories.UserFactory()
    pinned = indexed_file("Note d'arbitrage", mix({10: 1.0}), user)
    like_pinned = indexed_file("Note de cadrage", mix({10: 1.0, 11: 0.3}), user)
    topic = Topic.objects.create(name="Budget", creator=user)
    ItemTopic.objects.create(item=pinned, topic=topic, pinned=True, score=1.0)

    # The words say nothing useful; the reading places the other note.
    scores = {"Note de cadrage": 0.5, "Note d'arbitrage": 0.4}
    with albert(mix({40: 1.0}), rerank=scores):
        sort_files_into(topic)

    assert set(topic.memberships.values_list("item_id", flat=True)) == {pinned.id, like_pinned.id}
    assert ItemTopic.objects.get(item=pinned).pinned is True


def test_a_subject_lets_go_of_a_file_that_no_longer_fits():
    """Rewriting the subject's words takes the files that drifted out."""
    user = factories.UserFactory()
    item = indexed_file("Marché hébergement", mix({0: 1.0}), user)
    topic = Topic.objects.create(name="Marchés", creator=user)
    with albert(mix({0: 1.0}), rerank={"Marché hébergement": 0.5}):
        sort_files_into(topic)
    assert topic.memberships.count() == 1

    topic.name = "Cuisine"
    topic.save()
    with albert(mix({50: 1.0})):
        sort_files_into(topic)

    assert not topic.memberships.exists()
    assert models.Item.objects.filter(id=item.id).exists()


def test_the_api_creates_a_subject_and_sorts_the_drive_into_it():
    """Creating a subject answers with how many files it gathered."""
    user = factories.UserFactory()
    indexed_file("Cahier des charges", mix({0: 1.0}), user)
    indexed_file("Photo de vacances", mix({50: 1.0}), user)
    client = APIClient()
    client.force_login(user)

    scores = {"Cahier des charges": 0.6, "Photo de vacances": 0.01}
    with albert(mix({0: 1.0}), rerank=scores):
        response = client.post(TOPICS, {"name": "Marchés publics"}, format="json")

    assert response.status_code == 201
    assert response.json()["files"] == 1
    assert Topic.objects.get().creator == user


def test_the_graph_shows_the_subjects_of_its_owner_only():
    """A subject belongs to its creator; the graph of someone else ignores it."""
    user = factories.UserFactory()
    stranger = factories.UserFactory()
    item = indexed_file("Cahier des charges", mix({0: 1.0}), user)
    topic = Topic.objects.create(name="Marchés", creator=stranger)
    ItemTopic.objects.create(item=item, topic=topic, pinned=True, score=1.0)

    client = APIClient()
    client.force_login(user)
    data = client.get("/api/v1.0/graph/").json()

    assert data["topics"] == []
    assert data["files"][0]["topics"] == []


def test_pinning_a_file_through_the_api_keeps_it_in_the_subject():
    """Pinning places the file and sharpens the subject in one call."""
    user = factories.UserFactory()
    item = indexed_file("Compte rendu", mix({20: 1.0}), user)
    topic = Topic.objects.create(name="Réunions", creator=user)
    client = APIClient()
    client.force_login(user)

    with albert(mix({0: 1.0})):
        response = client.post(f"{TOPICS}{topic.id}/files/", {"item": str(item.id)}, format="json")

    assert response.status_code == 200
    membership = ItemTopic.objects.get(item=item, topic=topic)
    assert membership.pinned is True

    with albert(mix({0: 1.0})):
        client.delete(f"{TOPICS}{topic.id}/files/{item.id}/")
    assert not ItemTopic.objects.filter(item=item, topic=topic).exists()


def test_a_file_the_reranker_judges_relevant_joins_the_subject():
    """A subject named in one word catches files cosine would leave out."""
    user = factories.UserFactory()
    cv = indexed_file("CV_Ahmed.pdf", mix({30: 1.0}), user)
    other = indexed_file("Facture EDF", mix({31: 1.0}), user)
    topic = Topic.objects.create(name="cv", creator=user)

    # The words of the subject sit far from both files; the reranker reads
    # them and puts the CV first by a wide margin.
    third = indexed_file("Quittance de loyer", mix({32: 1.0}), user)
    scores = {"CV_Ahmed.pdf": 0.53, "Facture EDF": 0.02, "Quittance de loyer": 0.01}
    with albert(mix({0: 1.0}), rerank=scores):
        sort_files_into(topic)

    assert set(topic.memberships.values_list("item_id", flat=True)) == {cv.id}
    assert not ItemTopic.objects.filter(item__in=[other, third]).exists()


def test_a_file_is_read_past_its_first_passage():
    """What a file is about may start on its second passage, or its third."""
    user = factories.UserFactory()
    item = indexed_file("video.mp4", mix({0: 1.0}), user)
    storage.save_chunks(
        item,
        [
            Chunk(0, "video", hash_text("video"), mix({0: 1.0})),
            Chunk(1, "Comment les abeilles font le miel", hash_text("miel"), mix({0: 1.0})),
        ],
    )
    for title in ("Facture EDF", "Quittance"):
        indexed_file(title, mix({0: 1.0}), user)
    topic = Topic.objects.create(name="Abeilles", creator=user)

    # The reranker only answers to the passage about honey.
    def scores(query, documents):  # pylint: disable=unused-argument
        return [0.77 if "abeilles" in text else 0.001 for text in documents]

    with mock.patch("graph.services.subjects.AlbertClient") as client:
        client.return_value.embed.return_value = [mix({0: 1.0})]
        client.return_value.rerank.side_effect = scores
        sort_files_into(topic)

    assert set(topic.memberships.values_list("item_id", flat=True)) == {item.id}


def test_a_subject_described_in_keywords_still_finds_its_files():
    """Each line of a description is its own question, the best answer counts.

    Measured on a real drive: a subject "abeille" described as "frelon /
    ruche / guêpe / nid / apiculture" scored its bee documentary 0.013 when
    the whole block was the query, and 0.77 when asked "abeille" alone.
    """
    user = factories.UserFactory()
    video = indexed_file("video.mp4", mix({0: 1.0}), user)
    for title in ("Facture EDF", "Quittance"):
        indexed_file(title, mix({0: 1.0}), user)
    topic = Topic.objects.create(name="abeille", description="frelon\nruche\nguêpe", creator=user)

    def answer(query, documents):
        if query != "abeille":
            # Nothing here is about hornets: whatever comes closest to the
            # word must not be crowned for it.
            return [0.02 if text.startswith("Facture") else 0.001 for text in documents]
        return [0.77 if text.startswith("video.mp4") else 0.001 for text in documents]

    with mock.patch("graph.services.subjects.AlbertClient") as client:
        client.return_value.embed.return_value = [mix({0: 1.0})]
        client.return_value.rerank.side_effect = answer
        sort_files_into(topic)

    assert set(topic.memberships.values_list("item_id", flat=True)) == {video.id}
    topic.refresh_from_db()
    assert topic.question == "abeille"


def test_a_pinned_file_cannot_silence_the_subject_s_own_words():
    """What the subject says of itself always reaches the reader.

    On a real drive, one header pinned to a subject called "abeille" pulled
    it among the network headers: the only video about bees was no longer
    close enough to be read at all, and the subject filled up with strangers.
    """
    user = factories.UserFactory()
    bees = indexed_file("video.mp4", mix({0: 1.0}), user)
    pinned = indexed_file("errno.txt", mix({60: 1.0}), user)
    topic = Topic.objects.create(name="abeille", creator=user)
    ItemTopic.objects.create(item=pinned, topic=topic, pinned=True, score=1.0)
    # Enough headers sitting right between the words and the pinned file to
    # fill a shortlist on their own, closer to the blend than the video is.
    for i in range(70):
        indexed_file(f"if_{i}.txt", mix({0: 1.0, 60: 1.0, 61: i / 1000}), user)

    scores = {"video.mp4": 0.77}
    with albert(mix({0: 1.0}), rerank=scores):
        sort_files_into(topic)

    assert bees.id in set(topic.memberships.values_list("item_id", flat=True))


def test_a_subject_the_reranker_only_guesses_at_stays_empty():
    """No gap between the best answer and the middle of the batch: nobody in."""
    user = factories.UserFactory()
    for title, axis in (("Facture EDF", 31), ("Relevé bancaire", 32), ("Quittance", 33)):
        indexed_file(title, mix({axis: 1.0}), user)
    topic = Topic.objects.create(name="photo", creator=user)

    guessing = {"Facture EDF": 0.19, "Relevé bancaire": 0.12, "Quittance": 0.11}
    with albert(mix({0: 1.0}), rerank=guessing):
        sort_files_into(topic)

    assert not topic.memberships.exists()


def test_a_subject_never_sorts_someone_elses_files():
    """
    A subject sorts the drive of its owner, not the instance.

    The colleague's file is a perfect match by content, and it still stays
    out: it is not in my drive. The reader is never even shown it, so no
    passage of another drive travels to Albert for my subject.
    """
    me = factories.UserFactory()
    colleague = factories.UserFactory()
    mine = indexed_file("Marché hébergement", mix({0: 1.0, 1: 0.2}), me)
    theirs = indexed_file("Marché hébergement (leur copie)", mix({0: 1.0}), colleague)
    topic = Topic.objects.create(name="Marchés publics", creator=me)

    scores = {
        "Marché hébergement": 0.62,
        "Marché hébergement (leur copie)": 0.99,
    }
    with albert(mix({0: 1.0}), rerank=scores) as client:
        sort_files_into(topic)

    assert set(topic.memberships.values_list("item_id", flat=True)) == {mine.id}
    assert not ItemTopic.objects.filter(item=theirs).exists()
    read = "\n".join(
        text for call in client.return_value.rerank.call_args_list for text in call.args[1]
    )
    assert "leur copie" not in read
