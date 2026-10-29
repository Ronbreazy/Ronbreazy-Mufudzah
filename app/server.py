"""
server.py
---------
The actual web server. Built on Python's built-in `http.server`, so there
is no framework to learn -- just:

  1. A request comes in (method + path + headers + body).
  2. We find the handler function registered for that (method, path).
  3. The handler reads the request, talks to `database.py` /
     `legal_content.py`, and returns (status_code, response_dict).
  4. We send that back as JSON, with CORS headers so a webpage running from
     a different origin (e.g. opening the frontend .html file directly, or
     serving it from a different port) is allowed to call this API.

Each handler function has the signature:
    handler(method, path, headers, json_body) -> (status_code, response_dict)
"""

import json
import os
import re
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app import judgment, database
from app import auth
from app import legal_content
from app import ai_provider
from app import ollama_provider
from app import retrieval
from app import conversation
from app import knowledge
from app import dictionary
from app import documents
from app import doc_export
from app import config
from app.ai_errors import AIProviderError


class FileResponse:
    """Return this from a handler (instead of a dict) to send a file to
    download, e.g. a generated Word or PDF document."""
    def __init__(self, data, content_type, filename):
        self.data = data
        self.content_type = content_type
        self.filename = filename


class PageResponse:
    """Return this from a handler to send a web page (the frontend)."""
    def __init__(self, data):
        self.data = data


_FRONTEND_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "frontend", "selfactorshub-frontend.html")


def handle_frontend(method, path, headers, body):
    """GET / -- the website itself, so other computers and phones can open
    http://<this computer's address>:8000/ in a browser."""
    try:
        with open(_FRONTEND_FILE, "rb") as f:
            return 200, PageResponse(f.read())
    except OSError:
        raise ApiError(404, "The frontend file was not found in the frontend folder.")


class StreamResponse:
    """Return this from a handler to send a stream of JSON events, one per
    line (newline-delimited JSON), as they happen -- used so chat and
    dictionary answers appear word by word while the AI is writing them.
    `first` is the first event, already taken from the generator so that
    input errors become a normal 400 before anything is sent."""
    def __init__(self, first, rest):
        self.first = first
        self.rest = rest


def _stream(events):
    events = iter(events)
    return 200, StreamResponse(next(events), events)


class ApiError(Exception):
    """Raise this inside a handler to return an error response with a
    specific HTTP status code, e.g. `raise ApiError(400, "email required")`.
    """
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _require_auth(headers):
    """Reads the Authorization header, verifies the token, and returns the
    logged-in user's row from the database. Raises ApiError(401, ...) if
    there's no valid token.
    """
    auth_header = headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise ApiError(401, "Missing or invalid Authorization header.")
    token = auth_header[len("Bearer "):].strip()
    payload = auth.verify_token(token)
    if not payload:
        raise ApiError(401, "Invalid or expired token.")
    user = database.get_user_by_id(payload["user_id"])
    if not user:
        raise ApiError(401, "User for this token no longer exists.")
    return user


# ---------------------------------------------------------------------------
# Endpoint handlers -- one function per route, matching CONFIG.ENDPOINTS in
# the frontend exactly.
# ---------------------------------------------------------------------------

def handle_health(method, path, headers, body):
    return 200, {"status": "ok"}


def handle_register(method, path, headers, body):
    full_name = (body.get("full_name") or "").strip()
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""

    if not full_name or not email or not password:
        raise ApiError(400, "full_name, email and password are all required.")
    if not documents.clean_name(full_name):
        raise ApiError(400, documents.NAME_ERROR)
    if "@" not in email:
        raise ApiError(400, "Please provide a valid email address.")
    if len(password) < 6:
        raise ApiError(400, "Password must be at least 6 characters.")
    if database.get_user_by_email(email):
        raise ApiError(409, "An account with that email already exists.")

    password_hash, salt = auth.hash_password(password)
    user_id = database.create_user(full_name, email, password_hash, salt)
    token = auth.create_token(user_id, email)

    return 201, {
        "access_token": token,
        "user": {"full_name": full_name, "email": email},
    }


