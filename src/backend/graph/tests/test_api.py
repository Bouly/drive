"""Tests for GET /api/v1.0/graph/."""

from datetime import timedelta

from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from core import factories, models

from graph.models import ItemIndex, ItemLink, ItemTopic, Topic
from graph.services import storage
from graph.services.chunking import Chunk, hash_text

pytestmark = pytest.mark.django_db

URL = "/api/v1.0/graph/"


def unit(axis, dim=1024):
    """A unit vector along one axis."""
    vector = [0.0] * dim
    vector[axis] = 1.0
    return vector


def make_file(title, users=None, link_reach=models.LinkReachChoices.RESTRICTED):
    """A ready file, optionally shared with users."""
    return factories.ItemFactory(
        title=title,
        type=models.ItemTypeChoices.FILE,
        update_upload_state=models.ItemUploadStateChoices.READY,
        users=users or [],
        link_reach=link_reach,
        mimetype="application/pdf",
        size=1234,
    )


def with_chunk(item, axis=0):
    """Give the item one stored passage so it takes part in the graph."""
    storage.save_chunks(item, [Chunk(0, "passage", hash_text("passage"), unit(axis))])
    return item


def test_anonymous_is_rejected():
    """The graph needs a logged-in user."""
    assert APIClient().get(URL).status_code == 401


def test_empty_graph():
    """A user with no indexed file gets an empty graph, not an error."""
    user = factories.UserFactory()
    client = APIClient()
    client.force_login(user)
    response = client.get(URL)
    assert response.status_code == 200
    assert response.json() == {"files": [], "links": [], "clusters": []}


def test_graph_only_shows_readable_files_and_their_links():
    """Files the user cannot read are absent, and so are links touching them."""
    user = factories.UserFactory()
    mine = with_chunk(make_file("mine", users=[user]), 0)
    shared = with_chunk(make_file("shared", users=[user]), 1)
    secret = with_chunk(make_file("secret"), 2)
    # A file with a link but no passage is still a node.
    linked = make_file("linked", users=[user])

    storage.replace_links(
        mine,
        [
            {"target": shared, "weight": 0.8, "kind": ItemLink.Kind.SEMANTIC, "reason": "proches"},
            {"target": secret, "weight": 0.9, "kind": ItemLink.Kind.COPY},
            {"target": linked, "weight": 0.5, "kind": ItemLink.Kind.FOLDER},
        ],
    )
    topic = Topic.objects.create(label="Budget", keywords=["budget", "subvention"])
    ItemTopic.objects.create(item=mine, topic=topic)
    Topic.objects.create(label="Unused")

    client = APIClient()
    client.force_login(user)
    data = client.get(URL).json()

    assert {f["title"] for f in data["files"]} == {"mine", "shared", "linked"}
    node = next(f for f in data["files"] if f["title"] == "mine")
    assert node["mimetype"] == "application/pdf"
    assert node["size"] == 1234
    assert node["creator"]
    assert node["cluster"] == str(topic.id)
    assert next(f for f in data["files"] if f["title"] == "shared")["cluster"] is None

    assert {(l["target"], l["kind"]) for l in data["links"]} == {
        (str(shared.id), "semantic"),
        (str(linked.id), "folder"),
    }
    semantic = next(l for l in data["links"] if l["kind"] == "semantic")
    assert semantic["source"] == str(mine.id)
    assert semantic["weight"] == 0.8
    assert semantic["reason"] == "proches"
    assert semantic["surprising"] is False

    assert data["clusters"] == [
        {"id": str(topic.id), "label": "Budget", "keywords": ["budget", "subvention"]}
    ]


def test_public_files_are_part_of_the_graph():
    """A file reachable by link is readable, hence visible in the graph."""
    user = factories.UserFactory()
    with_chunk(make_file("public", link_reach=models.LinkReachChoices.PUBLIC))
    client = APIClient()
    client.force_login(user)
    assert [f["title"] for f in client.get(URL).json()["files"]] == ["public"]


def test_a_file_shows_up_before_it_is_analysed():
    """An uploaded file is in the graph right away, marked as being analysed."""
    user = factories.UserFactory()
    make_file("rapport.pdf", users=[user])
    factories.ItemFactory(
        title="photos.zip",
        type=models.ItemTypeChoices.FILE,
        update_upload_state=models.ItemUploadStateChoices.READY,
        users=[user],
        mimetype="application/zip",
        size=999,
    )
    with_chunk(make_file("analysé", users=[user]))

    client = APIClient()
    client.force_login(user)
    status = {f["title"]: f["status"] for f in client.get(URL).json()["files"]}

    assert status == {"rapport.pdf": "pending", "photos.zip": "skipped", "analysé": "indexed"}


def test_a_file_analysed_without_text_stops_pending():
    """A photo analysed in vain is not shown as being analysed forever."""
    user = factories.UserFactory()
    photo = make_file("chat.jpg", users=[user])
    ItemIndex.objects.create(item=photo, state=ItemIndex.State.EMPTY)

    client = APIClient()
    client.force_login(user)
    assert [f["status"] for f in client.get(URL).json()["files"]] == ["empty"]


def test_a_file_never_queued_stops_pending_after_a_while():
    """A file nobody ever analysed does not pulse forever."""
    user = factories.UserFactory()
    old_file = make_file("oublié.pdf", users=[user])
    models.Item.objects.filter(id=old_file.id).update(
        updated_at=timezone.now() - timedelta(hours=2)
    )

    client = APIClient()
    client.force_login(user)
    assert [f["status"] for f in client.get(URL).json()["files"]] == ["idle"]


def test_files_of_a_trashed_folder_are_hidden():
    """Trashing a folder takes its files out of the graph, links included."""
    user = factories.UserFactory()
    folder = factories.ItemFactory(type=models.ItemTypeChoices.FOLDER, users=[user])
    inside = with_chunk(
        factories.ItemFactory(
            parent=folder,
            type=models.ItemTypeChoices.FILE,
            update_upload_state=models.ItemUploadStateChoices.READY,
            users=[user],
        )
    )
    outside = with_chunk(make_file("dehors", users=[user]))
    storage.replace_links(outside, [{"target": inside, "weight": 0.9, "kind": "semantic"}])
    folder.soft_delete()

    client = APIClient()
    client.force_login(user)
    data = client.get(URL).json()
    assert [f["id"] for f in data["files"]] == [str(outside.id)]
    assert data["links"] == []


def test_trashed_files_are_hidden():
    """A file in the trash leaves the graph until it is restored."""
    user = factories.UserFactory()
    item = with_chunk(make_file("bin", users=[user]))
    item.soft_delete()
    client = APIClient()
    client.force_login(user)
    assert client.get(URL).json()["files"] == []
