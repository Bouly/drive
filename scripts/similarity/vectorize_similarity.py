"""
Vectorise deux fichiers texte via l'API Albert (etalab) et calcule leur similarite cosinus.

Prerequis :
    pip install openai
    export ALBERT_API_KEY="votre_cle"   # https://albert.api.etalab.gouv.fr

Usage :
    python vectorize_similarity.py fichier1.txt fichier2.txt
"""

import os
import sys
from math import sqrt

from openai import OpenAI

ALBERT_BASE_URL = "https://albert.api.etalab.gouv.fr/v1"
EMBEDDING_MODEL = "openweight-embeddings"


def read_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sqrt(sum(x * x for x in a))
    norm_b = sqrt(sum(y * y for y in b))
    return dot / (norm_a * norm_b)


def main() -> None:
    if len(sys.argv) != 3:
        print(f"Usage: python {sys.argv[0]} <fichier1> <fichier2>")
        sys.exit(1)

    path_a, path_b = sys.argv[1], sys.argv[2]
    text_a, text_b = read_file(path_a), read_file(path_b)

    api_key = os.environ.get("ALBERT_API_KEY")
    if not api_key:
        print("Erreur : la variable d'environnement ALBERT_API_KEY n'est pas definie.")
        sys.exit(1)

    client = OpenAI(base_url=ALBERT_BASE_URL, api_key=api_key)

    response = client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=[text_a, text_b],
        encoding_format="float",
    )

    vector_a = response.data[0].embedding
    vector_b = response.data[1].embedding

    similarity = cosine_similarity(vector_a, vector_b)

    print(f"Fichier 1 : {path_a} ({len(vector_a)} dimensions)")
    print(f"Fichier 2 : {path_b} ({len(vector_b)} dimensions)")
    print(f"Similarite cosinus : {similarity:.4f}")


if __name__ == "__main__":
    main()