def handle_login(method, path, headers, body):
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""

    if not email or not password:
        raise ApiError(400, "email and password are required.")

    user = database.get_user_by_email(email)
    if not user or not auth.verify_password(password, user["password_hash"], user["salt"]):
        raise ApiError(401, "Incorrect email or password.")

    token = auth.create_token(user["id"], user["email"])
    return 200, {
        "access_token": token,
        "user": {"full_name": user["full_name"], "email": user["email"]},
    }


def _document_request(body):
    document_type = body.get("document_type") or ""
    court_tier = body.get("court_tier") or ""
    case_details = body.get("case_details") or {}
    if not isinstance(case_details, dict):
        raise ApiError(400, "case_details must be an object.")
    try:
        return documents.generate(document_type, court_tier, case_details)
    except documents.DocumentInputError as e:
        raise ApiError(400, str(e))


def handle_generate_document(method, path, headers, body):
    """POST /documents/generate -> {title, content, blocks, sources_used, warnings, court_tier}"""
    return 200, _document_request(body)


def handle_document_templates(method, path, headers, body):
    """GET /documents/templates -> the documents the wizard offers and the questions each one asks."""
    return 200, documents.list_templates()


_EXPORT_FORMATS = {
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", doc_export.render_docx),
    "pdf": ("application/pdf", doc_export.render_pdf),
}


def handle_document_download(method, path, headers, body):
    """POST /documents/download with the same body as /documents/generate plus
    "format": "docx" or "pdf" -> the document as a file to download. The
    document is built again on the server from the answers, so the file always
    matches the template exactly."""
    fmt = str(body.get("format") or "").lower()
    if fmt not in _EXPORT_FORMATS:
        raise ApiError(400, "format must be 'docx' or 'pdf'.")
    doc = _document_request(body)
    content_type, render = _EXPORT_FORMATS[fmt]
    return 200, FileResponse(render(doc["blocks"], doc["title"]), content_type,
                             doc_export.safe_filename(doc["title"], fmt))


# ---------------------------------------------------------------------------
# AI provider dispatch
# ---------------------------------------------------------------------------
# One place that decides which provider (if any) is active, based on
# config.AI_PROVIDER. Adding a third provider later means: write a module
# with the same three functions (is_configured, call_simplify,
# call_chat) that raises AIProviderError on failure, then
# add one line to this dict -- nothing else in this file changes.
_PROVIDERS = {
    "openai": ai_provider,
    "ollama": ollama_provider,
}


def _active_provider():
    """Returns the provider module to use, or None for rule-based only."""
    provider = _PROVIDERS.get(config.AI_PROVIDER)
    if provider and provider.is_configured():
        return provider
    return None


def _run_simplify(text):
    """The Judgment Summarizer: turns a judgment into a SHORT summary in plain
    English (what it was about, the key points, what the court decided).

    Tries the configured AI provider first, falling back to the offline
    rule-based version if AI mode is off or the call fails for any reason.
    Returns (result_dict, engine_name) -- engine_name is 'ai:openai',
    'ai:ollama', or 'rule_based', so the frontend can show which answered.
    """
    provider = _active_provider()
    if provider:
        try:
            result = dict(provider.call_simplify(text))
            # Small local models are often too modest about whether a text is
            # legal. If our own glossary recognises legal terms in what was
            # pasted, treat it as legal content regardless of what the model
            # said -- this decides whether it joins the case-law library.
            if not result.get("grounded") and retrieval.retrieve_glossary_terms(text):
                result["grounded"] = True
            return result, f"ai:{config.AI_PROVIDER}"
        except AIProviderError as e:
            # Log it server-side and quietly fall through to rule-based --
            # a flaky network, a down Ollama server, or an expired key
            # should never break this feature for the person using it.
            print(f"[{config.AI_PROVIDER}] {_why(e)} -- the summarizer used its own summary instead.")
    return legal_content.simplify_judgment(text), "rule_based"


def _rule_based_chat(message, prefer=""):
    """The chatbot with no AI: shows the best-matching passage from the
    knowledge base (if it's a strong enough match -- see config.CHAT_MIN_OVERLAP),
    otherwise the out-of-scope reply. Nothing is ever answered from anywhere
    else.
    """
    hits = retrieval.retrieve_knowledge(message, k=1, min_overlap=config.CHAT_MIN_OVERLAP, prefer=prefer)
    if hits:
        hit = hits[0]
        return {
            "response": f"{hit['content']}\n\n(From the knowledge base: {hit['source']})",
            "grounded": True,
            "sources": [hit["source"]],
        }
    return legal_content.chat_reply(message)


