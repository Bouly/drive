# Graphe des fichiers (app `graph`)

Chaîne backend qui relie les fichiers d'un Drive par leur contenu, en six étapes :

| Étape | Rôle | Qui / où |
| --- | --- | --- |
| 1. Extraire | texte brut du fichier (docx, odt, pdf, images OCR…) | branche `graph-extraction` |
| 2. Découper | passages de quelques centaines de mots | branche `graph-extraction` |
| 3. Représenter | un vecteur par passage (modèle d'embedding) | branche `graph-extraction` |
| **4. Stocker** | passages + vecteurs (pgvector), liens, sujets | **ici** : `models.py`, `services/storage.py` |
| 5. Relier | plus proches voisins, termes partagés | à venir |
| 6. Structurer | sujets, rapprochements inattendus | à venir |

Sur `main`, l'app ne contient que le stockage. Une version complète des étapes 1 à 3
(Apache Tika, découpage, embeddings `bge-m3` via TEI ou Ollama, commande `graph_prepare`)
est disponible sur la branche `graph-extraction` pour qui reprend ces étapes.

## Le contrat : ce que le stockage accepte

Un passage est un `graph.services.chunking.Chunk` :

```python
Chunk(index=0, text="…", text_hash=hash_text("…"), embedding=[...])  # 1024 floats
```

- **Dimension : 1024** (`GRAPH_EMBEDDING_DIM`), choisie pour le modèle `bge-m3`.
  Changer de modèle = changer ce réglage, une migration `AlterField`, et recalculer tous
  les vecteurs. À décider avant de remplir la base.
- Les vecteurs doivent être **normalisés** (norme 1) : la distance cosinus devient un
  simple produit scalaire et l'index HNSW est configuré pour ça (`vector_cosine_ops`).
- `text_hash` = sha256 du texte (`hash_text`) : deux passages identiques ont le même hash.

## Comment c'est stocké

Postgres 16 avec l'extension **pgvector** (image `pgvector/pgvector:0.8.6-pg16-trixie`,
mêmes données que `postgres:16`). La migration `0001_initial` crée l'extension puis :

| Table | Rôle |
| --- | --- |
| `drive_graph_chunk` | un passage : `item`, `index`, `text`, `text_hash`, `embedding vector(1024)`, `signature` (MinHash, libre pour l'étape 5). Index HNSW cosinus. Supprimé avec son item. |
| `drive_graph_link` | un lien `source → target` : `weight` (0..1), `kind` (semantic, lexical, copy, folder), `surprising`, `reason`, `evidence`. Pas de lien vers soi-même. |
| `drive_graph_topic` / `drive_graph_item_topic` | un sujet (`label`, `keywords`) et l'appartenance d'un item (un seul sujet par item) |

Une base vectorielle répond à une question : « quels sont les k vecteurs les plus proches
de celui-ci ? ». L'index HNSW (un graphe de voisinage à plusieurs niveaux) répond en
quelques millisecondes avec ~99 % de précision, au lieu de comparer à tout. Le garder
dans Postgres permet de filtrer par droits d'accès dans la même requête et d'avoir des
suppressions cohérentes (cascade), ce qu'une base vectorielle séparée ne donne pas.

## L'interface pour les autres étapes

Aucun SQL vectoriel ailleurs : tout passe par `graph.services.storage`.

```python
from core.models import Item
from graph.services import storage

storage.save_chunks(item, chunks)             # remplace les passages d'un item, rend le nombre stocké
vector = storage.item_vector(item)            # moyenne normalisée des passages, None si aucun

readable = Item.objects.readable_per_se(user) # les droits s'appliquent ici, dans la même requête
storage.nearest_items(vector, readable, k=6, min_similarity=0.55, exclude_item=item)
# -> [Neighbour(item_id, similarity)] du plus proche au plus lointain, similarity = 1 - distance cosinus

storage.nearest_chunks(vector, readable, k=10)  # pareil au niveau passage, avec le texte (la preuve)

storage.replace_links(item, [
    {"target": other, "weight": 0.82, "kind": "copy", "reason": "Passage repris", "evidence": "…", "surprising": True},
])
storage.delete_chunks(item)
```

Les liens sémantiques d'un item (étape 5 minimale) sont dans `graph.services.linking` :
`link_item(item, candidats)` garde les 4 voisins les plus proches (≥ 0,62 même sujet,
≥ 0,70 sujets différents = « rapprochement inattendu ») et écrit via `replace_links`.
C'est ce qu'appellent la tâche `graph.tasks.index_item` (à l'upload) et le seed Albert.

## Vérifier en local

```bash
make migrate                      # applique 0001_initial (extension + tables)
bin/pytest graph                  # 7 tests sur une vraie base pgvector
```

Dans un shell Django (`docker compose exec app-dev python manage.py shell`) :

```python
from core.models import Item
from graph.services import storage
from graph.services.chunking import Chunk, hash_text
item = Item.objects.filter(type="file", upload_state="ready").first()
storage.save_chunks(item, [Chunk(0, "test", hash_text("test"), [1.0] + [0.0] * 1023)])
storage.nearest_items([1.0] + [0.0] * 1023, Item.objects.all())
```
