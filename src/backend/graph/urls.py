"""Urls of the graph app."""

from django.conf import settings
from django.urls import path

from graph.api import GraphView

urlpatterns = [
    path(f"api/{settings.API_VERSION}/graph/", GraphView.as_view(), name="graph"),
]
