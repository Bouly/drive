# Graphe des fichiers (app `graph`)

Chaîne backend qui relie les fichiers d'un Drive par leur contenu. Six étapes :

| Étape | Rôle | Où |
| --- | --- | --- |
| 1. Extraire | texte brut du fichier (docx, odt, pdf, pptx, xlsx, images OCR…) via Apache Tika | `services/extraction.py` |
| 2. Découper | chunks de ~350 mots, chevauchement 50 | `services/chunking.py` |
| 3. Représenter | un vecteur par chunk, modèle `bge-m3` servi par TEI (prod) ou Ollama (local) | `services/embeddings.py` |
| 4. Stocker | chunks + vecteurs (pgvector), liens, sujets | `models.py` (à venir) |
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
