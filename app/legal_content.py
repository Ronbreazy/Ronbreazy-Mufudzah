"""
legal_content.py
-----------------
This is where all the "legal knowledge" lives -- as plain, editable Python
data structures rather than a black-box AI call. That means:

  - It costs nothing to run and needs no API key.
  - Every answer it gives is traceable back to a line of code you can read.
  - You (or a real lawyer) can review, correct, and extend it directly.

IMPORTANT: this is a starting point, not a substitute for legal review.
The document templates and explanations below are intentionally generic.
Before this app is used for real cases, a Zimbabwean legal practitioner
should review and refine the templates, the plain-language dictionary, and
the chat answers below.
"""

import datetime

COURT_NAMES = {
    "magistrates_court": "MAGISTRATES COURT OF ZIMBABWE",
    "high_court": "HIGH COURT OF ZIMBABWE",
    "supreme_court": "SUPREME COURT OF ZIMBABWE",
    "constitutional_court": "CONSTITUTIONAL COURT OF ZIMBABWE",
}

CASE_NO_PREFIX = {
    "magistrates_court": "CIV",
    "high_court": "HC",
    "supreme_court": "SC",
    "constitutional_court": "CCZ",
}

# Which act/rules to point to as "sources_used" for a generated document,
# per court tier. These are the well-known governing rules for each court,
# but always double-check the current citation before relying on it.
COURT_SOURCES = {
    "magistrates_court": ["Magistrates Court Act [Chapter 7:10]", "Magistrates Court (Civil) Rules"],
    "high_court": ["High Court Act [Chapter 7:06]", "High Court Rules, 2021"],
    "supreme_court": ["Supreme Court Act [Chapter 7:13]", "Supreme Court Rules"],
    "constitutional_court": ["Constitution of Zimbabwe Amendment (No. 20) Act 2013", "Constitutional Court Rules, 2016"],
}


def generate_document(document_type, court_tier, case_details):
    """Builds a court document from a template. The templates themselves now
    live in app/documents.py; this name is kept so older code still works.
    Returns { title, content, blocks, sources_used, warnings, court_tier }.
    """
    from app import documents  # imported here: documents.py imports this module
    return documents.generate(document_type, court_tier, case_details)


# ---------------------------------------------------------------------------
# Plain-language simplifier
# ---------------------------------------------------------------------------
# Maps a legal term (matched case-insensitively, whole word) to a short
# plain-language explanation, plus the source that explanation is grounded
# in. Add more entries here to expand the simplifier's vocabulary.

import re

LEGAL_TERMS = {
    "plaintiff": ("person suing", "General civil procedure terminology"),
    "defendant": ("person being sued", "General civil procedure terminology"),
    "affidavit": ("a written statement sworn to be true", "General civil procedure terminology"),
    "judgment": ("the court's final decision", "General civil procedure terminology"),
    "summons": ("an official order to appear in court", "Magistrates Court (Civil) Rules"),
    "petition": ("a formal written request to the court", "General civil procedure terminology"),
    "respondent": ("the person answering/replying to the claim", "General civil procedure terminology"),
    "applicant": ("the person bringing the case", "General civil procedure terminology"),
    "interdict": ("a court order stopping someone from doing something", "High Court Rules, 2021"),
    "ex parte": ("made by, or for the benefit of, one side only, without the other side present", "High Court Rules, 2021"),
    "urgent chamber application": ("a fast-tracked application heard by a judge because waiting for the normal court roll would cause serious harm", "High Court Rules, 2021"),
    "prescription": ("a legal time limit after which a claim can no longer be brought", "Prescription Act [Chapter 8:11]"),
    "locus standi": ("the legal right to bring a case to court", "General civil procedure terminology"),
    "prima facie": ("on first impression / based on the initial evidence", "General civil procedure terminology"),
    "in limine": ("as a preliminary point, before the main matter is argued", "General civil procedure terminology"),
    "mandamus": ("a court order compelling a person or body to carry out a public duty", "General civil procedure terminology"),
    "subpoena": ("an order requiring someone to attend court or produce documents", "General civil procedure terminology"),
    "default judgment": ("a judgment given because one side did not respond or appear", "Magistrates Court (Civil) Rules"),
    "accused": ("the person charged with a crime", "Criminal Procedure and Evidence Act [Chapter 9:07]"),
    "appellant": ("the person who brings an appeal to a higher court", "General civil procedure terminology"),
    "acquitted": ("found not guilty and set free", "Criminal Procedure and Evidence Act [Chapter 9:07]"),
    "convicted": ("found guilty of a crime by the court", "Criminal Procedure and Evidence Act [Chapter 9:07]"),
    "bail": ("release from custody while waiting for trial, usually on conditions", "Criminal Procedure and Evidence Act [Chapter 9:07]"),
    "plea": ("the accused person's answer to the charge: guilty or not guilty", "Criminal Procedure and Evidence Act [Chapter 9:07]"),
    "cross-examination": ("questioning of a witness by the other side", "General civil procedure terminology"),
}


