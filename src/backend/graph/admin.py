"""Read-only admin views to inspect what the graph storage contains."""

from django.contrib import admin

from graph.models import ItemChunk, ItemLink, Topic


class ReadOnlyAdmin(admin.ModelAdmin):
    """Rows are written by the pipeline, never by hand."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ItemChunk)
class ItemChunkAdmin(ReadOnlyAdmin):
    """Passages and their hashes; vectors stay hidden (1024 numbers)."""

    list_display = ("item", "index", "text_hash", "word_count", "updated_at")
    search_fields = ("item__title", "text")
    raw_id_fields = ("item",)
    exclude = ("embedding",)

    @admin.display(description="words")
    def word_count(self, obj):
        """Length of the passage, in words."""
        return len(obj.text.split())


@admin.register(ItemLink)
class ItemLinkAdmin(ReadOnlyAdmin):
    """Links between items."""

    list_display = ("source", "target", "kind", "weight", "surprising", "updated_at")
    list_filter = ("kind", "surprising")
    search_fields = ("source__title", "target__title", "reason")
    raw_id_fields = ("source", "target")


@admin.register(Topic)
class TopicAdmin(ReadOnlyAdmin):
    """Topics and their keywords."""

    list_display = ("label", "automatic", "keywords", "updated_at")
    list_filter = ("automatic",)
    search_fields = ("label",)
