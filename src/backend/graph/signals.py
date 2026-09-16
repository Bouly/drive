"""
Keep the graph in step with the files it draws.

Indexing happens when an upload is analysed, but a file also leaves and comes
back through the trash, and that changes the links of the files around it.
``soft_delete`` and ``restore`` both save the item with those two fields, so
one receiver covers them.
"""

from django.db import transaction
from django.db.models import signals
from django.dispatch import receiver

from core.models import Item, ItemTypeChoices

TRASH_FIELDS = {"deleted_at", "ancestors_deleted_at"}


@receiver(signals.post_save, sender=Item)
def item_trashed_or_restored(sender, instance, created, update_fields=None, **kwargs):
    # pylint: disable=unused-argument
    """Drop the links of a trashed file, rebuild those of a restored one."""
    if created or not update_fields or not TRASH_FIELDS <= set(update_fields):
        return

    # Imported here: the tasks import the models this app is still loading.
    from graph.tasks import forget_from_graph, index_item  # noqa: PLC0415

    item_id = instance.id
    is_folder = instance.type == ItemTypeChoices.FOLDER
    if instance.deleted_at is None:
        # Restored: index it again, which links it and its neighbours back.
        if not is_folder:
            transaction.on_commit(lambda: index_item.delay(item_id))
        else:
            transaction.on_commit(lambda: forget_from_graph.delay(item_id, restore=True))
        return
    transaction.on_commit(lambda: forget_from_graph.delay(item_id))
