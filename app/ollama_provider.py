"""
ollama_provider.py
-------------------
Talks to a local Ollama server (https://ollama.com) instead of ChatGPT.
Mirrors ai_provider.py's interface exactly (is_configured, call_simplify,
call_chat, call_define) so server.py can treat both providers the same
way -- see the AI_PROVIDER setting in app/config.py.

Why this needs NO pip install at all, unlike ai_provider.py's OpenAI
integration: Ollama is just a local HTTP server, so Python's built-in
urllib is enough to talk to it. Nothing to install beyond Ollama itself.

To turn this on:
    1. Install Ollama from https://ollama.com and start it running
       (it listens on http://localhost:11434 by default).
    2. Download a model once:  ollama pull llama3.2
    3. export AI_PROVIDER=ollama
    4. python3 run.py

Because everything runs on your own machine, this is the more private
option of the two: the text people paste into the simplifier/summarizer/
chat never leaves your computer, and there's no per-request cost. The
trade-off is quality and speed depend on your own hardware, and small
local models are more likely to produce a malformed response than GPT-4o
-- hence the same defensive JSON-parsing and fallback pattern as
ai_provider.py.
"""

import json
import re
import urllib.request
import urllib.error

from app import config
from app.ai_errors import AIProviderError
from app.textutil import normalise_simplified, normalise_definition, as_list


def is_configured():
    """Unlike OpenAI, there's no key to check -- Ollama just needs to be
    running. We don't ping it here (that would slow down every request
    that isn't even using AI mode); instead, a request only actually
    happens when config.AI_PROVIDER == 'ollama', and if the server turns
    out not to be reachable, _post() below raises AIProviderError and the
    caller falls back to rule-based, exactly like an OpenAI failure would.
    """
    return True


def _extract_json(raw_text):
    """Same defensive parsing as ai_provider.py -- small local models are
    even more likely than GPT-4o to wrap JSON in a markdown fence or add
    a stray sentence before/after it.
    """
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise AIProviderError(f"Ollama response wasn't valid JSON: {e}")


