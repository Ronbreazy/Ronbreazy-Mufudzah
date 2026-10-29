"""
ai_provider.py
--------------
This is the ONLY file that talks to OpenAI's API. Everything else in the
app calls the functions at the bottom (call_simplify, call_chat, call_define)
and doesn't need to know or care whether ChatGPT is actually configured --
if it isn't, or if anything goes wrong, this module raises AIProviderError
and the caller (see server.py) falls back to the offline rule-based logic
in app/legal_content.py.

Why isolate it like this:
  - The app still runs with zero external dependencies and zero cost by
    default -- this file is only ever imported and used if you've set
    OPENAI_API_KEY (see app/config.py).
  - If the `openai` package isn't installed at all, importing this file
    still succeeds (the import is wrapped in try/except below) -- you'll
    just never be able to turn AI mode on, which is fine.
  - A network hiccup or an expired key degrades gracefully to the
    rule-based version instead of returning an error to the user.

To turn this on:
    pip install --upgrade openai
    export OPENAI_API_KEY="sk-..."
    python3 run.py
"""

import json
import re

from app import config
from app.ai_errors import AIProviderError
from app.textutil import normalise_simplified, normalise_definition

try:
    from openai import OpenAI
    _OPENAI_PACKAGE_INSTALLED = True
except ImportError:
    _OPENAI_PACKAGE_INSTALLED = False


def is_configured():
    """True if AI mode is available to use right now (package installed
    AND an API key is set). server.py checks this before even attempting
    an AI call.
    """
    return _OPENAI_PACKAGE_INSTALLED and bool(config.OPENAI_API_KEY)


_client = None


def _get_client():
    global _client
    if _client is None:
        _client = OpenAI(api_key=config.OPENAI_API_KEY)
    return _client


def _extract_json(raw_text):
    """ChatGPT is asked to respond with pure JSON, but models sometimes
    wrap it in a ```json ... ``` markdown fence anyway -- strip that off
    before parsing, and raise a clear error if it still isn't valid JSON.
    """
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise AIProviderError(f"ChatGPT response wasn't valid JSON: {e}")


def _chat_json(system_prompt, user_text):
    """Sends one request to the Chat Completions endpoint and returns the
    parsed JSON reply. Wraps every failure mode (no package, no key,
    network error, bad response) into a single AIProviderError so callers
    only need one except clause.
    """
    if not _OPENAI_PACKAGE_INSTALLED:
        raise AIProviderError("The 'openai' package isn't installed (pip install openai).")
    if not config.OPENAI_API_KEY:
        raise AIProviderError("OPENAI_API_KEY is not set.")

    try:
        response = _get_client().chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
            temperature=0.2,  # low temperature: favour consistent, literal output over creative wording
        )
        raw_text = response.choices[0].message.content
    except Exception as e:  # noqa: BLE001 - any SDK/network error becomes an AIProviderError
        raise AIProviderError(f"OpenAI request failed: {e}")

    return _extract_json(raw_text)


# ---------------------------------------------------------------------------
# Judgment Summarizer (judgment -> short plain-English summary)
# ---------------------------------------------------------------------------

_SIMPLIFY_SYSTEM_PROMPT = """You are a judgment summarizer for self-represented litigants in Zimbabwe.
Read the judgment and summarise it for someone with no legal training.
Rules:
- Use plain, everyday English and short sentences. Keep names, dates, amounts and orders accurate.
- Do not add facts, opinions or advice that are not in the text.
- Judgments quote OTHER cases ("In S v Banda it was held that ..."). Those are the law the court applied,
  NOT this court's decision. Never put them in "outcome" or "decision".
- "decision" must be what THIS court decided at the end of the judgment: the verdict and sentence in a
  criminal case, or each numbered order in a civil case (e.g. "The application is dismissed with costs").
- "headline" is one short plain sentence with the result, e.g. "Guilty of murder and sentenced to 20 years in prison."
Respond with ONLY a JSON object, nothing else, in exactly this shape:
{"simplified_text": "a short plain-English summary: who the parties are, what the case was about, what the court found, and the result (3 to 6 sentences)",
 "key_points": ["the main facts and the court's own findings, one short plain sentence each"],
 "decision": ["what this court decided, one item per order or per verdict/sentence"],
 "headline": "one short sentence with the result",
 "outcome": "the decision written as one or two plain sentences",
 "citations": ["any Act or rule named in the text"],
 "grounded": true or false}
"grounded" is true if the text contains recognisable legal content."""


