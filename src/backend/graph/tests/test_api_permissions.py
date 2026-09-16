"""
What the graph must never show: the access surface of GET /api/v1.0/graph/.

The graph draws a drive, so it has to answer exactly what Drive answers. Two
ways of getting that wrong have bitten already: reading an empty
``link_reach`` as "not restricted", which showed every file of the instance
to every logged-in user, and looking for accesses on the item alone, while an
uploaded file carries none — its owner holds one on the workspace above it.

``?folder=`` widened that surface: a scoped graph names the files it holds,
so framing it on a folder must not become a way of reading one.
"""

import pytest
from rest_framework.test import APIClient

from core import factories, models

from graph.models import ItemLink, ItemTopic, Topic
from graph.services import storage
from graph.services.chunking import Chunk, hash_text

pytestmark = pytest.mark.django_db

URL = "/api/v1.0/graph/"


def unit(axis, dim=1024):
    """A unit vector along one axis."""
    vector = [0.0] * dim
    vector[axis] = 1.0
    return vector


def with_chunk(item, axis=0):
    """Give the item one stored passage so it takes part in the graph."""
    storage.save_chunks(item, [Chunk(0, "passage", hash_text("passage"), unit(axis))])
    return item


def make_drive(user, title="Mon espace"):
    """A workspace at the root, as Drive creates one: restricted, owned."""
    return factories.ItemFactory(
        title=title,
        type=models.ItemTypeChoices.FOLDER,
        link_reach=models.LinkReachChoices.RESTRICTED,
        users=[(user, models.RoleChoices.OWNER)],
    )


def make_folder(title, parent=None, users=None, link_reach=None, link_traces=None):
    """A folder inside a drive; no reach of its own, it inherits the one above."""
    return factories.ItemFactory(
        title=title,
        type=models.ItemTypeChoices.FOLDER,
        parent=parent,
        users=users or [],
        link_traces=link_traces or [],
        link_reach=link_reach,
    )


def make_file(title, parent=None, users=None, link_reach=None, link_traces=None):
    """A ready file, uploaded in a folder: like Drive's, it carries no reach."""
    return factories.ItemFactory(
        title=title,
        type=models.ItemTypeChoices.FILE,
        update_upload_state=models.ItemUploadStateChoices.READY,
        parent=parent,
        users=users or [],
        link_traces=link_traces or [],
        link_reach=link_reach,
        mimetype="application/pdf",
        size=1234,
    )


def titles(response):
    """The file names the graph answered."""
    return {f["title"] for f in response.json()["files"]}


def logged(user):
    """A client already logged in as that user."""
    client = APIClient()
    client.force_login(user)
    return client


# --- the whole graph ----------------------------------------------------


def test_a_whole_foreign_drive_stays_out():
    """
    Nothing of somebody else's workspace reaches my graph.

    The files carry no reach of their own and no access of their own: both
    hang on the workspace above them, which is the shape Drive creates and
    the shape that used to leak.
    """
    me = factories.UserFactory()
    owner = factories.UserFactory()
    theirs = make_drive(owner, "Espace de l'autre")
    folder = make_folder("Comptabilité", parent=theirs)
    with_chunk(make_file("salaires.xlsx", parent=folder))
    with_chunk(make_file("bilan.pdf", parent=theirs))
    with_chunk(make_file("à moi.pdf", parent=make_drive(me)))

    assert titles(logged(me).get(URL)) == {"à moi.pdf"}
    # And the same the other way round: each drive sees its own.
    assert titles(logged(owner).get(URL)) == {"salaires.xlsx", "bilan.pdf"}


def test_losing_an_access_takes_the_files_out_again():
    """A graph is read live: what sharing gave, unsharing takes back."""
    me = factories.UserFactory()
    owner = factories.UserFactory()
    drive = make_drive(owner)
    shared = make_folder("partagé", parent=drive, users=[(me, models.RoleChoices.READER)])
    with_chunk(make_file("note.pdf", parent=shared))

    client = logged(me)
    assert titles(client.get(URL)) == {"note.pdf"}

    models.ItemAccess.objects.filter(item=shared, user=me).delete()
    assert titles(client.get(URL)) == set()


