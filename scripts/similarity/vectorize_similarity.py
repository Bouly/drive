"""
Vectorise deux documents et calcule leur similarite cosinus via l'API Albert (etalab).

Deux sources sont possibles :
  - des fichiers texte locaux (mode par defaut) ;
  - des documents deja uploades dans une collection Albert (--from-albert),
    dont le contenu est recupere via /v1/search puis revectorise.

Prerequis :
    pip install openai requests
    export ALBERT_API_KEY="votre_cle"   # https://albert.api.etalab.gouv.fr

Usage :
    python vectorize_similarity.py fichier1.txt fichier2.txt
    python vectorize_similarity.py --from-albert --list [--collection-id 123]
    python vectorize_similarity.py --from-albert --document-ids 111 222
"""

import argparse
import os
import sys
from math import sqrt

import requests
from openai import OpenAI

ALBERT_BASE_URL = "https://albert.api.etalab.gouv.fr/v1"
EMBEDDING_MODEL = "openweight-embeddings"
# Le modele d'embeddings refuse plus de 8192 tokens ; on tronque par securite
# (approximation grossiere : ~4 caracteres par token pour du francais).
MAX_INPUT_CHARS = 24000


def get_api_key() -> str:
    api_key = os.environ.get("ALBERT_API_KEY")
    if not api_key:
        print("Erreur : la variable d'environnement ALBERT_API_KEY n'est pas definie.")
        sys.exit(1)
    return api_key


def read_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def fetch_albert_documents(api_key: str, collection_id: int | None = None, limit: int = 20) -> list[dict]:
    """Liste les documents deja uploades sur Albert (GET /v1/documents)."""
    params = {"limit": limit}
    if collection_id is not None:
        params["collection_id"] = collection_id

    response = requests.get(
        url=f"{ALBERT_BASE_URL}/documents",
        headers={"Authorization": f"Bearer {api_key}"},
        params=params,
    )
    response.raise_for_status()
    return response.json()["data"]


def fetch_albert_document_content(api_key: str, document_id: int, query: str, limit: int = 100) -> str:
    """Recupere le texte d'un document Albert en rassemblant ses chunks (POST /v1/search).

    La recherche "lexical" sans query renvoie une erreur 500 cote serveur Albert ;
    on utilise donc une recherche semantique avec le nom du document comme requete,
    ce qui suffit a remonter l'essentiel de ses chunks pour un document de taille modeste.
    """
    response = requests.post(
        url=f"{ALBERT_BASE_URL}/search",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"document_ids": [document_id], "method": "semantic", "query": query, "limit": limit},
    )
    response.raise_for_status()
    results = response.json()["data"]
    results.sort(key=lambda r: r["chunk"]["id"])
    return "\n".join(r["chunk"]["content"] for r in results)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sqrt(sum(x * x for x in a))
    norm_b = sqrt(sum(y * y for y in b))
    return dot / (norm_a * norm_b)


def compare(label_a: str, text_a: str, label_b: str, text_b: str, api_key: str) -> None:
    client = OpenAI(base_url=ALBERT_BASE_URL, api_key=api_key)

    response = client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=[text_a[:MAX_INPUT_CHARS], text_b[:MAX_INPUT_CHARS]],
        encoding_format="float",
    )

    vector_a = response.data[0].embedding
    vector_b = response.data[1].embedding
    similarity = cosine_similarity(vector_a, vector_b)

    print(f"Document 1 : {label_a} ({len(vector_a)} dimensions)")
    print(f"Document 2 : {label_b} ({len(vector_b)} dimensions)")
    print(f"Similarite cosinus : {similarity:.4f}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="*", help="Deux fichiers texte locaux a comparer")
    parser.add_argument(
        "--from-albert",
        action="store_true",
        help="Comparer deux documents deja vectorises dans Albert au lieu de fichiers locaux",
    )
    parser.add_argument("--collection-id", type=int, default=None, help="Filtrer la liste par collection")
    parser.add_argument("--list", action="store_true", help="Lister les documents disponibles puis quitter")
    parser.add_argument(
        "--document-ids",
        type=int,
        nargs=2,
        metavar=("ID1", "ID2"),
        help="Ids des deux documents Albert a comparer (sinon selection interactive)",
    )
    return parser


def run_albert_mode(args: argparse.Namespace, api_key: str) -> None:
    documents = fetch_albert_documents(api_key, collection_id=args.collection_id)
    if not documents:
        print("Aucun document trouve sur ce compte Albert.")
        sys.exit(1)

    if args.list or not args.document_ids:
        print("Documents disponibles :")
        for doc in documents:
            print(f"  [{doc['id']}] {doc['name']} (collection {doc['collection_id']}, {doc['chunks']} chunks)")
        if args.list:
            return

    if args.document_ids:
        id_a, id_b = args.document_ids
    else:
        id_a = int(input("Id du document 1 : "))
        id_b = int(input("Id du document 2 : "))

    names = {doc["id"]: doc["name"] for doc in documents}
    text_a = fetch_albert_document_content(api_key, id_a, query=names.get(id_a, str(id_a)))
    text_b = fetch_albert_document_content(api_key, id_b, query=names.get(id_b, str(id_b)))

    compare(names.get(id_a, str(id_a)), text_a, names.get(id_b, str(id_b)), text_b, api_key)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    api_key = get_api_key()

    if args.from_albert:
        run_albert_mode(args, api_key)
        return

    if len(args.files) != 2:
        parser.error("fournissez deux fichiers, ou utilisez --from-albert")

    path_a, path_b = args.files
    compare(path_a, read_file(path_a), path_b, read_file(path_b), api_key)


if __name__ == "__main__":
    main()
