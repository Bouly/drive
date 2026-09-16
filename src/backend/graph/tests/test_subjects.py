"""Tests for the subjects a user creates: files fall in, pinned files define them."""

from contextlib import contextmanager
from unittest import mock

import pytest
from rest_framework.test import APIClient

from core import factories, models

from graph.models import ItemTopic, Topic
from graph.services import storage
from graph.services.chunking import Chunk, hash_text
from graph.services.subjects import sort_files_into

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
def albert(vector):
    """Albert answering with one fixed vector for the subject's words."""
    with mock.patch("graph.services.subjects.AlbertClient") as client:
        client.return_value.embed.return_value = [vector]
        yield client


def test_files_close_to_the_words_fall_into_the_subject():
    """A subject described in words gathers the files that resemble it."""
    user = factories.UserFactory()
    close = indexed_file("Marché hébergement", mix({0: 1.0, 1: 0.2}), user)
    far = indexed_file("Recette de cuisine", mix({50: 1.0}), user)
    topic = Topic.objects.create(name="Marchés publics", creator=user)

    with albert(mix({0: 1.0})):
        sort_files_into(topic)

    assert set(topic.memberships.values_list("item_id", flat=True)) == {close.id}
    assert ItemTopic.objects.get(item=close).score > 0.9
    assert not ItemTopic.objects.filter(item=far).exists()
    topic.refresh_from_db()
    assert topic.vector is not None


def test_a_pinned_file_defines_the_subject_and_stays_in_it():
    """What the user pins moves the subject and is never taken out."""
    user = factories.UserFactory()
    pinned = indexed_file("Note d'arbitrage", mix({10: 1.0}), user)
    like_pinned = indexed_file("Note de cadrage", mix({10: 1.0, 11: 0.3}), user)
    topic = Topic.objects.create(name="Budget", creator=user)
    ItemTopic.objects.create(item=pinned, topic=topic, pinned=True, score=1.0)

    # The words say nothing useful; the pinned file carries the subject.
    with albert(mix({40: 1.0})):
        sort_files_into(topic)

    assert set(topic.memberships.values_list("item_id", flat=True)) == {pinned.id, like_pinned.id}
    assert ItemTopic.objects.get(item=pinned).pinned is True


def test_a_subject_lets_go_of_a_file_that_no_longer_fits():
    """Rewriting the subject's words takes the files that drifted out."""
    user = factories.UserFactory()
    item = indexed_file("Marché hébergement", mix({0: 1.0}), user)
    topic = Topic.objects.create(name="Marchés", creator=user)
    with albert(mix({0: 1.0})):
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

    with albert(mix({0: 1.0})):
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