def test_a_link_never_names_a_file_i_cannot_open():
    """
    A link is dropped whichever end of it I cannot read.

    Sending it would name a file, and its id, to someone with no right to
    know it exists.
    """
    me = factories.UserFactory()
    owner = factories.UserFactory()
    mine = with_chunk(make_file("à moi.pdf", parent=make_drive(me)), 0)
    theirs = with_chunk(make_file("secret.pdf", parent=make_drive(owner)), 1)
    # One link each way: neither direction may travel.
    storage.replace_links(mine, [{"target": theirs, "weight": 0.9, "kind": ItemLink.Kind.SEMANTIC}])
    storage.replace_links(theirs, [{"target": mine, "weight": 0.9, "kind": ItemLink.Kind.SEMANTIC}])

    data = logged(me).get(URL).json()
    assert data["links"] == []
    assert titles(logged(me).get(URL)) == {"à moi.pdf"}


def test_the_evidence_of_a_dropped_link_never_travels():
    """
    The passage justifying a link is content: it goes only with the link.

    A link to a file I cannot read is left out, and its evidence with it.
    """
    me = factories.UserFactory()
    owner = factories.UserFactory()
    mine = with_chunk(make_file("à moi.pdf", parent=make_drive(me)), 0)
    theirs = with_chunk(make_file("secret.pdf", parent=make_drive(owner)), 1)
    storage.replace_links(
        mine,
        [
            {
                "target": theirs,
                "weight": 0.95,
                "kind": ItemLink.Kind.SEMANTIC,
                "evidence": "le salaire de la directrice est de",
            }
        ],
    )

    body = logged(me).get(URL).content.decode()
    assert "le salaire de la directrice" not in body
    assert str(theirs.id) not in body


def test_the_subjects_of_another_user_are_not_listed():
    """Subjects are written per drive: I am shown mine, never somebody else's."""
    me = factories.UserFactory()
    owner = factories.UserFactory()
    Topic.objects.create(name="Le leur", creator=owner)
    Topic.objects.create(name="Le mien", creator=me)

    data = logged(me).get(URL).json()
    assert [t["name"] for t in data["topics"]] == ["Le mien"]


def test_a_foreign_subject_holding_my_file_is_not_shown_on_it():
    """
    A file of mine sorted into somebody else's subject says nothing of it.

    The membership belongs to the other drive; naming it on my node would
    leak the existence of a subject I cannot read.
    """
    me = factories.UserFactory()
    owner = factories.UserFactory()
    mine = with_chunk(make_file("à moi.pdf", parent=make_drive(me)))
    theirs = Topic.objects.create(name="Le leur", creator=owner)
    ItemTopic.objects.create(item=mine, topic=theirs, score=0.9)

    data = logged(me).get(URL).json()
    node = next(f for f in data["files"] if f["title"] == "à moi.pdf")
    assert node["topics"] == []
    assert str(theirs.id) not in logged(me).get(URL).content.decode()


# --- the scoped graph ---------------------------------------------------


def test_scoping_on_a_foreign_folder_is_a_404():
    """A folder of somebody else's drive frames nothing of mine."""
    me = factories.UserFactory()
    owner = factories.UserFactory()
    folder = make_folder("Comptabilité", parent=make_drive(owner))
    with_chunk(make_file("salaires.xlsx", parent=folder))

    assert logged(me).get(URL, {"folder": str(folder.id)}).status_code == 404


def test_scoping_on_the_parent_of_a_folder_shared_with_me_is_a_404():
    """
    Sharing a folder does not share the one above it.

    Walking up from what I was given would otherwise read a whole drive one
    parent at a time.
    """
    me = factories.UserFactory()
    owner = factories.UserFactory()
    drive = make_drive(owner)
    parent = make_folder("Direction", parent=drive)
    shared = make_folder("Pour moi", parent=parent, users=[(me, models.RoleChoices.READER)])
    with_chunk(make_file("note.pdf", parent=shared))
    with_chunk(make_file("salaires.xlsx", parent=parent))

    client = logged(me)
    assert client.get(URL, {"folder": str(parent.id)}).status_code == 404
    assert client.get(URL, {"folder": str(drive.id)}).status_code == 404
    # What was actually shared still works, and holds only its own file.
    assert titles(client.get(URL, {"folder": str(shared.id)})) == {"note.pdf"}


