"""Read-only admin views to inspect what the graph storage contains."""

from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html, format_html_join

from core.models import Item

from graph.models import ItemChunk, ItemIndex, ItemLink
from graph.services import storage

# Values of the vector shown before the "show all" toggle.
PREVIEW_VALUES = 8
# Passages listed as the closest to the one being viewed.
NEAREST_PASSAGES = 10


def _admin_link(url_name, pk, label):
    """Link to the admin change page ``url_name`` of an object."""
    return format_html('<a href="{}">{}</a>', reverse(url_name, args=[pk]), label)


def _table(headers, rows):
    """A small HTML table; ``rows`` are tuples of already escaped cells."""
    head = format_html_join("", "<th>{}</th>", ((header,) for header in headers))
    body = format_html_join(
        "",
        "<tr>{}</tr>",
        ((format_html_join("", "<td>{}</td>", ((cell,) for cell in row)),) for row in rows),
    )
    return format_html("<table><thead><tr>{}</tr></thead><tbody>{}</tbody></table>", head, body)


class ReadOnlyAdmin(admin.ModelAdmin):
    """Rows are written by the pipeline, never by hand."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ItemChunk)
class ItemChunkAdmin(ReadOnlyAdmin):
    """Passages, their vector and their neighbours."""

    list_display = ("item", "index", "text_hash", "word_count", "updated_at")
    search_fields = ("item__title", "text")
    raw_id_fields = ("item",)
    fields = (
        "item",
        "index",
        "text",
        "text_hash",
        "embedding_preview",
        "nearest_passages",
        "item_neighbours",
    )
    readonly_fields = ("embedding_preview", "nearest_passages", "item_neighbours")

    @admin.display(description="words")
    def word_count(self, obj):
        """Length of the passage, in words."""
        return len(obj.text.split())

    @admin.display(description="vector")
    def embedding_preview(self, obj):
        """Dimension, norm and first values of the vector, the full one on demand."""
        vector = [float(x) for x in obj.embedding]
        norm = sum(x * x for x in vector) ** 0.5
        preview = ", ".join(f"{x:.4f}" for x in vector[:PREVIEW_VALUES])
        full = ", ".join(f"{x:.6f}" for x in vector)
        return format_html(
            "{} dimensions, norm {}<br><code>[{}, …]</code>"
            '<details><summary>show all</summary><code style="word-break: break-all">[{}]</code>'
            "</details>",
            len(vector),
            f"{norm:.4f}",
            preview,
            full,
        )

    @admin.display(description="closest passages in other files")
    def nearest_passages(self, obj):
        """The passages of other files whose vector is closest to this one."""
        neighbours = storage.nearest_chunks(
            list(obj.embedding), Item.objects.all(), k=NEAREST_PASSAGES, exclude_item=obj.item
        )
        if not neighbours:
            return "-"
        chunks = {
            str(chunk.pk): chunk
            for chunk in ItemChunk.objects.select_related("item").filter(
                pk__in=[neighbour.chunk_id for neighbour in neighbours]
            )
        }
        rows = []
        for neighbour in neighbours:
            chunk = chunks[neighbour.chunk_id]
            excerpt = chunk.text if len(chunk.text) <= 200 else f"{chunk.text[:200]}…"
            rows.append(
                (
                    f"{neighbour.similarity:.3f}",
                    _admin_link("admin:core_item_change", chunk.item_id, chunk.item.title),
                    _admin_link("admin:graph_itemchunk_change", chunk.pk, f"#{chunk.index}"),
                    excerpt,
                )
            )
        return _table(("similarity", "file", "passage", "text"), rows)

    @admin.display(description="neighbours of the file (stored links)")
    def item_neighbours(self, obj):
        """The links stored from this passage's file, as drawn on the graph page."""
        links = (
            ItemLink.objects.filter(source=obj.item).select_related("target").order_by("-weight")
        )
        if not links:
            return "-"
        rows = [
            (
                f"{link.weight:.3f}",
                _admin_link("admin:core_item_change", link.target_id, link.target.title),
                link.get_kind_display(),
                _admin_link("admin:graph_itemlink_change", link.pk, "link"),
            )
            for link in links
        ]
        return _table(("weight", "file", "kind", ""), rows)


@admin.register(ItemLink)
class ItemLinkAdmin(ReadOnlyAdmin):
    """Links between items."""

    list_display = ("source", "target", "kind", "weight", "updated_at")
    list_filter = ("kind",)
    search_fields = ("source__title", "target__title", "reason")
    raw_id_fields = ("source", "target")


@admin.register(ItemIndex)
class ItemIndexAdmin(ReadOnlyAdmin):
    """Where each file stands in the indexing pipeline."""

    list_display = ("item", "state", "detail", "updated_at")
    list_filter = ("state",)
    search_fields = ("item__title", "detail")
    raw_id_fields = ("item",)
