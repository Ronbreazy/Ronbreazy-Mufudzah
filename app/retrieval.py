"""
retrieval.py
------------
This is the "Retrieval" half of Retrieval-Augmented Generation (RAG). Its
only job is: given a question or piece of text, find the most relevant
entries already sitting in *your own* data -- the case law library, the
legal terms glossary, and the lawyer directory -- so they can be handed to
ChatGPT as grounding material (see ai_provider.py) instead of letting it
answer purely from its own general training.

Why this is plain keyword overlap, not embeddings/a vector database:
  - Your datasets are small (a growing case-law library, an 18-term
    glossary, ~9 directory entries) -- a full semantic search engine would
    be solving a problem you don't have yet.
  - No extra dependency, no extra API cost, no internet required for the
    retrieval step itself (only the generation step needs ChatGPT).
  - It's easy to read and debug: a "match" here always means "these actual
    words appeared in both", not an opaque similarity score.

If your case-law library grows into the thousands of entries, keyword
overlap will start missing relevant results phrased differently ("thrown
out" vs "dismissed"). At that point, swapping in OpenAI's embeddings API
+ cosine similarity here is a natural upgrade -- the functions below are
the only place that would need to change.
"""

import re
from collections import Counter

from app import database
from app.legal_content import LEGAL_TERMS

# Common English words that don't help tell two pieces of legal text apart.
# Keeping this small and legal-context-specific (rather than a generic
# stopword list) is deliberate -- words like "court" or "order" ARE useful
# signal here even though they'd be stopwords in general text.
_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "to", "of", "in", "on", "at", "by", "for", "with", "and", "or", "but",
    "this", "that", "these", "those", "it", "its", "as", "from", "has",
    "have", "had", "will", "would", "shall", "should", "can", "could",
    "i", "you", "he", "she", "they", "we", "my", "your", "his", "her",
    # question/filler words -- they say what KIND of question it is, not what it's about
    "what", "how", "when", "where", "why", "who", "which", "does", "did",
    "need", "want", "get", "help", "please", "about", "there", "any", "some",
    "tell", "just", "also", "very", "than", "then", "them", "our", "out",
    # more ways of ASKING (not what is being asked about)
    "mean", "means", "meaning", "stand", "stands", "define", "explain", "describe",
    "definition", "use", "used", "using", "know", "make", "much", "many", "let",
}


def _stem(word):
    """Cuts a word down to a rough root so different forms of it match:
    "signing"/"signs"/"signed" -> "sign", "notifications" -> "notification",
    "logging" -> "log", "filing"/"file" -> "fil". Crude on purpose (it isn't
    real grammar) but applied the same way to the question and to your files,
    so the same word always lands on the same root.
    """
    word = word.strip("'")
    if len(word) > 4 and word.endswith("ies"):
        word = word[:-3] + "y"
    elif len(word) > 5 and word.endswith("ing"):
        word = word[:-3]
    elif len(word) > 4 and word.endswith("ed"):
        word = word[:-2]
    elif len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        word = word[:-1]
    if len(word) > 3 and word[-1] == word[-2] and word[-1] not in "aeioulsz":
        word = word[:-1]  # "logg" -> "log", "stopp" -> "stop"
    if len(word) > 3 and word.endswith("e"):
        word = word[:-1]
    return word


def _tokens(text):
    """The meaningful words of `text`, as roots, in order (repeats kept)."""
    words = re.findall(r"[a-z']+", (text or "").lower())
    return [_stem(w) for w in words if w not in _STOPWORDS and len(w) > 2]


def _tokenize(text):
    return set(_tokens(text))


def _overlap_score(query_tokens, doc_tokens):
    """Number of distinct query words that also appear in the document.
    Simple and easy to reason about -- more shared words = more relevant.
    """
    return len(query_tokens & doc_tokens)


def retrieve_case_law(query, k=3):
    """Returns up to k case-law entries most relevant to `query`, each as
    {title, simplified_text, citations}. Only returns entries with at
    least one matching word -- an empty result means "nothing relevant
    found", not "here are some random entries".
    """
    query_tokens = _tokenize(query)
    if not query_tokens:
        return []

    scored = []
    for entry in database.list_case_law():
        doc_tokens = _tokenize(entry["title"] + " " + entry["simplified_text"])
        score = _overlap_score(query_tokens, doc_tokens)
        if score > 0:
            scored.append((score, entry))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [
        {"title": e["title"], "simplified_text": e["simplified_text"], "citations": e["citations"]}
        for _score, e in scored[:k]
    ]