def _run_chat(message, prefer=""):
    """The chatbot answers IECMS questions ONLY, from the knowledge base ONLY.

    1. Search the knowledge base. If nothing matches, stop here with the
       out-of-scope reply -- the AI is never asked, so it can't answer from
       its own general knowledge.
    2. With an AI provider on, hand it ONLY those passages (retrieval-augmented
       generation) and tell it to answer from them alone. If it says they don't
       answer the question, give the out-of-scope reply instead.
    3. With AI off, or if the AI call fails, show the best passage directly.
    """
    hits = retrieval.retrieve_knowledge(message, k=3, min_overlap=config.CHAT_MIN_OVERLAP, prefer=prefer)
    if not hits:
        return legal_content.chat_reply(message), "rule_based"

    provider = _active_provider() if config.CHAT_USE_AI else None
    if provider:
        try:
            context_text = retrieval.format_context_for_prompt({"knowledge": hits})
            reply = dict(provider.call_chat(message, context_text))
            engine = f"ai:{config.AI_PROVIDER}"
            if not reply.get("grounded"):
                return legal_content.chat_reply(message), engine
            # Only ever cite sources that were actually in the knowledge base.
            available = []
            for hit in hits:
                if hit["source"] not in available:
                    available.append(hit["source"])
            reply["sources"] = [s for s in reply.get("sources", []) if s in available] or available
            return reply, engine
        except AIProviderError as e:
            print(f"[{config.AI_PROVIDER}] chat call failed, falling back to rule-based: {e}")
    return _rule_based_chat(message, prefer), "rule_based"


def handle_chat(method, path, headers, body):
    message = body.get("message") or ""
    if not message.strip():
        raise ApiError(400, "message is required.")
    user = body.get("user")  # optional: {"full_name": ...}, a name, or null

    # Small talk ("hie", "thanks", "who are you?") gets a friendly reply with no
    # search and no AI. A greeting followed by a real question ("hi, how do I
    # log in?") is answered as the question.
    kind, question = conversation.classify(message)
    if kind:
        return 200, {
            "response": conversation.reply(kind, user),
            "grounded": False,
            "sources": [],
            "engine": "conversation",
            "kind": kind,
        }

    result, engine = _run_chat(question, prefer=conversation.search_boost(question))
    result = dict(result)
    result["engine"] = engine
    return 200, result


def _chat_stream_events(message, prefer):
    """Same rules as _run_chat, but the AI's answer is passed on as it is
    written. Events: {"type": "delta", "text"} ... then {"type": "done", ...}."""
    hits = retrieval.retrieve_knowledge(message, k=config.CHAT_AI_PASSAGES, min_overlap=config.CHAT_MIN_OVERLAP, prefer=prefer)
    if not hits:
        yield dict(legal_content.chat_reply(message), type="done", engine="rule_based")
        return
    provider = _active_provider() if config.CHAT_USE_AI else None
    if not provider:
        yield dict(_rule_based_chat(message, prefer), type="done", engine="rule_based")
        return
    if not hasattr(provider, "stream_chat"):
        reply, engine = _run_chat(message, prefer)
        yield dict(reply, type="done", engine=engine)
        return

    sources = []
    for hit in hits:
        if hit["source"] not in sources:
            sources.append(hit["source"])
    marker = ollama_provider.NOT_IN_KB
    written, sent = "", 0
    try:
        context_text = retrieval.format_context_for_prompt({"knowledge": hits})
        for piece in provider.stream_chat(message, context_text):
            written += piece
            head = written.lstrip()
            # Hold the first few characters back until it's clear the model
            # isn't saying "NOT_IN_KB" (the question isn't covered).
            if len(head) < len(marker) and marker.startswith(head):
                continue
            if head.startswith(marker):
                break
            if len(written) > sent:
                yield {"type": "delta", "text": written[sent:]}
                sent = len(written)
    except AIProviderError as e:
        print(f"[{config.AI_PROVIDER}] {_why(e)} -- the chatbot showed the knowledge-base passage instead.")
        if not sent:
            yield dict(_rule_based_chat(message, prefer), type="done", engine="rule_based")
            return
    answer = written.strip()
    if not answer or answer.startswith(marker):
        yield dict(legal_content.chat_reply(message), type="done", engine=f"ai:{config.AI_PROVIDER}")
        return
    yield {"type": "done", "response": answer, "grounded": True, "sources": sources,
           "engine": f"ai:{config.AI_PROVIDER}"}


