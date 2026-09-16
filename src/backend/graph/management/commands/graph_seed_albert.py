"""
Seed a user's drive with public documents from the Albert API (DINUM).

    ALBERT_API_KEY=... python manage.py graph_seed_albert user@example.com

Without ``--collection`` it builds the demonstration bank: thirty documents,
half from Légifrance and half from the Ministry of Labour's fiches, written
as text documents, spreadsheets and scanned pages, spread over three folders
the reader owns, edits and reads, and over a year and a half of dates.

Collections (see --list): 139226 Légifrance, 150277 fiches travail-emploi,
150281 fiches service-public, 140029 décisions CNIL, 139999 dossiers
législatifs.
"""

from django.core.management.base import BaseCommand, CommandError

from core import models

from graph.services.albert import AlbertClient, AlbertError
from graph.services.seed import seed_from_albert

# The two corpora of the demonstration bank: the law, and the fiches that
# explain it. They overlap enough for the graph to draw real ties between the
# two ‒ a fiche on working hours and the articles it comes from.
DEMO_COLLECTIONS = [139226, 150277]
DOCUMENTS_PER_COLLECTION = 15


class Command(BaseCommand):
    """Create real files with stored chunks and links from Albert."""

    help = "Seed the graph storage with Albert public documents."

    def add_arguments(self, parser):
        parser.add_argument("email", nargs="?", help="Owner of the seeded files")
        parser.add_argument(
            "--collection", action="append", type=int, default=[], help="Albert collection id"
        )
        parser.add_argument(
            "--documents",
            type=int,
            default=DOCUMENTS_PER_COLLECTION,
            help="Documents per collection",
        )
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
            if not options["email"]:
                raise CommandError("Usage: graph_seed_albert <email> [--collection <id>]")
            user = models.User.objects.filter(email=options["email"]).first()
            if user is None:
                raise CommandError(f"No user with email {options['email']}")
            report = seed_from_albert(
                user,
                client,
                options["collection"] or DEMO_COLLECTIONS,
                documents_per_collection=options["documents"],
            )
        except AlbertError as exc:
            raise CommandError(str(exc)) from exc

        formats = ", ".join(f"{count} {kind}" for kind, count in sorted(report.formats.items()))
        self.stdout.write(
            self.style.SUCCESS(
                f"{report.items} files created ({formats}), {report.skipped} skipped, "
                f"{report.failed} unreadable, {report.chunks} passages, {report.links} links"
            )
        )
