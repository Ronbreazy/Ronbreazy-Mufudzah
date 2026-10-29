"""
dictionary.py
-------------
The Legal Dictionary: type a word or phrase ("affidavit", "ex parte",
"garnishee order") and get its meaning in plain English.

How a look-up works (GET /dictionary?q=affidavit):

  1. The word is tidied up ("  Affidavits! " -> "affidavits") and checked: it
     must look like a word or short phrase, not a whole paragraph.
  2. If it has been looked up before, the saved answer comes straight back
     from the database (the dictionary_cache table). Local models can take
     a while to answer, so every word only ever has to be asked once.
  3. Otherwise the active AI provider (Ollama, or ChatGPT -- whichever
     AI_PROVIDER is set to, see app/config.py) is asked for the meaning, and
     the answer is saved for next time.
  4. If no AI is switched on, or the call fails (Ollama not running, a bad
     response...), the app's own glossary (LEGAL_TERMS in legal_content.py)
     is used instead. If the glossary doesn't have the word either, the
     answer says it wasn't found and suggests similar glossary words.

Every answer says where it came from ("engine"), so the page can show that an
AI-written meaning should be double-checked, while a glossary meaning comes
from the app's own reviewed list.
"""

import difflib
import re

from app import database
from app.legal_content import LEGAL_TERMS

# A look-up must be a word or a short phrase: letters (any language), spaces,
# hyphens, apostrophes and full stops ("ex parte", "mens rea", "s.12", "d'etre").
_ALLOWED = re.compile(r"^[^\W\d_](?:[\w .'\-]*[\w.])?$", re.UNICODE)
MAX_LENGTH = 60
MAX_WORDS = 6


class DictionaryInputError(ValueError):
    """The text typed can't be looked up -- the message says why, in words a
    person can act on."""


def normalise(query):
    """'  Ex   Parte! ' -> 'ex parte'. Raises DictionaryInputError if what's
    left isn't a word or short phrase."""
    text = re.sub(r"\s+", " ", (query or "").replace("’", "'")).strip().strip("?!,;:\"").strip()
    text = text.lower()
    if not text:
        raise DictionaryInputError("Type a word or phrase to look up.")
    if len(text) > MAX_LENGTH or len(text.split()) > MAX_WORDS:
        raise DictionaryInputError(
            "That's too long for the dictionary -- type a single word or short phrase. "
            "To understand a whole judgment, use the Judgment Summarizer.")
    if not _ALLOWED.match(text):
        raise DictionaryInputError("Type a word or phrase using letters only.")
    return text


def _singular(term):
    """'affidavits' -> 'affidavit', 'summonses' -> 'summons'. Only used to
    match against the glossary, so a plural still finds its entry."""
    candidates = [term]
    if term.endswith("es"):
        candidates.append(term[:-2])
    if term.endswith("s"):
        candidates.append(term[:-1])
    return candidates


def glossary_entry(term):
    """The app's own glossary meaning for `term` (or its singular), or None."""
    for candidate in _singular(term):
        if candidate in LEGAL_TERMS:
            meaning, source = LEGAL_TERMS[candidate]
            return {
                "term": candidate,
                "found": True,
                "is_legal_term": True,
                "plain_meaning": meaning[0].upper() + meaning[1:] + ("" if meaning.endswith(".") else "."),
                "legal_meaning": None,
                "example": None,
                "related_terms": [],
                "source": source,
                "engine": "glossary",
            }
    return None


def suggestions(term, limit=5):
    """Glossary words that look like `term`: ones that start with it, contain
    it, or are spelled similarly ('afidavit' -> 'affidavit'). Used for the
    'Did you mean...?' line and for type-ahead in the search box."""
    terms = sorted(LEGAL_TERMS)
    if not term:
        return []
    starts = [t for t in terms if t.startswith(term) and t != term]
    contains = [t for t in terms if term in t and t not in starts and t != term]
    close = [t for t in difflib.get_close_matches(term, terms, n=limit, cutoff=0.75)
             if t not in starts and t not in contains and t != term]
    return (starts + contains + close)[:limit]


def browse_list():
    """Every glossary word with its short meaning, A-Z, for the page to show
    before anything is searched."""
    return [{"term": term, "plain_meaning": meaning, "source": source}
            for term, (meaning, source) in sorted(LEGAL_TERMS.items())]


def not_found(term):
    return {
        "term": term,
        "found": False,
        "is_legal_term": False,
        "plain_meaning": None,
        "legal_meaning": None,
        "example": None,
        "related_terms": [],
        "source": None,
        "engine": "glossary",
    }


