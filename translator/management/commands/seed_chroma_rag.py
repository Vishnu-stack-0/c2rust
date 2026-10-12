from django.core.management.base import BaseCommand

from translator.chroma_rag import get_collection, retrieve_for_translation, seed_examples


class Command(BaseCommand):
    help = "Reset and check the ChromaDB starter examples."

    def handle(self, *args, **options):
        stored = seed_examples(force=True)
        count = get_collection().count()
        self.stdout.write(self.style.SUCCESS(f"Stored {stored} examples. Collection now has {count}."))

        _, labels, error = retrieve_for_translation("int main() { return 0; }")
        if error:
            self.stdout.write(self.style.ERROR(f"Retrieval error: {error}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"Retrieval works. Sample labels: {labels}"))