"""
Local RAG over starter C-to-Rust examples, backed by ChromaDB.

Uses a simple local embedding (no model download), so retrieval works
offline and never depends on a model that might fail to load.
"""

import logging
import re
from typing import List, Optional, Tuple

import chromadb
from django.conf import settings

logger = logging.getLogger(__name__)

# New name on purpose: an older collection may store vectors of a
# different size, which makes every query fail. A new name avoids that.
COLLECTION_NAME = "c_rust_examples_v2"
EMBEDDING_DIM = 128

NO_EXAMPLES_TEXT = (
    "No similar examples found in the knowledge base. "
    "Translate using your own knowledge."
)


# ---------------------------------------------------------
# Embedding
# ---------------------------------------------------------

def _normalise_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def _simple_embed(text: str) -> List[float]:
    """
    Fixed-size character-trigram hash vector of length EMBEDDING_DIM.
    Always returns exactly EMBEDDING_DIM values.
    """
    text = _normalise_text(text)
    vector = [0.0] * EMBEDDING_DIM

    if not text:
        return vector

    for i in range(len(text) - 2):
        value = 0
        for character in text[i:i + 3]:
            value = (value * 31 + ord(character)) % 100003
        vector[value % EMBEDDING_DIM] += 1.0

    total = sum(vector)
    if total > 0:
        vector = [v / total for v in vector]

    return vector


# ---------------------------------------------------------
# ChromaDB
# ---------------------------------------------------------

def get_collection():
    client = chromadb.PersistentClient(
        path=str(settings.BASE_DIR / "chroma_db"),
    )
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def _make_label(document: str) -> str:
    """
    Short, readable description of an example.
    Uses the NOTES line, which says what the example teaches.
    """
    lines = [l.strip() for l in document.strip().splitlines() if l.strip()]

    for index, line in enumerate(lines):
        if line.upper().startswith("NOTES:") and index + 1 < len(lines):
            return lines[index + 1]

    for line in lines:
        if not line.upper().startswith(("C CODE:", "RUST CODE:")):
            return line

    return lines[0] if lines else "example"


# ---------------------------------------------------------
# Seed data
# ---------------------------------------------------------

SEED_DOCUMENTS = [
    """
C CODE:
#include <stdio.h>

int main() {
    int i;
    for (i = 1; i <= 5; i++) {
        printf("%d ", i);
    }
    return 0;
}

RUST CODE:
fn main() {
    for i in 1..=5 {
        print!("{} ", i);
    }
}

NOTES:
Use a Rust range such as 1..=5 for an inclusive C loop.
Use print! instead of println! when C uses printf without a newline.
""",
    """
C CODE:
#include <stdio.h>

int main() {
    int a = 10;
    int b = 20;
    int sum = a + b;

    printf("%d\\n", sum);
    return 0;
}

RUST CODE:
fn main() {
    let a: i32 = 10;
    let b: i32 = 20;
    let sum = a + b;

    println!("{}", sum);
}

NOTES:
Use let for variable declarations.
Use println! for printf with a newline.
""",
    """
C CODE:
#include <stdio.h>

int main() {
    int number;

    scanf("%d", &number);

    if (number > 0) {
        printf("Positive\\n");
    } else if (number < 0) {
        printf("Negative\\n");
    } else {
        printf("Zero\\n");
    }

    return 0;
}

RUST CODE:
use std::io;

fn main() {
    let mut input = String::new();

    io::stdin()
        .read_line(&mut input)
        .expect("Failed to read input");

    let number: i32 = input
        .trim()
        .parse()
        .expect("Invalid number");

    if number > 0 {
        println!("Positive");
    } else if number < 0 {
        println!("Negative");
    } else {
        println!("Zero");
    }
}

NOTES:
Use std::io::stdin().read_line() for scanf.
Parse input using trim().parse().
""",
    """
C CODE:
#include <stdio.h>

int main() {
    int numbers[5] = {1, 2, 3, 4, 5};
    int sum = 0;

    for (int i = 0; i < 5; i++) {
        sum += numbers[i];
    }

    printf("%d\\n", sum);
    return 0;
}

RUST CODE:
fn main() {
    let numbers = [1, 2, 3, 4, 5];
    let mut sum = 0;

    for number in numbers {
        sum += number;
    }

    println!("{}", sum);
}

NOTES:
Use Rust arrays and for-in loops.
Use mut for variables that change.
""",
]


def seed_examples(force: bool = False) -> int:
    """
    Store the starter examples. Uses fixed IDs with upsert, so running
    it again never creates duplicates. Returns the number stored.

    With force=False it only does work when the collection is missing
    examples, which keeps each translation request fast.
    """
    collection = get_collection()

    if not force and collection.count() >= len(SEED_DOCUMENTS):
        return 0

    documents = [doc.strip() for doc in SEED_DOCUMENTS]
    collection.upsert(
        ids=[f"seed_{i}" for i in range(len(documents))],
        documents=documents,
        embeddings=[_simple_embed(doc) for doc in documents],
        metadatas=[{"label": _make_label(doc)} for doc in documents],
    )
    return len(documents)


# ---------------------------------------------------------
# Search
# ---------------------------------------------------------

def search_examples(c_code: str, top_k: int = 2) -> List[Tuple[str, str]]:
    """
    Return up to top_k (document, label) pairs, most similar first.
    Raises on real errors so the caller can report them.
    """
    collection = get_collection()
    total = collection.count()

    if total == 0:
        return []

    result = collection.query(
        query_embeddings=[_simple_embed(c_code)],
        n_results=min(top_k, total),
        include=["documents", "metadatas"],
    )

    documents = result["documents"][0]
    metadatas = result["metadatas"][0]

    pairs = []
    for document, metadata in zip(documents, metadatas):
        label = (metadata or {}).get("label") or _make_label(document)
        pairs.append((document, label))
    return pairs


# ---------------------------------------------------------
# Public function for the translation view
# ---------------------------------------------------------

def retrieve_for_translation(
    c_code: str,
    top_k: int = 2,
) -> Tuple[str, List[str], Optional[str]]:
    """
    Returns (context_text, labels, error_message).

    error_message is None when retrieval worked, even if no examples
    matched. When something failed, it contains the real reason.
    """
    try:
        seed_examples()
        pairs = search_examples(c_code, top_k=top_k)
    except Exception as exc:
        logger.exception("RAG retrieval failed")
        return NO_EXAMPLES_TEXT, [], f"{type(exc).__name__}: {exc}"

    if not pairs:
        return NO_EXAMPLES_TEXT, [], None

    parts = []
    labels = []
    for index, (document, label) in enumerate(pairs, start=1):
        parts.append(f"--- Retrieved knowledge {index} ---\n{document}")
        labels.append(label)

    return "\n\n".join(parts), labels, None