def handle_chat_stream(method, path, headers, body):
    """POST /chat/stream -- like /chat, but the answer arrives word by word."""
    message = body.get("message") or ""
    if not message.strip():
        raise ApiError(400, "message is required.")
    kind, question = conversation.classify(message)
    if kind:
        return _stream([{"type": "done", "response": conversation.reply(kind, body.get("user")),
                         "grounded": False, "sources": [], "engine": "conversation", "kind": kind}])
    return _stream(_chat_stream_events(question, conversation.search_boost(question)))


def handle_chat_welcome(method, path, headers, body):
    """The message the chat window can open with, e.g. "Welcome to the Self
    Actors Hub ZW, Tendai! ... How can I assist you today?" Send {"user": ...}
    to include the person's first name."""
    return 200, {
        "response": conversation.welcome_message(body.get("user")),
        "grounded": False,
        "sources": [],
        "engine": "conversation",
        "kind": "greeting",
    }


def _why(error):
    """A short, plain reason for the terminal when the AI couldn't answer."""
    text = str(error)
    if "timed out" in text:
        return f"{config.OLLAMA_MODEL} was too slow to answer (timed out)"
    return f"AI unavailable ({text})"


def _complete_simplify(text, result, engine):
    """Fills in the fields every summary has, whichever engine wrote it."""
    result = dict(result)
    result.setdefault("key_points", [])
    result.setdefault("outcome", None)
    result.setdefault("decision", [result["outcome"]] if result["outcome"] else [])
    result.setdefault("headline", None)
    if engine != "rule_based":
        # The case details, the cases it quotes and its legal words are read
        # straight from the text, so they are the same whichever engine answered.
        extra = judgment.summarize(text, legal_content.LEGAL_TERMS)
        result.setdefault("case_details", extra["case_details"])
        result.setdefault("cases_cited", extra["cases_cited"])
        result.setdefault("legal_terms", extra["legal_terms"])
        if not result["headline"]:
            result["headline"] = extra["headline"]
    result.setdefault("case_details", {})
    result.setdefault("cases_cited", [])
    result.setdefault("legal_terms", [])
    result["engine"] = engine  # 'ai:openai', 'ai:ollama', or 'rule_based' -- informational for the frontend
    return result


def _save_case_law(text, result):
    # Save every summary of genuinely legal text into the Case Law library,
    # so it shows up in the frontend's Case Law list as a worked example for
    # other users. Ungrounded submissions (no recognised legal content) are
    # skipped so the library doesn't fill up with unrelated text.
    if not result.get("grounded"):
        return
    details = result.get("case_details") or {}
    if details.get("parties"):
        title = judgment._title(details["parties"])
        if details.get("case_number"):
            title += f" ({details['case_number']})"
    else:
        title = text.strip().splitlines()[0][:80]
        if len(text.strip()) > 80:
            title += "..."
    database.create_case_law_entry(title=title, simplified_text=result["simplified_text"],
                                   citations=result["citations"])


def handle_simplify(method, path, headers, body):
    text = body.get("text") or ""
    if not text.strip():
        raise ApiError(400, "text is required.")
    result, engine = _run_simplify(text)
    result = _complete_simplify(text, result, engine)
    _save_case_law(text, result)
    return 200, result


def _simplify_stream_events(text):
    """The Judgment Summarizer, quick version:
    1. {"type": "base", "result"}  -- at once: the case details, key points
       and the court's decision, read straight from the judgment.
    2. {"type": "delta", "text"}    -- llama3.2's plain-English summary,
       word by word, while it writes it.
    3. {"type": "done", "result"}   -- the finished summary."""
    base = _complete_simplify(text, legal_content.simplify_judgment(text), "rule_based")
    provider = _active_provider()
    yield {"type": "base", "result": base, "writing": bool(provider)}
    final = base
    if provider and hasattr(provider, "stream_summary"):
        written = ""
        try:
            for piece in provider.stream_summary(text):
                written += piece
                yield {"type": "delta", "text": piece}
            if written.strip():
                final = dict(base, simplified_text=written.strip(), engine=f"ai:{config.AI_PROVIDER}")
        except AIProviderError as e:
            print(f"[{config.AI_PROVIDER}] {_why(e)} -- the summarizer showed its own summary instead.")
            if written.strip():
                final = dict(base, simplified_text=written.strip(), engine=f"ai:{config.AI_PROVIDER}")
    elif provider:
        result, engine = _run_simplify(text)
        final = _complete_simplify(text, result, engine)
    _save_case_law(text, final)
    yield {"type": "done", "result": final}


