"""Urls of the graph app."""

from django.conf import settings
from django.urls import include, path

from rest_framework.routers import DefaultRouter

from graph.api import GraphView
from graph.viewsets import TopicViewSet

router = DefaultRouter()
router.register("topics", TopicViewSet, basename="graph-topic")

urlpatterns = [
    path(f"api/{settings.API_VERSION}/graph/", GraphView.as_view(), name="graph"),
    path(f"api/{settings.API_VERSION}/graph/", include(router.urls)),
]