# Everyday ways of saying the same thing. A word on the left also matches the
# words on the right in your knowledge-base files, so "register" finds a guide
# that says "sign up" and "pay" finds "payment". Single words only (a phrase
# would match too loosely). Add your own as you see questions go unanswered.
SYNONYMS = {
    "register": ["registration", "sign"],
    "registration": ["register", "sign"],
    "signup": ["register", "registration", "account"],
    "account": ["register", "registration"],
    "track": ["status", "progress"],
    "status": ["track", "progress"],
    "progress": ["track", "status"],
    "login": ["log", "logging"],
    "logon": ["log", "logging"],
    "signin": ["log", "logging"],
    "forgot": ["forgotten", "recover", "recovering", "reset"],
    "forget": ["forgotten", "recover", "recovering", "reset"],
    "forgotten": ["forgot", "recover", "recovering", "reset"],
    "lost": ["forgotten", "recover", "recovering"],
    "reset": ["recover", "recovering", "forgotten"],
    "pay": ["payment", "payments"],
    "fee": ["payment", "stamp"],
    "cost": ["fee", "payment"],
    "price": ["fee", "payment"],
    "lawyer": ["advocate", "representative"],
    "attorney": ["advocate", "representative"],
    "website": ["address", "url"],
    "link": ["address", "url"],
    "url": ["address", "website"],
    "phone": ["mobile", "device"],
    "smartphone": ["mobile", "device"],
    "cellphone": ["mobile", "device"],
    "upload": ["attach", "attachment"],
    "attach": ["upload"],
    "sue": ["file", "filing"],
}
_SYNONYM_ROOTS = {_stem(word): alternatives for word, alternatives in SYNONYMS.items()}


# Two-word phrases that mean one thing, so "sign in" isn't mistaken for the
# unrelated word "sign" (as in "Signing Documents").
_PHRASES = [
    (re.compile(r"\b(?:sign|log)\s*-?\s*(?:in|on)\b"), "login"),
    (re.compile(r"\bsign\s*-?\s*up\b"), "signup"),
    # Questions made only of "asking" words still say what they are about:
    (re.compile(r"\bwho\s+(?:can|may|is allowed to|should)\s+use\b"), "eligible"),
    (re.compile(r"\b(?:what\s+(?:do|does)\s+\w+\s+need|need(?:ed)?\s+to\s+use|requirements?)\b"), "requirements"),
    (re.compile(r"\b(?:get\s+)?help\b"), "helpdesk"),
    (re.compile(r"\b(?:have|got|must)\s+to\s+use\b|\bmandatory\b|\bcompulsory\b|\bforced\b"), "compulsory"),
]


def _query_groups(query):
    """One group of interchangeable word-roots per meaningful word in the
    question: {"register", "registration", "sign"} for "register". A passage
    matches a group if it contains ANY word of it."""
    groups, seen = [], set()
    text = (query or "").lower()
    for pattern, replacement in _PHRASES:
        text = pattern.sub(replacement, text)
    for word in re.findall(r"[a-z']+", text):
        if word in _STOPWORDS or len(word) <= 2:
            continue
        root = _stem(word)
        group = {root}
        for alternative in _SYNONYM_ROOTS.get(root, ()):
            group |= _tokenize(alternative)
        key = frozenset(group)
        if key not in seen:
            seen.add(key)
            groups.append(group)
    return groups