def handle_simplify_stream(method, path, headers, body):
    """POST /simplify/stream -- the summary appears at once, then llama3.2's
    plain-English paragraph word by word."""
    text = body.get("text") or ""
    if not text.strip():
        raise ApiError(400, "text is required.")
    return _stream(_simplify_stream_events(text))


def handle_directory(method, path, headers, body):
    # GET /directory            -> the normal browse list
    # GET /directory?q=kanokanga -> every firm matching the search, including
    #                               the ones that are hidden from the browse list
    return 200, {"results": database.list_directory(query=body.get("q"))}


def handle_forum_list(method, path, headers, body):
    return 200, database.list_forum_posts()


def handle_forum_create(method, path, headers, body):
    user = _require_auth(headers)  # must be logged in to post
    title = (body.get("title") or "").strip()
    post_body = (body.get("body") or "").strip()
    if not title or not post_body:
        raise ApiError(400, "title and body are required.")
    post = database.create_forum_post(user["full_name"], title, post_body)
    return 201, post


def handle_case_law(method, path, headers, body):
    return 200, database.list_case_law()


def handle_dictionary(method, path, headers, body):
    """The Legal Dictionary.

    GET /dictionary              -> {terms: [...], ai_enabled} -- the A-Z glossary to show before searching
    GET /dictionary?q=affidavit  -> the meaning of the word (AI, cached, or glossary -- see app/dictionary.py)
    """
    query = (body.get("q") or "").strip()
    if not query:
        return 200, {"terms": dictionary.browse_list(), "ai_enabled": _active_provider() is not None,
                     "ai_provider": config.AI_PROVIDER or None}
    try:
        return 200, dictionary.lookup(query, _active_provider(), config.AI_PROVIDER)
    except dictionary.DictionaryInputError as e:
        raise ApiError(400, str(e))


def handle_dictionary_stream(method, path, headers, body):
    """GET /dictionary/stream?q=word -- the meaning arrives word by word."""
    query = (body.get("q") or "").strip()
    if not query:
        raise ApiError(400, "Type a word to look up.")
    try:
        return _stream(dictionary.lookup_stream(query, _active_provider(), config.AI_PROVIDER))
    except dictionary.DictionaryInputError as e:
        raise ApiError(400, str(e))


def handle_dictionary_suggest(method, path, headers, body):
    """GET /dictionary/suggest?q=affi -> {suggestions: ["affidavit"]}, for type-ahead.
    Glossary only and instant -- never calls the AI."""
    query = re.sub(r"\s+", " ", (body.get("q") or "").lower()).strip()
    return 200, {"suggestions": dictionary.suggestions(query, limit=8) if query else []}


# Route table: (METHOD, path) -> handler
ROUTES = {
    ("GET", "/"): handle_frontend,
    ("GET", "/index.html"): handle_frontend,
    ("GET", "/health"): handle_health,
    ("POST", "/auth/register"): handle_register,
    ("POST", "/auth/login"): handle_login,
    ("POST", "/chat"): handle_chat,
    ("POST", "/chat/welcome"): handle_chat_welcome,
    ("POST", "/chat/stream"): handle_chat_stream,
    ("POST", "/documents/generate"): handle_generate_document,
    ("GET", "/documents/templates"): handle_document_templates,
    ("POST", "/documents/download"): handle_document_download,
    ("POST", "/simplify"): handle_simplify,
    ("POST", "/simplify/stream"): handle_simplify_stream,
    ("GET", "/directory"): handle_directory,
    ("GET", "/forum/posts"): handle_forum_list,
    ("POST", "/forum/posts"): handle_forum_create,
    ("GET", "/case-law"): handle_case_law,
    ("GET", "/dictionary"): handle_dictionary,
    ("GET", "/dictionary/suggest"): handle_dictionary_suggest,
    ("GET", "/dictionary/stream"): handle_dictionary_stream,
}