def simplify_text(text):
    """Finds known legal terms in `text` and appends a plain-language gloss
    the first time each one appears. Returns { simplified_text, citations,
    grounded }.
    """
    citations_used = set()

    def make_replacer(term, gloss, source):
        pattern = re.compile(r"\b" + re.escape(term) + r"\b", re.IGNORECASE)

        def _replace(match):
            citations_used.add(source)
            return f"{match.group(0)} [{gloss}]"

        return pattern, _replace

    result = text
    for term, (gloss, source) in LEGAL_TERMS.items():
        pattern = re.compile(r"\b" + re.escape(term) + r"\b(?!\s\[)", re.IGNORECASE)
        if pattern.search(result):
            citations_used.add(source)
            result = pattern.sub(lambda m, g=gloss: f"{m.group(0)} [{g}]", result)

    return {
        "simplified_text": result,
        "citations": sorted(citations_used),
        "grounded": len(citations_used) > 0,
    }


# ---------------------------------------------------------------------------
# Judgment summarizer
# ---------------------------------------------------------------------------
# Unlike simplify_text() above (which annotates individual legal *words* in
# place), this condenses a whole judgment/order down to its most important
# sentences: who the parties/court are, the issue(s) discussed, and -- most
# importantly -- the actual order/outcome at the end. It's a plain,
# rule-based "extractive" summary (it only ever reuses your own sentences,
# never invents new wording), built from three signals:
#
#   1. The opening sentence -- usually names the court/parties/background.
#   2. Sentences containing a known legal term (from LEGAL_TERMS above) --
#      these are usually where the substantive legal reasoning lives.
#   3. Sentences containing an "outcome" phrase (e.g. "it is ordered that",
#      "the application is dismissed") -- this is what most self-actors
#      actually want to know: what did the court decide?

# Phrases that tend to introduce a judgment's actual ruling/order. Feel
# free to add more phrasing you see in real judgments here. Split into two
# tiers: PRIMARY phrases usually state the actual relief granted/refused,
# while SECONDARY ones (costs orders) are part of the outcome but not the
# headline result -- so when picking the single best "outcome" sentence, a
# primary match wins even if a costs sentence happens to come later.
PRIMARY_OUTCOME_PHRASES = [
    "it is ordered", "it is hereby ordered", "is ordered that", "in the result",
    "we hold", "held that", "it is declared", "the court finds", "the court holds",
    "the application is", "the appeal is", "the claim is", "is dismissed",
    "is granted", "is allowed", "is upheld", "is set aside", "interdicted",
    "accordingly", "for these reasons", "for the foregoing reasons", "in conclusion",
]
SECONDARY_OUTCOME_PHRASES = ["costs shall", "costs to be", "with costs"]
OUTCOME_PHRASES = PRIMARY_OUTCOME_PHRASES + SECONDARY_OUTCOME_PHRASES

# A sentence boundary: a '.', '!' or '?' followed by whitespace and then
# either a capital letter or the end of the text. This is a simple
# heuristic (it can be fooled by things like "S. 12" or "Mr. Moyo"), not a
# full grammar parser -- good enough for a first pass, not perfect on
# every judgment.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\"'])")


def _split_sentences(text):
    text = " ".join(text.split())  # collapse newlines/extra whitespace
    if not text:
        return []
    return [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]


def _find_citations(text):
    """Returns the sorted set of sources for every known legal term that
    appears anywhere in `text` (used by both simplify_text's cousin here
    and the summarizer, without doing simplify_text's inline annotation).
    """
    lowered = text.lower()
    citations = set()
    for term, (_gloss, source) in LEGAL_TERMS.items():
        if re.search(r"\b" + re.escape(term) + r"\b", lowered):
            citations.add(source)
    return citations


def summarize_judgment(text, max_key_points=6):
    """Condenses a judgment/order down to its most important sentences.

    Returns:
        summary: the selected sentences joined into a short paragraph
        key_points: the same sentences as a list (handy for bullet display)
        outcome: the best-guess sentence stating the actual ruling/order,
                 or None if no outcome phrase was found
        citations: legal sources grounding the summary (same idea as
                   simplify_text)
        grounded: True if any known legal term was found anywhere in the text
    """
    sentences = _split_sentences(text)
    if not sentences:
        return {"summary": "", "key_points": [], "outcome": None, "citations": [], "grounded": False}

    lowered_sentences = [s.lower() for s in sentences]

    outcome_sentences = [
        s for s, low in zip(sentences, lowered_sentences)
        if any(phrase in low for phrase in OUTCOME_PHRASES)
    ]
    primary_outcome_sentences = [
        s for s, low in zip(sentences, lowered_sentences)
        if any(phrase in low for phrase in PRIMARY_OUTCOME_PHRASES)
    ]
    term_sentences = [
        s for s, low in zip(sentences, lowered_sentences)
        if any(re.search(r"\b" + re.escape(term) + r"\b", low) for term in LEGAL_TERMS)
    ]

    # Build the key points in original reading order: always lead with the
    # opening sentence, then weave in up to a few term/reasoning sentences,
    # then finish with the clearest outcome sentence(s) -- capped overall so
    # this stays a genuine summary rather than a copy of the whole thing.
    key_points = []

    def add(sentence):
        if sentence not in key_points and len(key_points) < max_key_points:
            key_points.append(sentence)

    add(sentences[0])
    for s in term_sentences:
        add(s)
    for s in outcome_sentences:
        add(s)

    # Re-sort the selected sentences back into their original order, since
    # the loops above added "reasoning" sentences before "outcome" ones
    # regardless of where they actually appeared in the text.
    key_points.sort(key=lambda s: sentences.index(s))

    citations = _find_citations(text)

    return {
        "summary": " ".join(key_points),
        "key_points": key_points,
        "outcome": (primary_outcome_sentences[-1] if primary_outcome_sentences
                    else outcome_sentences[-1] if outcome_sentences else None),
        "citations": sorted(citations),
        "grounded": len(citations) > 0,
    }


