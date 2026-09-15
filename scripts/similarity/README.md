# Similarite de documents par vectorisation

Script de test qui vectorise deux fichiers texte via l'API Albert (etalab)
et calcule leur similarite cosinus.

## Prerequis

```bash
pip install openai
export ALBERT_API_KEY="votre_cle"   # https://albert.api.etalab.gouv.fr
```

## Usage

```bash
python vectorize_similarity.py fichier1.txt fichier2.txt
```

`test_fichier_1.txt` et `test_fichier_2.txt` sont fournis comme exemples pour
valider rapidement l'installation.
