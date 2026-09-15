# Graphe des fichiers (app `graph`)

Chaîne backend qui relie les fichiers d'un Drive par leur contenu. Six étapes :

| Étape | Rôle | Où |
| --- | --- | --- |
| 1. Extraire | texte brut du fichier (docx, odt, pdf, pptx, xlsx, images OCR…) via Apache Tika | `services/extraction.py` |
| 2. Découper | chunks de ~350 mots, chevauchement 50 | `services/chunking.py` |
| 3. Représenter | un vecteur par chunk, modèle `bge-m3` servi par TEI (prod) ou Ollama (local) | `services/embeddings.py` |
| 4. Stocker | chunks + vecteurs (pgvector), liens, sujets | `models.py`, `services/storage.py` |
| 5. Relier | plus proches voisins, termes partagés | à venir |
| 6. Structurer | sujets, rapprochements inattendus | à venir |

`pipeline.prepare_item(item)` enchaîne 1 → 3 et rend des `Chunk` portant leur `embedding`.

## Choix fixés

- **Modèle** : `BAAI/bge-m3`, **1024 dimensions**, multilingue, contexte long, aucun préfixe
  à ajouter aux textes. Alternative rapide : `intfloat/multilingual-e5-base` (768 dims,
  préfixes `passage: ` / `query: `). Changer de modèle = changer `GRAPH_EMBEDDING_MODEL`,
  `GRAPH_EMBEDDING_DIM`, les préfixes, et **recalculer tous les vecteurs**.
- **Distance** : cosinus, vecteurs normalisés par le serveur (`normalize: true`).
- **Chunks** : 350 mots, chevauchement 50, découpage par paragraphes. Chaque chunk a un
  `text_hash` (sha256) pour repérer les passages identiques.
- **Formats** : bureautique, PDF, texte, JSON, images (OCR fra+eng). Vidéo et audio exclus.
  Taille max 50 Mo. Voir `GRAPH_ALLOWED_MIMETYPES`.

## Étape 4 · Stockage (pgvector)

Les vecteurs vivent dans le Postgres de Drive grâce à l'extension **pgvector**
(image `pgvector/pgvector:0.8.6-pg16-trixie`, même Postgres 16, données conservées).
La migration `0001_initial` crée l'extension puis les tables :

| Table | Rôle |
| --- | --- |
| `drive_graph_chunk` | un passage : `item`, `index`, `text`, `text_hash` (sha256), `embedding vector(1024)`, `signature` (MinHash, optionnel). Index HNSW cosinus. |
| `drive_graph_link` | un lien `source → target` : `weight` (0..1), `kind` (semantic, lexical, copy, folder), `surprising`, `reason`, `evidence` |
| `drive_graph_topic` / `drive_graph_item_topic` | un sujet (`label`, `keywords`) et l'appartenance d'un item |

Les autres étapes n'écrivent jamais de SQL vectoriel : elles passent par
`graph.services.storage` :

```python
from graph.services import storage

storage.save_chunks(item, chunks)          # chunks = sortie de pipeline.prepare_item
storage.nearest_items(vector, items, k=6)  # items = Item.objects.readable_per_se(user)
storage.nearest_chunks(vector, items, k=10)
storage.item_vector(item)                  # moyenne normalisée des chunks
storage.replace_links(item, [{"target": other, "weight": 0.8, "kind": "semantic", "reason": "…"}])
storage.delete_chunks(item)
```

`nearest_items` renvoie des `Neighbour(item_id, similarity)` triés du plus proche au plus
lointain, avec `similarity = 1 - distance cosinus` (1.0 = identique). Le seuil par défaut
est 0,55. Passer un queryset d'items **lisibles par l'utilisateur** est ce qui fait
respecter les droits : le filtre s'applique dans la même requête SQL.

## Lancer les services en local

```bash
docker compose --profile graph up -d tika embeddings embeddings-pull
```

En local, les embeddings sont servis par Ollama (fonctionne sur Mac Apple silicon, que
text-embeddings-inference ne supporte pas). `embeddings-pull` télécharge le modèle
(~1,2 Go) dans `data/embeddings/` la première fois. Vérifier :

```bash
curl -s localhost:11434/api/tags | jq '.models[].name'   # "bge-m3:latest"
curl -s localhost:9998/tika                               # page d'accueil Tika
```

En production (`deploy/`), c'est text-embeddings-inference qui sert `BAAI/bge-m3`,
plus rapide sur x86 ; le code ne change pas, seul `GRAPH_EMBEDDING_BACKEND` diffère.

Puis tester sur de vrais fichiers du Drive local (sans rien écrire en base) :

```bash
docker compose exec app-dev python manage.py graph_prepare --latest 3 --show 2
```

## Variables

| Variable | Défaut | Rôle |
| --- | --- | --- |
| `GRAPH_TIKA_URL` | `http://tika:9998` | serveur Tika |
| `GRAPH_EMBEDDING_BACKEND` | `tei` | `tei` ou `ollama` (local : `ollama`) |
| `GRAPH_EMBEDDING_URL` | `http://embeddings:80` | serveur d'embeddings (Ollama : port 11434) |
| `GRAPH_EMBEDDING_MODEL` | `BAAI/bge-m3` | modèle (`bge-m3` côté Ollama) |
| `GRAPH_EMBEDDING_DIM` | `1024` | taille des vecteurs, doit correspondre au modèle |
| `GRAPH_EMBEDDING_PASSAGE_PREFIX` / `_QUERY_PREFIX` | vide | préfixes des modèles e5 |
| `GRAPH_CHUNK_WORDS` / `GRAPH_CHUNK_OVERLAP` | `350` / `50` | découpage |
| `GRAPH_MAX_FILE_SIZE` | 50 Mo | au-delà, le fichier est ignoré |
| `GRAPH_ALLOWED_MIMETYPES` | bureautique, pdf, texte, images | préfixes de types acceptés |

## Tests

```bash
bin/pytest graph
```