def call_simplify(text):
    """OpenAI models take a much larger input than a small local model, so a
    whole judgment goes in one request (no chunking needed here)."""
    return normalise_simplified(_chat_json(_SIMPLIFY_SYSTEM_PROMPT, text), "ChatGPT")


# ---------------------------------------------------------------------------
# Chat (retrieval-augmented -- see app/retrieval.py)
# ---------------------------------------------------------------------------

_CHAT_SYSTEM_PROMPT = """You are the IECMS help assistant for self-represented litigants in Zimbabwe. IECMS is the Integrated Electronic Case Management System used by the courts.

You will be given the user's question and reference material taken from this app's IECMS knowledge base.

Rules:
- Answer ONLY from the reference material. Do not use any other knowledge, even if you are sure of it.
- If the reference material does not answer the question, or the question is not about IECMS, set "grounded" to false and say you can only answer IECMS questions from the knowledge base.
- Never invent steps, fees, deadlines, forms, Acts or rules that are not in the reference material.
- Keep the answer short and in plain English.

Respond with ONLY a single JSON object (no markdown, no commentary) matching
exactly this shape:
{
  "response": "<your plain-English answer, a few sentences at most>",
  "grounded": true or false (true only if the reference material answers the question),
  "sources": ["<the [source] name of each excerpt your answer relies on>"]
}"""


def call_chat(message, retrieved_context_text=""):
    """`retrieved_context_text` is the plain-text block produced by
    retrieval.format_context_for_prompt() -- pass "" if nothing relevant
    was found, in which case the model is told to answer from general
    knowledge instead and mark itself as not grounded.
    """
    user_content = message
    if retrieved_context_text:
        user_content = f"Reference material:\n{retrieved_context_text}\n\nUser message: {message}"

    data = _chat_json(_CHAT_SYSTEM_PROMPT, user_content)
    for key in ("response", "grounded", "sources"):
        if key not in data:
            raise AIProviderError(f"ChatGPT response was missing '{key}'.")
    return {
        "response": data["response"],
        "grounded": bool(data["grounded"]),
        "sources": list(data["sources"]),
    }


# ---------------------------------------------------------------------------
# Legal Dictionary (one word or phrase -> its meaning in plain English)
# ---------------------------------------------------------------------------

_DEFINE_SYSTEM_PROMPT = """You are a legal dictionary for self-represented litigants in Zimbabwe: ordinary people going to court without a lawyer.

You are given ONE word or short phrase. Explain what it means.

Rules:
- Write in plain, everyday English that someone with no legal training understands.
- If it is a legal term, explain it as it is used in Zimbabwean courts and law (Roman-Dutch common law, English-style procedure).
- If it is an ordinary word, give its everyday meaning and set "is_legal_term" to false.
- If you do not know the word, or it is not a real word, set "found" to false. Never guess or make up a meaning.
- Do not give legal advice. Do not cite specific cases, section numbers or fees unless you are certain.

Respond with ONLY a single JSON object (no markdown, no commentary) matching exactly this shape:
{
  "found": true or false,
  "is_legal_term": true or false,
  "plain_meaning": "<1 or 2 plain sentences saying what it means>",
  "legal_meaning": "<1 or 2 sentences on how courts use it, or null>",
  "example": "<one short example sentence in a court setting, or null>",
  "related_terms": ["<up to 4 related words a person might also look up>"]
}"""


def call_define(term):
    return normalise_definition(_chat_json(_DEFINE_SYSTEM_PROMPT, term), term, "ChatGPT")
