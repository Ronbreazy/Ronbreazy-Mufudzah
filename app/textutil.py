"""
textutil.py
-----------
Small helpers shared by several modules, kept in one place so there is only
one copy to read and fix:

  - split_into_chunks(): cuts long text into pieces that fit a model's
    context window, or into knowledge-base entries.
  - normalise_simplified(): checks and tidies the JSON a model sends back
    for the Judgment Summarizer, so a sloppy answer from a small local model
    (a string where a list was asked for, a missing optional field) doesn't
    break the feature.
"""

import re

from app.ai_errors import AIProviderError


def split_into_chunks(text, max_chars):
    """Splits text into pieces of at most max_chars, breaking on paragraphs
    first, then sentences, so nothing is cut in the middle of a thought
    unless a single sentence is itself longer than max_chars.
    """
    pieces = []
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= max_chars:
            pieces.append(paragraph)
            continue
        buf = ""
        for sentence in re.split(r"(?<=[.!?])\s+", paragraph):
            if buf and len(buf) + 1 + len(sentence) > max_chars:
                pieces.append(buf)
                buf = sentence
            else:
                buf = (buf + " " + sentence).strip()
        if buf:
            pieces.append(buf)

    # A single sentence longer than max_chars still has to be cut somewhere.
    cut = []
    for piece in pieces:
        while len(piece) > max_chars:
            cut.append(piece[:max_chars])
            piece = piece[max_chars:]
        cut.append(piece)

    # Pack the small pieces back together, up to max_chars per chunk.
    chunks, current = [], ""
    for piece in cut:
        if current and len(current) + 2 + len(piece) > max_chars:
            chunks.append(current)
            current = piece
        else:
            current = (current + "\n\n" + piece) if current else piece
    if current:
        chunks.append(current)
    return chunks


def as_list(value):
    """Small local models sometimes return a plain string where we asked
    for a list -- wrap it instead of letting list() split it into letters."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    try:
        return list(value)
    except TypeError:
        return [value]


def _as_bool(value):
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1")
    return bool(value)


def normalise_simplified(data, who):
    """Turns the model's JSON into the exact shape the app uses:
        {simplified_text, key_points, outcome, citations, grounded}

    Only `simplified_text` is essential -- if the model leaves out the
    optional extras we fill in empty ones rather than throwing the whole
    answer away (small local models forget fields quite often). `who` is
    the provider name, used only in the error message.
    """
    if not isinstance(data, dict):
        raise AIProviderError(f"{who} didn't return a JSON object.")

    text = str(data.get("simplified_text") or "").strip()
    if not text:
        raise AIProviderError(f"{who} response was missing 'simplified_text'.")

    key_points = [str(p).strip() for p in as_list(data.get("key_points")) if str(p).strip()]

    outcome = data.get("outcome")
    outcome = str(outcome).strip() if outcome else None
    if outcome and outcome.lower() in ("null", "none", "n/a"):
        outcome = None

    citations = []
    for c in as_list(data.get("citations")):
        c = str(c).strip()
        if c and c not in citations:
            citations.append(c)

    decision = [str(d).strip() for d in as_list(data.get("decision")) if str(d).strip()]
    if not outcome and decision:
        outcome = " ".join(decision)
    headline = data.get("headline")
    headline = str(headline).strip() if headline else None
    if headline and headline.lower() in ("null", "none", "n/a"):
        headline = None

    return {
        "simplified_text": text,
        "key_points": key_points,
        "outcome": outcome,
        "decision": decision or ([outcome] if outcome else []),
        "headline": headline,
        "citations": citations,
        "grounded": _as_bool(data.get("grounded", False)),
    }


def normalise_definition(data, term, who):
    """Turns the model's JSON for a Legal Dictionary look-up into the exact
    shape the app uses:
        {term, found, is_legal_term, plain_meaning, legal_meaning, example, related_terms}

    `found` is false when the model says it doesn't know the word (or gives
    no meaning at all) -- the page then says "not found" instead of showing
    something made up. Optional fields left out by a small model become
    empty rather than throwing the whole answer away.
    """
    if not isinstance(data, dict):
        raise AIProviderError(f"{who} didn't return a JSON object.")

    def text(key):
        value = data.get(key)
        value = str(value).strip() if value is not None else ""
        return None if value.lower() in ("", "null", "none", "n/a") else value

    plain = text("plain_meaning")
    found = _as_bool(data.get("found", bool(plain))) and bool(plain)

    related = []
    for r in as_list(data.get("related_terms")):
        r = str(r).strip()
        if r and r.lower() != term.lower() and r.lower() not in (x.lower() for x in related):
            related.append(r)

    return {
        "term": term,
        "found": found,
        "is_legal_term": found and _as_bool(data.get("is_legal_term", False)),
        "plain_meaning": plain if found else None,
        "legal_meaning": text("legal_meaning") if found else None,
        "example": text("example") if found else None,
        "related_terms": related[:6] if found else [],
    }