# ---------------------------------------------------------------------------
# Judgment Summarizer, rule-based version (judgment -> short plain-English summary)
# ---------------------------------------------------------------------------
# This is what runs when no AI provider is switched on. It does the same job
# the AI version does -- a SHORT summary in plain English -- using the two
# rule-based pieces above: summarize_judgment() picks the most important
# sentences and the court's order, then each legal term in them gets a
# plain-language explanation in [brackets] the first time it appears.

def _gloss_once(text, seen_terms, sources):
    """Adds "[plain-English meaning]" after the FIRST appearance of each
    legal term in `text` that hasn't been explained yet (`seen_terms` carries
    over between calls, so a term is explained once across the whole
    summary, not repeated in every sentence). Longer terms are matched first,
    so "default judgment" is explained as a whole instead of just "judgment".
    Matches are found in the ORIGINAL text and inserted afterwards, so an
    explanation never gets explained itself.
    """
    insertions = []
    claimed = []
    for term in sorted(LEGAL_TERMS, key=len, reverse=True):
        if term in seen_terms:
            continue
        gloss, source = LEGAL_TERMS[term]
        for m in re.finditer(r"\b" + re.escape(term) + r"\b", text, re.IGNORECASE):
            if any(start <= m.start() and m.end() <= end for start, end in claimed):
                continue  # inside a longer term we already matched
            if text[m.end():m.end() + 2] == " [":
                continue  # already explained in the source text
            claimed.append((m.start(), m.end()))
            insertions.append((m.end(), gloss))
            seen_terms.add(term)
            sources.add(source)
            break
    for position, gloss in sorted(insertions, reverse=True):
        text = text[:position] + f" [{gloss}]" + text[position:]
    return text


def simplify_judgment(text):
    """The Judgment Summarizer without AI (see app/judgment.py).

    Returns the fields the page shows:
        simplified_text  a short summary of the judgment
        key_points       the main facts and the court's own findings
        outcome          the court's decision as one piece of text
        decision         the same decision as a list (one item per order)
        headline         a one-line result ("Guilty of murder; Sentence: ...")
        case_details     case number, parties, court, judge, date
        cases_cited      other cases the judgment relied on
        legal_terms      legal words in the judgment, to look up in the Dictionary
        citations        Acts named in the judgment (or glossary sources)
        grounded         True if it recognisably is legal text
    """
    from app import judgment

    result = judgment.summarize(text, LEGAL_TERMS)
    glossary_sources = _find_citations(text)
    details = result["case_details"]
    grounded = bool(glossary_sources or result["acts"] or details.get("case_number") or result["decision"])
    if not grounded:
        return {"simplified_text": "", "key_points": [], "outcome": None, "decision": [], "headline": None,
                "case_details": {}, "cases_cited": [], "legal_terms": [], "citations": [], "grounded": False}

    return {
        "simplified_text": result["summary"],
        "key_points": result["key_points"],
        "outcome": " ".join(result["decision"]) or None,
        "decision": result["decision"],
        "headline": result["headline"],
        "case_details": details,
        "cases_cited": result["cases_cited"],
        "legal_terms": result["legal_terms"],
        "citations": result["acts"] or sorted(glossary_sources),
        "grounded": True,
    }


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------
# The chatbot answers IECMS questions ONLY, and ONLY from the files in
# knowledge_base/ (see app/knowledge.py and app/server.py). There are no
# built-in answers here on purpose: if your files don't cover a question, the
# chatbot says so instead of guessing. This is the reply it gives then.

OUT_OF_SCOPE_RESPONSE = (
    "I can only answer questions about IECMS (the Integrated Electronic Case Management "
    "System), and only from the guidance I've been given -- and I couldn't find anything "
    "about that. Try asking again using the words the IECMS guides use (for example "
    "'how do I register an account on IECMS?'), or ask the court registry for help."
)


def chat_reply(message):
    """The reply when the knowledge base has nothing for the question:
    { response, grounded, sources }."""
    return {"response": OUT_OF_SCOPE_RESPONSE, "grounded": False, "sources": []}
