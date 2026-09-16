"""
What a file says about a subject, and what ties it to each of its neighbours.

The card of a file already shows its neighbours and how close they are. A
number says two files are alike; it never says what about. This reads the
files and answers in words: three sentences on what this one says about the
subject being looked at, and one sentence per neighbour saying what the two
have in common.

The subject is whatever the reader is looking at ‒ a subject of theirs, or
what they typed in the search field. Without one the summary is of the file
plainly, which is what a reader wants when they open a file they do not know.

Everything is read from the passages already stored, so nothing is extracted
again, and every answer is cached against the file's own date: a card opened
twice costs one call, not two. When Albert cannot be reached the card falls
back on what the storage already holds ‒ the passage justifying each link ‒
rather than showing an error where a sentence was promised.
"""

import hashlib
import logging
import re
from concurrent.futures import ThreadPoolExecutor

from django.core.cache import cache

from graph.models import ItemChunk
from graph.services.albert import AlbertClient, AlbertError

logger = logging.getLogger(__name__)

# How many neighbours a card explains. The card lists more than this; past
# half a dozen sentences nobody reads them, and each one is a call.
MAX_NEIGHBOURS = 6
# How much of a file is read for its summary, and for a link's sentence.
SUMMARY_CHARS = 2400
LINK_CHARS = 700
# Room for the answers. A summary is three sentences, a link is one.
SUMMARY_TOKENS = 260
LINK_TOKENS = 70
# Answers are kept a week: a file that has not changed says the same thing.
CACHE_TTL = 7 * 24 * 3600
# Calls run side by side, one per neighbour plus the summary.
MAX_WORKERS = 7
# How long one of them may take. Albert answers a card in a second or two;
# this is only there so a request cannot sit on a worker until gunicorn kills
# it at ninety seconds, which would take the card down with it.
TIMEOUT = 30


def text_of(items, limit):
    """The first passages of each item, as ``{item_id: "title\\ntext"}``."""
    texts = {}
    rows = ItemChunk.objects.filter(item__in=items).order_by("item_id", "index")
    for item_id, text in rows.values_list("item_id", "text"):
        held = texts.get(item_id, "")
        if len(held) < limit:
            texts[item_id] = f"{held} {text}".strip()
    return {item.id: f"{item.title}\n{texts.get(item.id, '')[:limit]}" for item in items}


def about(subject):
    """The subject as it goes into a prompt, or "" when there is none."""
    subject = (subject or "").strip()
    return subject[:120]


def summary_prompt(text, subject):
    """What to ask about one file."""
    if subject:
        return (
            f"Voici un document :\n\n{text}\n\n"
            f"En trois phrases au maximum, dis ce que ce document apporte sur le thème "
            f"« {subject} ». Si le document ne traite pas ce thème, dis-le en une phrase "
            f"et résume-le brièvement. Réponds en français, sans introduction, sans titre "
            f"et sans énumération."
        )
    return (
        f"Voici un document :\n\n{text}\n\n"
        "En trois phrases au maximum, dis de quoi il traite. Réponds en français, sans "
        "introduction, sans titre et sans énumération."
    )


def link_prompt(text, other, subject):
    """What to ask about a pair of files."""
    theme = f" au regard du thème « {subject} »" if subject else ""
    return (
        f"Premier document :\n{text}\n\n"
        f"Second document :\n{other}\n\n"
        f"Dis ce que ces deux documents ont en commun{theme}. Ta réponse est une seule "
        f"phrase courte et rien d'autre : pas d'introduction, pas de titre, pas de "
        f"commentaire sur ta réponse. Commence directement par le point commun."
    )


# What a model adds when it has been told how long to be: "…imposée. (25 mots)".
# Asking it not to does not always take, and it is the last thing a reader
# needs on every line of a card.
COUNTED = re.compile(r"\s*[(\[]\s*\d+\s*(mots?|words?|caract[eè]res?)\s*[)\]]\s*$", re.IGNORECASE)


def clean(answer):
    """The answer without the note the model made about its own length."""
    return COUNTED.sub("", answer.strip()).strip().strip('"«» ')


def ask(prompt, tokens):
    """Albert's answer to one prompt, or "" when it cannot be reached."""
    try:
        return clean(AlbertClient(timeout=TIMEOUT).chat(prompt, max_tokens=tokens))
    except AlbertError as exc:
        logger.warning("Albert could not answer a brief: %s", exc)
        return ""


def cached(key, prompt, tokens):
    """Ask once for a given file and subject, then remember the answer."""
    answer = cache.get(key)
    if answer is not None:
        return answer
    answer = ask(prompt, tokens)
    if answer:
        cache.set(key, answer, timeout=CACHE_TTL)
    return answer


# Bumped whenever the prompts change: answers are kept a week, and a card
# would otherwise keep answering in the old wording until they expire.
PROMPTS = 2


def key_for(kind, subject, *items):
    """
    A cache key for one answer, tied to the files it was read from.

    The dates are in it: a file replaced by a new upload says something else,
    and its old summary must not survive it.
    """
    stamp = "|".join(f"{item.id}@{item.updated_at.isoformat()}" for item in items)
    digest = hashlib.sha256(f"{PROMPTS}|{kind}|{subject}|{stamp}".encode()).hexdigest()[:32]
    return f"graph-brief-{digest}"


def brief(item, neighbours, subject=""):
    """
    The card's words: a summary of ``item``, and a sentence per neighbour.

    Neighbours are answered in the order they are given, which is the order
    the card lists them ‒ closest first. Every call runs beside the others:
    seven answers take what the slowest one takes, not their sum.
    """
    subject = about(subject)
    neighbours = list(neighbours)[:MAX_NEIGHBOURS]
    long_texts = text_of([item, *neighbours], SUMMARY_CHARS)
    short_texts = {one: text[:LINK_CHARS] for one, text in long_texts.items()}

    jobs = [
        (
            key_for("summary", subject, item),
            summary_prompt(long_texts[item.id], subject),
            SUMMARY_TOKENS,
        )
    ]
    for neighbour in neighbours:
        jobs.append(
            (
                key_for("link", subject, item, neighbour),
                link_prompt(short_texts[item.id], short_texts[neighbour.id], subject),
                LINK_TOKENS,
            )
        )

    # Threads only carry HTTP calls: every passage was read above, so no
    # database connection travels into them.
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        answers = list(pool.map(lambda job: cached(*job), jobs))

    return {
        "subject": subject,
        "summary": answers[0],
        "links": [
            {"id": str(neighbour.id), "sentence": answer}
            for neighbour, answer in zip(neighbours, answers[1:], strict=True)
        ],
    }