def retrieve_knowledge(query, k=3, min_overlap=1, prefer=""):
    """Returns up to k knowledge-base entries (see app/knowledge.py) most
    relevant to `query`, best first, each as {title, content, source}.

    `min_overlap` is how many of the question's words an entry must match to
    count (the chatbot uses config.CHAT_MIN_OVERLAP, because a one-word
    coincidence isn't a good enough answer). A word counts as matched if the
    entry has it or one of its SYNONYMS. A question with fewer meaningful
    words than that -- e.g. just "IECMS" -- needs all of them.

    `prefer` is extra words that only influence the ORDER of the results
    (see conversation.search_boost) -- they never make an entry qualify.
    """
    groups = _query_groups(query)
    if not groups:
        return []
    needed = min(min_overlap, len(groups))
    prefer_tokens = _tokenize(prefer)

    entries = database.list_knowledge()

    # In a long document every entry's title starts with the document's own
    # name ("IECMS Getting Started Guide (page 12)"). Those words say nothing
    # about WHICH part answers the question, so words found in the titles of
    # half or more of a file's entries are ignored when matching titles. (A
    # file needs at least 4 entries for this to be worked out.)
    titles_by_file = {}
    for entry in entries:
        titles_by_file.setdefault(entry["file_name"], []).append(_tokenize(entry["title"]))
    common_title_words = {}
    for file_name, title_sets in titles_by_file.items():
        common_title_words[file_name] = set()
        if len(title_sets) >= 4:
            counts = Counter(word for words in title_sets for word in words)
            common_title_words[file_name] = {w for w, n in counts.items() if n >= len(title_sets) / 2}

    scored = []
    for entry in entries:
        title_tokens = _tokenize(entry["title"]) - common_title_words[entry["file_name"]]
        content_tokens = _tokens(entry["content"])
        doc_tokens = title_tokens | set(content_tokens)
        shared = sum(1 for group in groups if group & doc_tokens)
        if shared < needed:
            continue
        # Words in the title count double: a heading that matches the question
        # is a much stronger signal than a passing mention. How often the
        # question's words appear in the text only breaks near-ties, so the
        # passage that is really ABOUT the topic beats one that just mentions it.
        counts = Counter(content_tokens)
        title_shared = sum(1 for group in groups if group & title_tokens)
        mentions = sum(min(sum(counts[t] for t in group), 3) for group in groups)
        score = 2 * (shared + title_shared) + mentions + 4 * len(prefer_tokens & doc_tokens)
        scored.append((score, entry))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [
        {"title": e["title"], "content": e["content"], "source": e["source"]}
        for _score, e in scored[:k]
    ]


def retrieve_glossary_terms(query):
    """Returns the LEGAL_TERMS entries whose term actually appears in
    `query` -- e.g. if the query mentions "affidavit", that definition
    comes back. This reuses the same glossary the rule-based simplifier
    uses, so AI mode and rule-based mode stay grounded in the same source
    of truth.
    """
    lowered = (query or "").lower()
    matches = []
    for term, (gloss, source) in LEGAL_TERMS.items():
        if re.search(r"\b" + re.escape(term) + r"\b", lowered):
            matches.append({"term": term, "definition": gloss, "source": source})
    return matches


def retrieve_directory(query, k=3):
    """Returns up to k directory (lawyer/legal-aid) entries relevant to
    `query`, e.g. a chat question mentioning "domestic violence" should
    surface ZWLA-style organisations whose focus areas match.
    """
    query_tokens = _tokenize(query)
    if not query_tokens:
        return []

    scored = []
    for entry in database.list_directory():
        doc_text = entry["name"] + " " + entry["description"] + " " + " ".join(entry["focus_areas"])
        doc_tokens = _tokenize(doc_text)
        score = _overlap_score(query_tokens, doc_tokens)
        if score > 0:
            scored.append((score, entry))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [
        {"name": e["name"], "type": e["type"], "focus_areas": e["focus_areas"],
         "location": e["location"], "contact": e["contact"], "fee_basis": e["fee_basis"]}
        for _score, e in scored[:k]
    ]


def build_context(query, include_directory=False):
    """Convenience wrapper: gathers everything relevant to `query` across
    all three sources into one dict, ready to hand to ai_provider.py.
    Set include_directory=True for chat (where "who can help me" questions
    are common) -- simplify/summarize don't need it.
    """
    context = {
        "knowledge": retrieve_knowledge(query),
        "case_law": retrieve_case_law(query),
        "glossary": retrieve_glossary_terms(query),
    }
    if include_directory:
        context["directory"] = retrieve_directory(query)
    return context


def format_context_for_prompt(context):
    """Turns the dict from build_context() into a plain-text block to
    paste into a ChatGPT prompt. Returns "" if nothing relevant was found
    -- callers should skip adding an empty context block to the prompt.
    """
    lines = []

    # Knowledge-base excerpts first: they are the most deliberately chosen
    # material, so the model should lean on them most.
    if context.get("knowledge"):
        lines.append("Relevant knowledge base excerpts:")
        for k in context["knowledge"]:
            lines.append(f"- [{k['source']}] {k['title']}: {k['content']}")

    if context.get("glossary"):
        lines.append("Relevant glossary definitions:")
        for g in context["glossary"]:
            lines.append(f"- {g['term']}: {g['definition']} (source: {g['source']})")

    if context.get("case_law"):
        lines.append("\nRelevant entries from the case law library:")
        for c in context["case_law"]:
            lines.append(f"- \"{c['title']}\": {c['simplified_text']}")

    if context.get("directory"):
        lines.append("\nRelevant organisations from the directory:")
        for d in context["directory"]:
            lines.append(f"- {d['name']} ({d['type']}, {d['fee_basis']}): {d['location']}, {d['contact']}")

    return "\n".join(lines)
