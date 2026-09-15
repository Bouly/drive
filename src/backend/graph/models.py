"""
Storage of the file graph (step 4 of the pipeline): chunks with their
embeddings, links between items and topics.

The extraction and embedding services (graph.services) produce
``graph.services.chunking.Chunk`` objects and vectors of
``settings.GRAPH_EMBEDDING_DIM`` floats; the models storing them live here.
"""