def lookup(query, provider=None, provider_name=""):
    """Looks up `query` and returns the meaning (a dict, see not_found() for
    its fields, plus "suggestions" and "cached").

    `provider` is the active AI provider module (or None for no AI), and
    `provider_name` its name ("ollama"/"openai"); server.py passes both.
    Raises DictionaryInputError for input that can't be looked up.
    """
    from app.ai_errors import AIProviderError  # local import keeps this module light to test

    term = normalise(query)

    cached = database.get_dictionary_entry(term)
    if cached:
        result = dict(cached)
        result["cached"] = True
        result["suggestions"] = suggestions(term)
        return result

    result = None
    if provider is not None and hasattr(provider, "call_define"):
        try:
            result = provider.call_define(term)
            result["engine"] = f"ai:{provider_name}"
            # The glossary is the app's reviewed source -- if it knows the word,
            # say so, so the page can show both side by side.
            glossary = glossary_entry(term)
            result["glossary_meaning"] = glossary["plain_meaning"] if glossary else None
            result["source"] = glossary["source"] if glossary else None
            if result["found"]:
                database.save_dictionary_entry(term, result)
        except AIProviderError as e:
            print(f"[{provider_name}] dictionary look-up failed, using the glossary instead: {e}")
            result = None

    if result is None:
        result = glossary_entry(term) or not_found(term)

    result["cached"] = False
    result["suggestions"] = suggestions(term)
    return result


# ---------------------------------------------------------------------------
# Streaming look-up (GET /dictionary/stream): the meaning appears word by word
# as llama3.2 writes it, instead of after the whole answer is finished.
# ---------------------------------------------------------------------------

_LINE_LABELS = {"meaning": "plain_meaning", "in court": "legal_meaning",
                "example": "example", "related": "related_terms"}


def parse_definition_text(text, term):
    """Reads the model's "MEANING: / IN COURT: / EXAMPLE: / RELATED:" lines
    into the same shape as an ordinary look-up."""
    from app.textutil import normalise_definition

    data = {}
    current = None
    for raw in str(text).splitlines():
        line = raw.strip().strip("*").strip()
        m = re.match(r"^(meaning|in court|example|related)\s*(?:words|terms)?\s*:\s*(.*)$", line, re.IGNORECASE)
        if m:
            current = _LINE_LABELS[m.group(1).lower()]
            data[current] = m.group(2).strip()
        elif current and line:
            data[current] = (data[current] + " " + line).strip()
    if "related_terms" in data:
        data["related_terms"] = [w.strip(" .") for w in data["related_terms"].split(",") if w.strip(" .")]
    if (data.get("legal_meaning") or "").strip(" .").lower() in ("none", "n/a", "not a legal term"):
        data["legal_meaning"] = None
    found = bool(data.get("plain_meaning")) and "NOT FOUND" not in str(text).upper()[:20]
    data["found"] = found
    data["is_legal_term"] = bool(data.get("legal_meaning")) or glossary_entry(term) is not None
    return normalise_definition(data, term, "Ollama")


def lookup_stream(query, provider=None, provider_name=""):
    """Yields events for the page:
        {"type": "start", term, glossary_meaning, source}
        {"type": "delta", "text": "..."}      (only while the AI is writing)
        {"type": "done", "result": {...}}    (the same shape as lookup())
    Raises DictionaryInputError before anything is sent if the word can't be looked up."""
    from app.ai_errors import AIProviderError

    term = normalise(query)
    glossary = glossary_entry(term)
    if glossary:
        term = glossary["term"]  # "affidavits" is looked up (and saved) as "affidavit"
    yield {"type": "start", "term": term,
           "glossary_meaning": glossary["plain_meaning"] if glossary else None,
           "source": glossary["source"] if glossary else None}

    cached = database.get_dictionary_entry(term)
    if cached:
        result = dict(cached)
        result.update(cached=True, suggestions=suggestions(term))
        yield {"type": "done", "result": result}
        return

    result = None
    if provider is not None and hasattr(provider, "stream_define"):
        written = []
        try:
            for piece in provider.stream_define(term):
                written.append(piece)
                yield {"type": "delta", "text": piece}
            result = parse_definition_text("".join(written), term)
            result["engine"] = f"ai:{provider_name}"
            result["glossary_meaning"] = glossary["plain_meaning"] if glossary else None
            result["source"] = glossary["source"] if glossary else None
            if result["found"]:
                database.save_dictionary_entry(term, result)
            elif glossary:
                result = None  # the model didn't know it, but our glossary does
        except AIProviderError as e:
            reason = "too slow to answer (timed out)" if "timed out" in str(e) else f"unavailable ({e})"
            print(f"[{provider_name}] the AI was {reason} -- the dictionary used the glossary instead.")
            result = None
    elif provider is not None:
        yield {"type": "done", "result": lookup(query, provider, provider_name)}
        return

    if result is None:
        result = glossary or not_found(term)
    result = dict(result)
    result.update(cached=False, suggestions=suggestions(term))
    yield {"type": "done", "result": result}
