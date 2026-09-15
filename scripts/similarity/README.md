# Similarite de documents par vectorisation

Script de test qui vectorise deux fichiers texte via l'API Albert (etalab)
et calcule leur similarite cosinus.

## Prerequis

```bash
pip install openai
export ALBERT_API_KEY="votre_cle"   # https://albert.api.etalab.gouv.fr
```

## Usage

### Fichiers locaux

```bash
python vectorize_similarity.py fichier1.txt fichier2.txt
```

`test_fichier_1.txt` et `test_fichier_2.txt` sont fournis comme exemples pour
valider rapidement l'installation.

### Documents deja vectorises sur Albert

Au lieu de comparer des fichiers locaux, on peut comparer deux documents deja
uploades dans une collection Albert (`POST /v1/documents`) :

```bash
# lister les documents disponibles
python vectorize_similarity.py --from-albert --list [--collection-id 123]

# comparer deux documents par id (sinon selection interactive)
python vectorize_similarity.py --from-albert --document-ids 111 222
```

Le contenu de chaque document est recupere via `POST /v1/search` (chunks
tries par id) puis revectorise pour le calcul de similarite.
