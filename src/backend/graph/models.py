"""
Storage of the file graph (step 4 of the pipeline).

Three things are stored:

- ``ItemChunk``: a passage of a file with its embedding (a ``vector(1024)``
  column provided by the pgvector extension). Chunks are deleted with their
  item. Nearest-neighbour queries run on this table.
- ``ItemLink``: a directed edge between two items with a weight, a kind and
  a human-readable reason (step 5 writes them).

The other steps never touch pgvector directly: they go through
``graph.services.storage``.
"""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from pgvector.django import HnswIndex, VectorField

from core.models import BaseModel, Item


class ItemChunk(BaseModel):
    """A passage of an item's text and its embedding."""

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="chunks")
    # Position of the passage in the document, from 0.
    index = models.PositiveIntegerField(_("index"))
    text = models.TextField(_("text"))
    # sha256 of the text: two chunks with the same hash are the same passage.
    text_hash = models.CharField(_("text hash"), max_length=64, db_index=True)
    # Unit vector produced by the embedding model; cosine distance is 1 - dot.
    embedding = VectorField(dimensions=settings.GRAPH_EMBEDDING_DIM)
    # Optional fingerprint (MinHash) for near-duplicate detection, step 5.
    signature = models.JSONField(_("signature"), default=list, blank=True)

    class Meta:
        db_table = "drive_graph_chunk"
        verbose_name = _("Chunk")
        verbose_name_plural = _("Chunks")
        ordering = ("item", "index")
        constraints = [
            models.UniqueConstraint(fields=["item", "index"], name="unique_chunk_index_per_item"),
        ]
        indexes = [
            # Approximate nearest-neighbour index; m and ef_construction are
            # pgvector's recommended defaults for a few hundred thousand rows.
            HnswIndex(
                name="graph_chunk_embedding_hnsw",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
        ]

    def __str__(self):
        return f"{self.item_id}#{self.index}"


class ItemIndex(BaseModel):
    """
    Where an item stands in the indexing pipeline.

    Without it, a file analysed but holding no text (a photo, a scan of a
    blank page) would look like it is still being analysed, forever.
    """

    class State(models.TextChoices):
        """Result of the last indexing run."""

        PENDING = "pending", _("Being analysed")
        DONE = "done", _("Analysed")
        EMPTY = "empty", _("No text found")
        SKIPPED = "skipped", _("Nothing to analyse")
        FAILED = "failed", _("Analysis failed")

    item = models.OneToOneField(Item, on_delete=models.CASCADE, related_name="graph_index")
    state = models.CharField(_("state"), max_length=16, choices=State.choices)
    # Why it was skipped or how it failed, for the admin.
    detail = models.TextField(_("detail"), blank=True)

    class Meta:
        db_table = "drive_graph_index"
        verbose_name = _("Indexing state")
        verbose_name_plural = _("Indexing states")

    def __str__(self):
        return f"{self.item_id}: {self.state}"


class ItemLink(BaseModel):
    """A weighted relation between two items."""

    class Kind(models.TextChoices):
        """How the link was found."""

        SEMANTIC = "semantic", _("Semantic")  # close embeddings
        LEXICAL = "lexical", _("Lexical")  # rare terms in common
        COPY = "copy", _("Copy")  # passages reused word for word
        FOLDER = "folder", _("Folder")  # same folder

    source = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="links_out")
    target = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="links_in")
    # Strength of the relation, 0 to 1: every pair of indexed files is linked,
    # the weight is what tells a close pair from a distant one.
    weight = models.FloatField(_("weight"))
    kind = models.CharField(_("kind"), max_length=16, choices=Kind.choices)
    # Why the two files are related, shown to the user.
    reason = models.TextField(_("reason"), blank=True)
    # The passage that justifies the link, when there is one.
    evidence = models.TextField(_("evidence"), blank=True)

    class Meta:
        db_table = "drive_graph_link"
        verbose_name = _("Link")
        verbose_name_plural = _("Links")
        constraints = [
            models.UniqueConstraint(
                fields=["source", "target", "kind"], name="unique_link_per_kind"
            ),
            models.CheckConstraint(
                condition=~models.Q(source=models.F("target")), name="link_not_to_self"
            ),
        ]
        indexes = [models.Index(fields=["target", "kind"])]

    def __str__(self):
        return f"{self.source_id} -> {self.target_id} ({self.kind}, {self.weight:.2f})"


class Topic(BaseModel):
    """
    A subject someone created, that files fall into on their own.

    The subject is defined by what its owner says of it, in words and by
    example: a name, a description, and the files pinned to it. Those give it
    a vector, and every file close enough to that vector joins it. Nothing is
    invented: the names are the user's, and a file is in a subject because it
    resembles what the user put there.
    """

    name = models.CharField(_("name"), max_length=255)
    # What the subject is about, in a sentence or two. A name alone makes a
    # weak vector; a description, or a few pinned files, make a solid one.
    description = models.TextField(_("description"), blank=True)
    creator = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="graph_topics"
    )
    # The description and the pinned files, weighing half each, normalized;
    # None while the subject has neither. Used to shortlist the files worth
    # reading, never to decide on its own.
    vector = VectorField(dimensions=settings.GRAPH_EMBEDDING_DIM, null=True, blank=True)
    # The reading score a file has to reach to belong here, kept from the last
    # sort. The reranker answers on its own scale for every subject, so the
    # bar of one subject means nothing to another and has to be remembered.
    cut = models.FloatField(_("cut"), default=0.0)

    class Meta:
        db_table = "drive_graph_topic"
        verbose_name = _("Topic")
        verbose_name_plural = _("Topics")
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(fields=["creator", "name"], name="unique_topic_name_per_user"),
        ]

    def __str__(self):
        return self.name


class ItemTopic(BaseModel):
    """
    A file in a subject, because its owner put it there or because it fits.

    ``pinned`` marks the files the user chose: they define the subject and are
    never taken out by a recomputation. The others carry the ``score`` that
    put them there.
    """

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="topics")
    topic = models.ForeignKey(Topic, on_delete=models.CASCADE, related_name="memberships")
    # True when the user pinned the file to the subject.
    pinned = models.BooleanField(_("pinned"), default=False)
    # Cosine similarity with the subject, 1.0 for a pinned file.
    score = models.FloatField(_("score"), default=0.0)

    class Meta:
        db_table = "drive_graph_item_topic"
        verbose_name = _("Item topic")
        verbose_name_plural = _("Item topics")
        ordering = ("-score",)
        constraints = [
            models.UniqueConstraint(fields=["item", "topic"], name="unique_item_per_topic"),
        ]

    def __str__(self):
        return f"{self.item_id} in {self.topic_id}"
