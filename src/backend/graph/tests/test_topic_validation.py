"""Subject forms receive actionable validation instead of a database error."""

from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from core import factories

from graph.models import Topic

pytestmark = pytest.mark.django_db
URL = "/api/v1.0/graph/topics/"


@pytest.mark.parametrize("name", ["Budget", "budget", " Budget "])
def test_duplicate_subject_name_is_a_validation_error(name):
    """Whitespace and case variations cannot create another subject."""
    user = factories.UserFactory()
    Topic.objects.create(creator=user, name="Budget")
    client = APIClient()
    client.force_login(user)

    with patch("graph.viewsets.sort_files_into") as sort:
        response = client.post(URL, {"name": name}, format="json")

    assert response.status_code == 400
    assert Topic.objects.filter(creator=user).count() == 1
    sort.assert_not_called()


def test_subject_names_are_private_to_their_owner():
    """A colleague's subject does not reserve the name for everyone."""
    Topic.objects.create(creator=factories.UserFactory(), name="Budget")
    user = factories.UserFactory()
    client = APIClient()
    client.force_login(user)

    with patch("graph.viewsets.sort_files_into"):
        response = client.post(URL, {"name": "Budget"}, format="json")

    assert response.status_code == 201
    assert Topic.objects.filter(creator=user, name="Budget").exists()


def test_renaming_to_another_subject_is_rejected():
    """A failed rename preserves both the existing names and descriptions."""
    user = factories.UserFactory()
    Topic.objects.create(creator=user, name="Budget")
    topic = Topic.objects.create(creator=user, name="Formation", description="Initial")
    client = APIClient()
    client.force_login(user)

    with patch("graph.viewsets.sort_files_into") as sort:
        response = client.patch(
            f"{URL}{topic.pk}/", {"name": "budget", "description": "Changed"}, format="json"
        )

    assert response.status_code == 400
    topic.refresh_from_db()
    assert (topic.name, topic.description) == ("Formation", "Initial")
    sort.assert_not_called()


def test_existing_case_variants_can_still_be_edited():
    """Legacy duplicates do not prevent editing the subject's description."""
    user = factories.UserFactory()
    Topic.objects.create(creator=user, name="budget")
    topic = Topic.objects.create(creator=user, name="Budget")
    client = APIClient()
    client.force_login(user)

    with patch("graph.viewsets.sort_files_into"):
        response = client.patch(
            f"{URL}{topic.pk}/", {"name": "Budget", "description": "Updated"}, format="json"
        )

    assert response.status_code == 200
    topic.refresh_from_db()
    assert topic.description == "Updated"
