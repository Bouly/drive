"""Tests for GET /api/v1.0/graph/."""

from datetime import timedelta

from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from core import factories, models

from graph.models import ItemIndex, ItemLink
from graph.services import storage
from graph.services.chunking import Chunk, hash_text

pytestmark = pytest.mark.django_db

URL = "/api/v1.0/graph/"


def unit(axis, dim=1024):
    """A unit vector along one axis."""
    vector = [0.0] * dim
    vector[axis] = 1.0
    return vector


def make_file(
    title,
    users=None,
    link_reach=models.LinkReachChoices.RESTRICTED,
    parent=None,
    link_traces=None,
):
    """A ready file, optionally in a folder, shared with users or opened by link."""
    return factories.ItemFactory(
        title=title,
        type=models.ItemTypeChoices.FILE,
        update_upload_state=models.ItemUploadStateChoices.READY,
        users=users or [],
        link_traces=link_traces or [],
        link_reach=link_reach,
        parent=parent,
        mimetype="application/pdf",
        size=1234,
    )


def make_drive(user, title="Mon espace"):
    """A workspace at the root, as Drive creates one: restricted, owned."""
    return factories.ItemFactory(
        title=title,
        type=models.ItemTypeChoices.FOLDER,
        link_reach=models.LinkReachChoices.RESTRICTED,
        users=[(user, models.RoleChoices.OWNER)],
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
    assert response.json() == {"files": [], "links": [], "topics": [], "scope": None}


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
    client = APIClient()
    client.force_login(user)
    data = client.get(URL).json()

    assert {f["title"] for f in data["files"]} == {"mine", "shared", "linked"}
    node = next(f for f in data["files"] if f["title"] == "mine")
    assert node["mimetype"] == "application/pdf"
    assert node["size"] == 1234
    assert node["creator"]

    assert {(l["target"], l["kind"]) for l in data["links"]} == {
        (str(shared.id), "semantic"),
        (str(linked.id), "folder"),
    }
    semantic = next(l for l in data["links"] if l["kind"] == "semantic")
    assert semantic["source"] == str(mine.id)
    assert semantic["weight"] == 0.8
    # The generic reason is not sent: the page rebuilds it from the weight.
    assert "reason" not in semantic


def test_a_file_uploaded_in_my_drive_stays_in_my_drive():
    """
    The graph of one account is not the graph of another.

    A file uploaded in a folder carries no reach of its own: it inherits the
    one of the folder. Reading that empty reach as "not restricted" used to
    show every file of the instance to every logged-in user.
    """
    me = factories.UserFactory()
    colleague = factories.UserFactory()
    drive = make_drive(me)
    with_chunk(make_file("salaires.xlsx", parent=drive, link_reach=None))

    client = APIClient()
    client.force_login(me)
    assert [f["title"] for f in client.get(URL).json()["files"]] == ["salaires.xlsx"]

    client.force_login(colleague)
    assert client.get(URL).json()["files"] == []


def test_a_folder_shared_with_me_brings_its_files():
    """Accesses are inherited: a file shared through its folder is in my graph."""
    me = factories.UserFactory()
    owner = factories.UserFactory()
    drive = make_drive(owner)
    shared = factories.ItemFactory(
        title="Dossier partagé",
        type=models.ItemTypeChoices.FOLDER,
        parent=drive,
        link_reach=None,
        users=[(me, models.RoleChoices.READER)],
    )
    with_chunk(make_file("note.pdf", parent=shared, link_reach=None))
    with_chunk(make_file("privé.pdf", parent=drive, link_reach=None))

    client = APIClient()
    client.force_login(me)
    assert [f["title"] for f in client.get(URL).json()["files"]] == ["note.pdf"]


def test_a_public_file_joins_the_graph_once_opened():
    """
    A link puts a file in someone's drive only once they have followed it.

    That is what Drive lists, and the graph draws the drive: a public file
    nobody opened is not everybody's file.
    """
    user = factories.UserFactory()
    with_chunk(make_file("public", link_reach=models.LinkReachChoices.PUBLIC))
    client = APIClient()
    client.force_login(user)
    assert client.get(URL).json()["files"] == []

    models.LinkTrace.objects.create(item=models.Item.objects.get(title="public"), user=user)
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


def test_folder_scope_holds_only_what_the_folder_holds():
    """?folder= draws the files of that folder, at any depth, and nothing else."""
    user = factories.UserFactory()
    folder = factories.ItemFactory(
        title="dossier", type=models.ItemTypeChoices.FOLDER, users=[user]
    )
    sub = factories.ItemFactory(parent=folder, type=models.ItemTypeChoices.FOLDER, users=[user])
    child = with_chunk(
        factories.ItemFactory(
            parent=folder,
            title="enfant",
            type=models.ItemTypeChoices.FILE,
            update_upload_state=models.ItemUploadStateChoices.READY,
            users=[user],
        )
    )
    grandchild = with_chunk(
        factories.ItemFactory(
            parent=sub,
            title="petit-enfant",
            type=models.ItemTypeChoices.FILE,
            update_upload_state=models.ItemUploadStateChoices.READY,
            users=[user],
        )
    )
    with_chunk(make_file("dehors", users=[user]))

    client = APIClient()
    client.force_login(user)
    data = client.get(URL, {"folder": str(folder.id)}).json()

    assert {f["id"] for f in data["files"]} == {str(child.id), str(grandchild.id)}
    # The folder frames the drawing, it is not drawn itself.
    assert str(folder.id) not in {f["id"] for f in data["files"]}
    assert data["scope"] == {"id": str(folder.id), "title": "dossier"}


def test_folder_scope_drops_the_links_that_leave_it():
    """A tie to a file outside the folder is left out rather than drawn to nothing."""
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
    sibling = with_chunk(
        factories.ItemFactory(
            parent=folder,
            type=models.ItemTypeChoices.FILE,
            update_upload_state=models.ItemUploadStateChoices.READY,
            users=[user],
        )
    )
    outside = with_chunk(make_file("dehors", users=[user]))
    storage.replace_links(
        inside,
        [
            {"target": sibling, "weight": 0.8, "kind": "semantic"},
            {"target": outside, "weight": 0.9, "kind": "semantic"},
        ],
    )

    client = APIClient()
    client.force_login(user)
    data = client.get(URL, {"folder": str(folder.id)}).json()

    assert [(link["source"], link["target"]) for link in data["links"]] == [
        (str(inside.id), str(sibling.id))
    ]


def test_folder_scope_of_an_unreadable_folder_is_a_404():
    """A folder the user cannot read is answered as a missing one."""
    user = factories.UserFactory()
    folder = factories.ItemFactory(type=models.ItemTypeChoices.FOLDER)
    client = APIClient()
    client.force_login(user)
    assert client.get(URL, {"folder": str(folder.id)}).status_code == 404


def test_folder_scope_of_a_file_is_a_404():
    """Only a folder frames a graph; a file has nothing to hold."""
    user = factories.UserFactory()
    item = with_chunk(make_file("fichier", users=[user]))
    client = APIClient()
    client.force_login(user)
    assert client.get(URL, {"folder": str(item.id)}).status_code == 404


def test_folder_scope_of_a_trashed_folder_is_a_404():
    """A folder in the trash frames nothing."""
    user = factories.UserFactory()
    folder = factories.ItemFactory(type=models.ItemTypeChoices.FOLDER, users=[user])
    folder.soft_delete()
    client = APIClient()
    client.force_login(user)
    assert client.get(URL, {"folder": str(folder.id)}).status_code == 404


def test_folder_scope_of_a_nonsense_id_is_a_404():
    """An id that is not even a uuid gets the same answer as a missing folder."""
    user = factories.UserFactory()
    client = APIClient()
    client.force_login(user)
    assert client.get(URL, {"folder": "pas-un-uuid"}).status_code == 404


def test_no_folder_draws_the_whole_drive():
    """Without the parameter the graph is the drive, folder or not."""
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

    client = APIClient()
    client.force_login(user)
    data = client.get(URL).json()
    assert {f["id"] for f in data["files"]} == {str(inside.id), str(outside.id)}
    assert data["scope"] is None


def test_a_drive_with_no_subject_is_given_none():
    """
    Subjects are written, never invented.

    A drive full of files and empty of subjects answers an empty list: the
    page draws no group at all, rather than naming ones nobody asked for.
    """
    user = factories.UserFactory()
    drive = make_drive(user)
    for name in ("un.pdf", "deux.pdf", "trois.pdf"):
        with_chunk(make_file(name, parent=drive, link_reach=None))

    client = APIClient()
    client.force_login(user)
    data = client.get(URL).json()
    assert len(data["files"]) == 3
    assert data["topics"] == []
    assert all(f["topics"] == [] for f in data["files"])
