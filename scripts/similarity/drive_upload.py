"""
Upload d'un fichier dans "My files" d'une instance Drive locale (dev),
via le flow OIDC standard (Keycloak) rejoue en script.

Ne fonctionne que sur une instance de developpement locale, avec les
identifiants de test documentes par le projet (drive/drive).

Sequence (identique a celle utilisee par le frontend React) :
  1. GET /api/v1.0/authenticate/ : Django stocke l'etat OIDC dans sa session
     et redirige vers le formulaire de connexion Keycloak
  2. soumission du formulaire de connexion Keycloak
  3. Keycloak redirige vers /api/v1.0/callback/ avec un code ; Django verifie
     l'etat, echange le code et ouvre une session (cookie drive_sessionid + csrftoken)
  4. POST /api/v1.0/items/ (cree un item "file" a la racine = "My files")
  5. PUT du contenu sur l'URL presignee (policy) renvoyee par l'etape 4
  6. POST /api/v1.0/items/{id}/upload-ended/ pour finaliser

Note : Keycloak (en dev) pose ses cookies de session sur le domaine
"localhost.local" (configuration de l'instance), different de "localhost".
Comme le formulaire de connexion est sur ce meme domaine cote Keycloak, on
transmet les cookies explicitement (dict) plutot que de compter sur le
matching automatique par domaine de `requests`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import requests

DRIVE_BASE_URL = "http://localhost:8071"

FORM_ACTION_RE = re.compile(r'<form[^>]+id="kc-form-login"[^>]+action="([^"]+)"', re.IGNORECASE)


@dataclass
class DriveSession:
    session: requests.Session

    @property
    def csrftoken(self) -> str:
        for cookie in self.session.cookies:
            if cookie.name == "csrftoken":
                return cookie.value
        raise RuntimeError("csrftoken absent de la session (login non effectue ?).")


def login(username: str, password: str) -> DriveSession:
    """Rejoue le login OIDC (Keycloak) et renvoie une session Drive authentifiee."""
    session = requests.Session()

    login_page = session.get(f"{DRIVE_BASE_URL}/api/v1.0/authenticate/")
    login_page.raise_for_status()

    match = FORM_ACTION_RE.search(login_page.text)
    if not match:
        raise RuntimeError("Impossible de trouver le formulaire de connexion Keycloak.")
    form_action = match.group(1).replace("&amp;", "&")

    keycloak_cookies = {c.name: c.value for c in login_page.cookies}
    form_response = requests.post(
        form_action,
        data={"username": username, "password": password, "credentialId": ""},
        cookies=keycloak_cookies,
        allow_redirects=False,
    )

    if form_response.status_code != 302:
        raise RuntimeError(
            "Echec de connexion Drive : identifiants refuses par Keycloak."
        )

    callback = session.get(form_response.headers["Location"])
    callback.raise_for_status()

    if "drive_sessionid" not in session.cookies:
        raise RuntimeError(
            "Echec de connexion Drive : session non etablie apres le callback OIDC."
        )

    return DriveSession(session=session)


def upload_file(drive: DriveSession, filename: str, content: str, mimetype: str = "text/plain") -> str:
    """Cree un fichier a la racine ('My files') et y uploade `content`. Renvoie l'id de l'item."""
    create = drive.session.post(
        f"{DRIVE_BASE_URL}/api/v1.0/items/",
        json={"type": "file", "filename": filename},
        headers={"X-CSRFToken": drive.csrftoken},
    )
    create.raise_for_status()
    item = create.json()

    put = requests.put(
        item["policy"],
        data=content.encode("utf-8"),
        headers={"Content-Type": mimetype, "x-amz-acl": "private"},
    )
    put.raise_for_status()

    finalize = drive.session.post(
        f"{DRIVE_BASE_URL}/api/v1.0/items/{item['id']}/upload-ended/",
        json={},
        headers={"X-CSRFToken": drive.csrftoken},
    )
    finalize.raise_for_status()

    return item["id"]
