"""Tests for the card's words: a summary on a subject, a sentence per link."""

from unittest import mock

import pytest

from core import factories, models

from graph.services import storage
from graph.services.albert import AlbertError
from graph.services.brief import brief
from graph.services.chunking import Chunk, hash_text

pytestmark = pytest.mark.django_db


def make_file(user, title, passages):
    """A readable file carrying passages, so the brief has something to read."""
    item = factories.ItemFactory(
        title=title,
        type=models.ItemTypeChoices.FILE,
        filename=f"{title}.odt",
        mimetype="application/vnd.oasis.opendocument.text",
        users=[(user, models.RoleChoices.OWNER)],
        update_upload_state=models.ItemUploadStateChoices.READY,
    )
    storage.save_chunks(
        item,
        [
            Chunk(index=i, text=text, text_hash=hash_text(text), embedding=[0.0] * 1024)
            for i, text in enumerate(passages)
        ],
    )
    return item


def test_brief_summarises_the_file_and_every_link():
    """One answer for the file, one per neighbour, in the order asked."""
    user = factories.UserFactory()
    item = make_file(user, "Fiche télétravail", ["Le télétravail est volontaire."])
    close = make_file(user, "Accord cadre", ["L'accord fixe trois jours par semaine."])
    other = make_file(user, "Note de frais", ["Les frais sont remboursés au réel."])

    def answer_of(prompt, **_kwargs):
        # The calls run side by side, so what the answer is made of is the
        # prompt, never the order they happen to arrive in.
        if "Second document" not in prompt:
            return "Un résumé."
        return "Point commun avec " + ("l'accord." if "Accord cadre" in prompt else "la note.")

    with mock.patch("graph.services.brief.AlbertClient") as client:
        client.return_value.chat.side_effect = answer_of
        answer = brief(item, [close, other], subject="télétravail")

    assert answer["subject"] == "télétravail"
    assert answer["summary"] == "Un résumé."
    # Each sentence lands on the neighbour it was asked about, whichever call
    # answered first, and in the order the card lists them.
    assert answer["links"] == [
        {"id": str(close.id), "sentence": "Point commun avec l'accord."},
        {"id": str(other.id), "sentence": "Point commun avec la note."},
    ]
    # The subject, the passages and both titles reached the model.
    prompts = " ".join(call.args[0] for call in client.return_value.chat.call_args_list)
    assert "télétravail" in prompts
    assert "Le télétravail est volontaire." in prompts
    assert "L'accord fixe trois jours par semaine." in prompts


def test_brief_asks_once_per_file_and_subject():
    """The same card opened twice costs one call, not two."""
    user = factories.UserFactory()
    item = make_file(user, "Fiche", ["Un texte."])
    close = make_file(user, "Voisine", ["Un autre texte."])

    with mock.patch("graph.services.brief.AlbertClient") as client:
        client.return_value.chat.return_value = "Une phrase."
        first = brief(item, [close], subject="congés")
        assert client.return_value.chat.call_count == 2
        again = brief(item, [close], subject="congés")
        assert client.return_value.chat.call_count == 2
        # ...and another subject is another question.
        brief(item, [close], subject="salaire")
        assert client.return_value.chat.call_count == 4

    assert first == again


def test_brief_without_a_subject_summarises_the_file_plainly():
    """No subject: the card still says what the file is about."""
    user = factories.UserFactory()
    item = make_file(user, "Fiche", ["Le préavis est d'un mois."])

    with mock.patch("graph.services.brief.AlbertClient") as client:
        client.return_value.chat.return_value = "Un résumé."
        answer = brief(item, [])

    assert answer == {"subject": "", "summary": "Un résumé.", "links": []}
    assert "thème" not in client.return_value.chat.call_args.args[0]


def test_brief_survives_albert_being_down():
    """Albert unreachable: the card gets no sentence, never an error."""
    user = factories.UserFactory()
    item = make_file(user, "Fiche", ["Un texte."])
    close = make_file(user, "Voisine", ["Un autre texte."])

    with mock.patch("graph.services.brief.AlbertClient") as client:
        client.return_value.chat.side_effect = AlbertError("401")
        answer = brief(item, [close], subject="congés")

    assert answer["summary"] == ""
    assert answer["links"] == [{"id": str(close.id), "sentence": ""}]


def test_a_model_note_about_its_own_length_is_dropped():
    """"…imposée. (25 mots)" is not something a card should carry."""
    user = factories.UserFactory()
    item = make_file(user, "Fiche", ["Un texte."])
    close = make_file(user, "Voisine", ["Un autre texte."])

    with mock.patch("graph.services.brief.AlbertClient") as client:
        client.return_value.chat.return_value = "Les deux portent sur le préavis. (25 mots)"
        answer = brief(item, [close], subject="préavis")

    assert answer["links"][0]["sentence"] == "Les deux portent sur le préavis."


def test_brief_endpoint_only_reads_files_the_user_can_open(client):
    """A neighbour the reader cannot open is dropped, not summarised."""
    user = factories.UserFactory()
    client.force_login(user)
    item = make_file(user, "Fiche", ["Un texte."])
    mine = make_file(user, "Voisine", ["Un autre texte."])
    theirs = make_file(factories.UserFactory(), "Cachée", ["Rien à voir."])

    with mock.patch("graph.services.brief.AlbertClient") as albert:
        albert.return_value.chat.return_value = "Une phrase."
        response = client.get(
            f"/api/v1.0/graph/files/{item.id}/brief/",
            {"subject": "congés", "with": f"{theirs.id},{mine.id}"},
        )
        prompts = " ".join(call.args[0] for call in albert.return_value.chat.call_args_list)

    assert response.status_code == 200
    assert [link["id"] for link in response.json()["links"]] == [str(mine.id)]
    assert "Rien à voir." not in prompts

    # ...and a file the reader cannot open has no card at all.
    assert client.get(f"/api/v1.0/graph/files/{theirs.id}/brief/").status_code == 404
