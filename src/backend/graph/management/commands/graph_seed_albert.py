"""
Seed a user's graph with public documents from the Albert API (DINUM).

    ALBERT_API_KEY=... python manage.py graph_seed_albert user@example.com \
        --collection 150281 --collection 150277 --documents 30

Collections: 150281 fiches service-public, 150277 fiches travail-emploi,
140029 décisions CNIL, 139999 dossiers législatifs (see --list).
"""

from django.core.management.base import BaseCommand, CommandError

from core import models

from graph.services.albert import AlbertClient, AlbertError
from graph.services.seed import seed_from_albert


class Command(BaseCommand):
    """Create real files with stored chunks and links from Albert."""

    help = "Seed the graph storage with Albert public documents."

    def add_arguments(self, parser):
        parser.add_argument("email", nargs="?", help="Owner of the seeded files")
        parser.add_argument(
            "--collection", action="append", type=int, default=[], help="Albert collection id"
        )
        parser.add_argument("--documents", type=int, default=30, help="Documents per collection")
        parser.add_argument("--list", action="store_true", help="List public collections and exit")

    def handle(self, *args, **options):
        try:
            client = AlbertClient()
            if options["list"]:
                for collection in client.collections():
                    count = collection.get("documents")
                    self.stdout.write(
                        f"{collection['id']:>7}  {collection['name']}  ({count} docs)"
                    )
                return
            if not options["email"] or not options["collection"]:
                raise CommandError(
                    "Usage: graph_seed_albert <email> --collection <id> [--documents N]"
                )
            user = models.User.objects.filter(email=options["email"]).first()
            if user is None:
                raise CommandError(f"No user with email {options['email']}")
            report = seed_from_albert(
                user, client, options["collection"], documents_per_collection=options["documents"]
            )
        except AlbertError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"{report.items} files created ({report.skipped} skipped), "
                f"{report.chunks} chunks, {report.links} links"
            )
        )