def _chat_json(system_prompt, user_text, max_tokens=None, timeout=None):
    """Sends one request to Ollama's /api/chat endpoint and returns the
    parsed JSON reply. "format": "json" tells Ollama to constrain the
    model's output to valid JSON where the model supports it.
    """
    url = config.OLLAMA_BASE_URL.rstrip("/") + "/api/chat"
    payload = {
        "model": config.OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
        "stream": False,
        "format": "json",
        "options": _options(max_tokens),
        # Keep the model loaded between requests. Loading llama3.2 from disk
        # takes many seconds; Ollama unloads it after 5 idle minutes by default.
        "keep_alive": config.OLLAMA_KEEP_ALIVE,
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        # Local models can be slow, especially on a CPU-only machine or
        # the first call after loading a model into memory, and a long
        # judgment can take minutes. The wait is configurable with
        # OLLAMA_TIMEOUT (seconds).
        with urllib.request.urlopen(request, timeout=timeout or config.OLLAMA_TIMEOUT) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # Ollama IS running but refused the request. A 404 almost always means
        # the model hasn't been downloaded yet.
        try:
            detail = json.loads(e.read().decode("utf-8")).get("error", "")
        except Exception:  # noqa: BLE001
            detail = ""
        if e.code == 404:
            raise AIProviderError(
                f"Ollama is running but the model '{config.OLLAMA_MODEL}' isn't installed. "
                f"Run: ollama pull {config.OLLAMA_MODEL}" + (f" ({detail})" if detail else ""))
        raise AIProviderError(f"Ollama returned an error {e.code}: {detail or e}")
    except urllib.error.URLError as e:
        raise AIProviderError(
            f"Couldn't reach Ollama at {config.OLLAMA_BASE_URL} -- is it running? ({e})"
        )
    except Exception as e:  # noqa: BLE001
        raise AIProviderError(f"Ollama request failed: {e}")

    try:
        raw_text = body["message"]["content"]
    except (KeyError, TypeError):
        raise AIProviderError(f"Unexpected response shape from Ollama: {body}")

    return _extract_json(raw_text)


# The system prompts are similar in spirit to ai_provider.py's -- duplicated
# rather than imported so each provider's prompt can be tuned independently
# (small local models often need more explicit instructions than GPT-4o to
# reliably follow a JSON schema).

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

_CHAT_SYSTEM_PROMPT = """You are the IECMS help assistant for self-represented litigants in Zimbabwe. IECMS is the Integrated Electronic Case Management System used by the courts.
You are given a question and reference material from this app's IECMS knowledge base.
Rules:
- Answer ONLY from the reference material. Do not use any other knowledge, even if you are sure of it.
- If the reference material does not answer the question, or it is not about IECMS, set "grounded" to false and say you can only answer IECMS questions from the knowledge base.
- Never invent steps, fees, deadlines, forms, Acts or rules that are not in the reference material.
- Keep the answer short and in plain English.
Respond with ONLY a JSON object, nothing else, in exactly this shape:
{"response": "...", "grounded": true or false, "sources": ["the [source] name of each excerpt you used"]}"""


def call_simplify(text):
    """The Judgment Summarizer with AI: a short, plain-English summary of the
    judgment and a clear statement of what the court decided.

    A judgment that fits in one request is sent as it is. A longer one is
    first cut down to the parts that matter (see judgment.digest: the case
    details, facts, defence, findings and the full decision), so it still
    takes only ONE request. Sending a long judgment part by part took many
    minutes on an ordinary laptop. If the model takes longer than
    OLLAMA_SUMMARY_TIMEOUT, the app falls back to the rule-based summary.
    """
    from app import judgment

    if not text.strip():
        raise AIProviderError("There was no text to summarize.")
    prompt_text = text
    if len(text) > config.OLLAMA_CHUNK_CHARS:
        prompt_text = ("These are the main parts of ONE long judgment, picked out for you. "
                       "Summarise the judgment from them.\n\n" + judgment.digest(text, config.OLLAMA_CHUNK_CHARS))
    data = _chat_json(_SIMPLIFY_SYSTEM_PROMPT, prompt_text,
                      max_tokens=config.OLLAMA_SUMMARY_MAX_TOKENS, timeout=config.OLLAMA_SUMMARY_TIMEOUT)
    return normalise_simplified(data, "Ollama")


def call_chat(message, retrieved_context_text=""):
    user_content = message
    if retrieved_context_text:
        user_content = f"Reference material:\n{retrieved_context_text}\n\nUser message: {message}"

    data = _chat_json(_CHAT_SYSTEM_PROMPT, user_content,
                      max_tokens=config.OLLAMA_CHAT_MAX_TOKENS, timeout=config.OLLAMA_CHAT_TIMEOUT)
    for key in ("response", "grounded", "sources"):
        if key not in data:
            raise AIProviderError(f"Ollama response was missing '{key}'.")
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
- Write in plain, everyday English that someone with no legal training understands. Short sentences.
- If it is a legal term, explain it as it is used in Zimbabwean courts and law (Zimbabwe follows Roman-Dutch law and English procedure).
- If it is an ordinary word, give its everyday meaning and set "is_legal_term" to false.
- If you do not know the word, or it is not a real word, set "found" to false and leave the other fields empty. Never guess or make up a meaning.
- Do not give legal advice. Do not name specific cases, section numbers or fees unless you are certain.
Respond with ONLY a JSON object, nothing else, in exactly this shape:
{"found": true or false,
 "is_legal_term": true or false,
 "plain_meaning": "1 or 2 plain sentences saying what it means",
 "legal_meaning": "1 or 2 sentences on how courts use it, or null if not a legal term",
 "example": "one short example sentence using the word in a court setting, or null",
 "related_terms": ["up to 4 related words a person might also look up"]}"""


def call_define(term):
    data = _chat_json(_DEFINE_SYSTEM_PROMPT, term,
                      max_tokens=config.OLLAMA_DEFINE_MAX_TOKENS, timeout=config.OLLAMA_DEFINE_TIMEOUT)
    return normalise_definition(data, term, "Ollama")


def _options(max_tokens=None):
    # Every request uses the SAME num_ctx: Ollama reloads the whole model
    # when it changes, which costs many seconds each time.
    options = {"temperature": 0.2, "num_ctx": config.OLLAMA_NUM_CTX}
    if max_tokens:
        options["num_predict"] = max_tokens
    return options


def warm_up():
    """Loads the model into memory when the backend starts (in the
    background), so the first real question doesn't wait for it to load.
    It asks for a one-word answer, which works on every Ollama version.
    Failures are only printed: the app still works, and falls back to its
    rule-based answers if the model can't be used."""
    url = config.OLLAMA_BASE_URL.rstrip("/") + "/api/chat"
    payload = {"model": config.OLLAMA_MODEL, "stream": False, "keep_alive": config.OLLAMA_KEEP_ALIVE,
               "messages": [{"role": "user", "content": "Say OK."}], "options": _options(max_tokens=2)}
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            response.read()
        print(f"[ollama] {config.OLLAMA_MODEL} is loaded and ready.")
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8")).get("error", "")
        except Exception:  # noqa: BLE001
            detail = ""
        if e.code == 404:
            print(f"[ollama] the model '{config.OLLAMA_MODEL}' is not installed. Run: ollama pull {config.OLLAMA_MODEL}")
        else:
            print(f"[ollama] Ollama could not load {config.OLLAMA_MODEL} (error {e.code}: {detail or 'no details'}). "
                  f"Test it with: ollama run {config.OLLAMA_MODEL} \"hi\"")
    except Exception as e:  # noqa: BLE001
        print(f"[ollama] could not reach Ollama at {config.OLLAMA_BASE_URL} ({e}). Is the Ollama app running?")


# ---------------------------------------------------------------------------
# Streaming: the answer is passed on word by word as the model writes it,
# so the person starts reading after a second or two instead of staring at
# a spinner until the whole answer is finished.
# ---------------------------------------------------------------------------

def stream_text(system_prompt, user_text, max_tokens=None, timeout=None):
    """Yields the model's answer in small pieces of plain text. `timeout` is
    how long to wait for the NEXT piece (so a slow but working model is never
    cut off half way). Raises AIProviderError if Ollama can't be used."""
    url = config.OLLAMA_BASE_URL.rstrip("/") + "/api/chat"
    payload = {
        "model": config.OLLAMA_MODEL,
        "messages": [{"role": "system", "content": system_prompt},
                     {"role": "user", "content": user_text}],
        "stream": True,
        "keep_alive": config.OLLAMA_KEEP_ALIVE,
        "options": _options(max_tokens),
    }
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
    try:
        response = urllib.request.urlopen(request, timeout=timeout or config.OLLAMA_TIMEOUT)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise AIProviderError(f"Ollama is running but the model '{config.OLLAMA_MODEL}' isn't installed. "
                                  f"Run: ollama pull {config.OLLAMA_MODEL}")
        raise AIProviderError(f"Ollama returned an error {e.code}")
    except Exception as e:  # noqa: BLE001
        raise AIProviderError(f"Couldn't reach Ollama at {config.OLLAMA_BASE_URL} -- is it running? ({e})")
    with response:
        try:
            for line in response:
                if not line.strip():
                    continue
                part = json.loads(line.decode("utf-8"))
                if part.get("error"):
                    raise AIProviderError(f"Ollama error: {part['error']}")
                piece = (part.get("message") or {}).get("content") or ""
                if piece:
                    yield piece
                if part.get("done"):
                    return
        except AIProviderError:
            raise
        except Exception as e:  # noqa: BLE001 - a timeout or a dropped connection mid-answer
            raise AIProviderError(f"Ollama stopped answering ({e})")


DEFINE_TEXT_PROMPT = """You are a legal dictionary for ordinary people in Zimbabwe who go to court without a lawyer.
Explain the word or phrase you are given in plain, everyday English, in short sentences.
If it is a legal term, explain it as it is used in Zimbabwean courts. Do not give legal advice. Never make up a meaning.
Write exactly these four lines and nothing else:
MEANING: one or two plain sentences saying what it means
IN COURT: one sentence on how the courts use it, or "none" if it is not a legal term
EXAMPLE: one short example sentence using it in a court setting
RELATED: up to 4 related words, separated by commas
If you do not know the word, or it is not a real word, write only: NOT FOUND"""


def stream_define(term):
    return stream_text(DEFINE_TEXT_PROMPT, term,
                       max_tokens=config.OLLAMA_DEFINE_MAX_TOKENS, timeout=config.OLLAMA_DEFINE_TIMEOUT)


NOT_IN_KB = "NOT_IN_KB"

CHAT_TEXT_PROMPT = f"""You are the IECMS help assistant for self-represented litigants in Zimbabwe. IECMS is the Integrated Electronic Case Management System used by the courts.
You are given a question and reference material from this app's knowledge base.
Rules:
- Answer ONLY from the reference material. Never use outside knowledge, and never invent steps, fees, deadlines or forms.
- If the reference material does not answer the question, reply with exactly: {NOT_IN_KB}
- Keep the answer short, in plain English. When the material lists steps, give them as a numbered list.
- Do not mention "the reference material" or the excerpt numbers; just answer."""


def stream_chat(message, retrieved_context_text):
    user_content = f"Reference material:\n{retrieved_context_text}\n\nQuestion: {message}"
    return stream_text(CHAT_TEXT_PROMPT, user_content,
                       max_tokens=config.OLLAMA_CHAT_MAX_TOKENS, timeout=config.OLLAMA_CHAT_TIMEOUT)


SUMMARY_TEXT_PROMPT = """You summarise court judgments for ordinary people in Zimbabwe who have no legal training.
Write ONE short paragraph (4 to 6 plain sentences): who the parties are, what the case was about,
what the court found, and what it finally decided. Use everyday English. Keep names, dates and
amounts exactly as written. Judgments quote OTHER cases; those are not this court's decision.
Do not add anything that is not in the text. Write only the paragraph."""


def stream_summary(text):
    """The Judgment Summarizer's plain-English paragraph, word by word. The
    case details, key points and the court's orders are read from the text
    itself (app/judgment.py), so the model only has to write this paragraph:
    a short prompt and a short answer, which is what makes it quick."""
    from app import judgment

    prompt_text = text if len(text) <= config.OLLAMA_SUMMARY_INPUT_CHARS else (
        "The main parts of one long judgment:\n\n" + judgment.digest(text, config.OLLAMA_SUMMARY_INPUT_CHARS))
    return stream_text(SUMMARY_TEXT_PROMPT, prompt_text,
                       max_tokens=config.OLLAMA_SUMMARY_STREAM_TOKENS, timeout=config.OLLAMA_SUMMARY_TIMEOUT)