def test_a_scoped_graph_holds_no_file_the_reader_cannot_open():
    """
    Framing a shared folder shows its files, not the drive it sits in.

    The scope narrows what I can already read; it never widens it.
    """
    me = factories.UserFactory()
    owner = factories.UserFactory()
    drive = make_drive(owner)
    shared = make_folder("Pour moi", parent=drive, users=[(me, models.RoleChoices.READER)])
    deeper = make_folder("Annexes", parent=shared)
    with_chunk(make_file("note.pdf", parent=shared))
    with_chunk(make_file("annexe.pdf", parent=deeper))
    with_chunk(make_file("salaires.xlsx", parent=drive))

    assert titles(logged(me).get(URL, {"folder": str(shared.id)})) == {"note.pdf", "annexe.pdf"}


def test_a_scoped_link_never_names_a_file_outside_my_reach():
    """Inside a scope, a link to an unreadable file is dropped twice over."""
    me = factories.UserFactory()
    owner = factories.UserFactory()
    drive = make_drive(owner)
    shared = make_folder("Pour moi", parent=drive, users=[(me, models.RoleChoices.READER)])
    inside = with_chunk(make_file("note.pdf", parent=shared), 0)
    secret = with_chunk(make_file("salaires.xlsx", parent=drive), 1)
    storage.replace_links(inside, [{"target": secret, "weight": 0.9, "kind": ItemLink.Kind.COPY}])

    body = logged(me).get(URL, {"folder": str(shared.id)}).content.decode()
    assert str(secret.id) not in body
    assert "salaires.xlsx" not in body


def test_a_public_folder_cannot_be_scoped_before_it_is_opened():
    """
    A link puts a folder in my drive only once I have followed it.

    Scoping is a read: it must wait for the same thing the drive waits for.
    """
    user = factories.UserFactory()
    folder = make_folder("Public", link_reach=models.LinkReachChoices.PUBLIC)
    with_chunk(make_file("tract.pdf", parent=folder))

    client = logged(user)
    assert client.get(URL, {"folder": str(folder.id)}).status_code == 404

    models.LinkTrace.objects.create(item=folder, user=user)
    response = client.get(URL, {"folder": str(folder.id)})
    assert response.status_code == 200
    assert titles(response) == {"tract.pdf"}


def test_a_trashed_file_stays_out_of_a_scoped_graph():
    """The scope draws a live folder: what is in the bin is not in it."""
    me = factories.UserFactory()
    folder = make_folder("Dossier", parent=make_drive(me))
    with_chunk(make_file("gardé.pdf", parent=folder))
    binned = with_chunk(make_file("jeté.pdf", parent=folder))
    binned.soft_delete()

    assert titles(logged(me).get(URL, {"folder": str(folder.id)})) == {"gardé.pdf"}


def test_anonymous_cannot_scope_either():
    """The scope is no way around logging in."""
    folder = make_folder("Dossier", parent=make_drive(factories.UserFactory()))
    assert APIClient().get(URL, {"folder": str(folder.id)}).status_code == 401


def test_a_scoped_graph_is_always_a_part_of_the_whole_one():
    """
    Whatever the folder, scoping can only ever remove.

    The property the two code paths have to agree on: the scope is a filter
    of the drive, never a second way of reading it.
    """
    me = factories.UserFactory()
    owner = factories.UserFactory()
    drive = make_drive(me)
    folder = make_folder("Dossier", parent=drive)
    with_chunk(make_file("dedans.pdf", parent=folder))
    with_chunk(make_file("dehors.pdf", parent=drive))
    # A drive of somebody else's, to make sure neither path reaches it.
    with_chunk(make_file("ailleurs.pdf", parent=make_drive(owner)))

    client = logged(me)
    whole = titles(client.get(URL))
    scoped = titles(client.get(URL, {"folder": str(folder.id)}))
    assert scoped <= whole
    assert "ailleurs.pdf" not in whole
    assert scoped == {"dedans.pdf"}