class Handler(BaseHTTPRequestHandler):
    # Quieter server logs -- comment this out if you want to see every
    # request logged to the console.
    def log_message(self, format, *args):
        pass

    def _send_json(self, status_code, data):
        body_bytes = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body_bytes)))
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(body_bytes)

    def _send_file(self, status_code, response):
        self.send_response(status_code)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(response.data)))
        self.send_header("Content-Disposition", f'attachment; filename="{response.filename}"')
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(response.data)

    def _send_stream(self, response):
        # No Content-Length: the connection is closed when the stream ends.
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self._send_cors_headers()
        self.end_headers()
        self.close_connection = True

        def send(event):
            self.wfile.write((json.dumps(event) + "\n").encode("utf-8"))
            self.wfile.flush()

        try:
            send(response.first)
            for event in response.rest:
                send(event)
        except (BrokenPipeError, ConnectionResetError):
            return  # the person closed the page; stop quietly
        except Exception as e:  # noqa: BLE001 - headers are already sent, so report it in the stream
            try:
                send({"type": "error", "error": f"Internal server error: {e}"})
            except Exception:  # noqa: BLE001
                pass

    def _send_cors_headers(self):
        # Allows the frontend (which may be served from a different origin,
        # e.g. a local file or a different port) to call this API.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        # Lets the page read the file name of a downloaded document.
        self.send_header("Access-Control-Expose-Headers", "Content-Disposition")

    def do_OPTIONS(self):
        # Browsers send an OPTIONS "preflight" request before certain
        # cross-origin requests (like ones with an Authorization header).
        # We just need to answer it with the allowed methods/headers.
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            raise ApiError(400, "Request body must be valid JSON.")

    def _dispatch(self, method):
        # Strip any query string (e.g. "/directory?q=x") before matching; for
        # GET requests it is handed to the handler as `body` ({"q": "x"}).
        path = re.sub(r"\?.*$", "", self.path)
        query = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).items()}
        handler = ROUTES.get((method, path))

        if handler is None:
            self._send_json(404, {"error": f"No such endpoint: {method} {path}"})
            return

        try:
            body = self._read_json_body() if method == "POST" else query
            status_code, response_data = handler(method, path, self.headers, body)
            if isinstance(response_data, FileResponse):
                self._send_file(status_code, response_data)
            elif isinstance(response_data, PageResponse):
                self.send_response(status_code)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(response_data.data)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(response_data.data)
            elif isinstance(response_data, StreamResponse):
                self._send_stream(response_data)
            else:
                self._send_json(status_code, response_data)
        except ApiError as e:
            self._send_json(e.status_code, {"error": e.message})
        except Exception as e:  # noqa: BLE001 - last line of defence
            self._send_json(500, {"error": f"Internal server error: {e}"})


class AppServer(ThreadingHTTPServer):
    # Python's default is to queue only 5 waiting connections; under a burst
    # of users (load testing showed 50 at once) the rest were reset. A longer
    # queue lets them wait their turn instead.
    request_queue_size = 128
    daemon_threads = True


def _lan_address():
    """This computer's address on the Wi-Fi / network (e.g. 192.168.1.23)."""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))  # no data is sent; this only picks the network card
            return sock.getsockname()[0]
    except OSError:
        return None


def run_server(host, port):
    database.init_db()
    try:
        count = knowledge.sync()
        print(f"Knowledge base: {count} entries loaded from {config.KNOWLEDGE_DIR}")
        if not count:
            print("Knowledge base: EMPTY -- the chatbot can only answer from files in that folder "
                  "(.docx, .pdf, .md or .txt). Add your documents there and restart.")
    except Exception as e:  # noqa: BLE001 - a bad knowledge file must not stop the whole backend
        print(f"Knowledge base: could not load ({e}) -- the chatbot will run without it.")
    if config.AI_PROVIDER == "ollama":
        import threading
        threading.Thread(target=ollama_provider.warm_up, daemon=True).start()
    server = AppServer((host, port), Handler)
    print(f"ZimLegal backend running at http://{host}:{port}")
    print(f"Open the website on this computer:   http://localhost:{port}/")
    lan = _lan_address()
    if lan:
        print(f"Phones/computers on the same Wi-Fi: http://{lan}:{port}/")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()
