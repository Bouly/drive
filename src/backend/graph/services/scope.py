"""
Whose drive a file belongs to, for every step that reads across the graph.

The graph is read per user: a file only travels to someone who can already
open it in Drive, and a subject only ever sorts the files of its owner. Both
questions are the same one, asked here once.

``Item.objects.readable_per_se`` cannot answer it. It reads an empty
``link_reach`` as "not restricted", and an empty reach is the normal state of
a file uploaded in a folder: it inherits the reach of the folder, and only a
workspace at the root carries one of its own. Every file of the instance
would then show in everyone's graph. It also looks for accesses on the item
alone, while an uploaded file carries none — its owner holds one on the
workspace above it.
"""

from collections import defaultdict

from django.db.models import Exists, OuterRef, Q

from core import models


def readable_by(user):
    """
    The items the user can open, scoped the way Drive scopes them.

    Reach and accesses are both inherited, so both are read off the item and
    its ancestors, as ``computed_link_reach`` does in Python. What comes out
    is what Drive lists for the user: what is shared with them, plus the
    link-shared items they opened at least once.
    """
    access = models.ItemAccess.objects.filter(
        Q(user=user) | Q(team__in=user.teams),
        item__path__ancestors=OuterRef("path"),
    )
    shared_by_link = models.Item.objects.filter(
        path__ancestors=OuterRef("path"),
        link_reach__in=[
            models.LinkReachChoices.PUBLIC,
            models.LinkReachChoices.AUTHENTICATED,
        ],
    )
    # A link only puts an item in someone's drive once they have followed it,
    # which is what Drive itself lists.
    opened_once = models.LinkTrace.objects.filter(user=user, item__path__ancestors=OuterRef("path"))
    return models.Item.objects.filter(
        Exists(access) | (Exists(shared_by_link) & Exists(opened_once))
    )


def live_files_of(user):
    """The files of that user's drive, trashed ones and their folders apart."""
    return readable_by(user).filter(
        type=models.ItemTypeChoices.FILE, ancestors_deleted_at__isnull=True
    )


def topics_reading(item, topics):
    """
    Among these subjects, the ones whose owner can read this file.

    A subject sorts the files of its owner's drive and no others, so a file
    arriving in one drive is never offered to the subjects of another. One
    query per owner, and owners are counted in people, not in files.
    """
    by_creator = defaultdict(list)
    for topic in topics:
        by_creator[topic.creator].append(topic)
    kept = []
    for creator, owned in by_creator.items():
        if readable_by(creator).filter(pk=item.pk).exists():
            kept.extend(owned)
    return kept
