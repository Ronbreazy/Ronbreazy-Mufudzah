# Self-Actors Hub ZW

An AI-assisted legal empowerment platform for self-represented litigants ("self-actors") in Zimbabwe's courts, focused on the Harare High Court and Magistrates Court. Final-year dissertation project, BComm Honours in Information Systems, Midlands State University.

**Live demo:** [add your Render link here]  ·  **Demo video:** [optional]

> Information and document drafts only, not legal advice.

## What it does

| Feature | What the user gets |
|---|---|
| **Document Wizard** | Answers simple questions and gets a court-ready document laid out like filed papers (civil summons, divorce summons, affidavit, notice of opposition, protection order, court application, notice of appeal), downloadable as Word or PDF. |
| **Judgment Summarizer** | Pastes a judgment and gets the case details, a plain-English summary, the key points and exactly what the court decided. |
| **Legal Dictionary** | Looks up a legal word and gets a plain-English meaning, streamed word by word and saved for next time. |
| **IECMS Assistant** | Asks how to register, file, pay or join a virtual hearing on IECMS (the courts' e-filing system) and gets the answer from the knowledge base, with its source. |
| **Lawyers and Legal Aid** | Searches a directory of lawyers and free legal aid organisations, with map links. |
| **Education and Forum** | Rights, court procedures, case law and a community forum. |

## How it is built

- **Backend:** Python standard library only (`http.server`, `sqlite3`, `hashlib`, `hmac`): REST API, SQLite storage, salted PBKDF2 password hashing and HMAC-signed tokens that expire after 24 hours.
- **AI:** Llama 3.2 running locally through Ollama, so case details never leave the server. Every AI feature has a rule-based fallback, so the system works without the model (as on the free live demo).
- **Search:** keyword retrieval with stemming and synonyms over Markdown, PDF and Word files; out-of-scope questions are refused.
- **Frontend:** a single-page HTML/CSS/JavaScript app with hash routing, served by the backend; works on phones.
- **Documents:** Word (.docx) and PDF files generated without third-party libraries.

## Results

- 123 automated tests against a running server, all passing (`python -m unittest tests.test_backend`)
- Chatbot: 23 of 25 everyday IECMS questions answered from the correct page (92%); 5 of 5 off-topic questions refused
- Load test: 50 concurrent users, 1,500 requests, all successful
- Security: 10 of 11 checks passed (SQL injection, XSS, token tampering and expiry); the remaining item, login-attempt limiting, is the next planned improvement

## Run it locally

```
python run.py
```
Then open http://localhost:8000/. Python 3.8+ is all that is needed (`pip install pypdf` to read PDF files in the knowledge base). For the AI features, install [Ollama](https://ollama.com), run `ollama pull llama3.2:1b`, then start with `AI_PROVIDER=ollama` (on Windows: double-click `start.bat`).

## Deploy

`render.yaml` deploys the app on [Render](https://render.com) for free: New > Blueprint > choose this repository.

---

# Technical documentation

## Quick start on Windows

Double-click **start.bat** (or type `start.bat` in the VS Code terminal). It turns on the AI (Ollama, model `llama3.2:1b`) and starts the backend; then open http://localhost:8000/ in the browser. The model must be installed once with `ollama pull llama3.2:1b`.

## Why plain Python, and no external AI by default

- **Nothing to install.** `http.server` (web server), `sqlite3` (database),
  and `hashlib`/`hmac` (password hashing + login tokens) all ship with
  Python.
- **Every response is traceable.** Document drafting and text simplification
  use plain, readable rules and templates you can open and read in
  `app/legal_content.py` — not a black-box AI call — and the chatbot answers
  only from the files you put in `knowledge_base/`. You can correct or extend
  them directly, and a real lawyer can review exactly what the app says.
- **This is a solid starting point, not a finished legal product.** The
  templates, the plain-language dictionary, and the chat answers are all
  intentionally generic and marked where they need real legal review
  (search for "SAMPLE" and "review" in `app/legal_content.py` and
  `app/database.py`).

## Project layout

```
zimlegal-backend/
├── run.py                  # ← start here: python3 run.py
├── knowledge_base/         # ← YOUR files for the chatbot go here (see below)
├── requirements.txt        # OPTIONAL extras: pypdf (PDF knowledge files), openai (ChatGPT mode)
├── app/
│   ├── config.py           # settings: secret key, DB path, host/port
│   ├── ai_errors.py        # shared exception type for all AI providers
│   ├── ai_provider.py      # OPTIONAL ChatGPT integration (isolated -- see below)
│   ├── ollama_provider.py  # OPTIONAL local Ollama integration (isolated -- see below)
│   ├── retrieval.py        # lightweight keyword search over your own data (the "R" in RAG)
│   ├── knowledge.py        # loads the .md/.txt/.docx/.pdf files in knowledge_base/ for the chatbot
│   ├── textutil.py         # shared helpers (splitting long text, tidying model output)
│   ├── database.py         # SQLite tables + all read/write functions
│   ├── auth.py             # password hashing + login token creation/checking
│   ├── conversation.py     # small talk (hie, thanks, who are you), the welcome message, question tweaks
│   ├── legal_content.py    # document templates, plain-language dictionary, out-of-scope chat reply
│   ├── dictionary.py       # the Legal Dictionary: look-up via Ollama/ChatGPT, saved answers, glossary fallback
│   ├── documents.py        # Document Wizard templates (affidavit, notice of opposition, protection order, ...)
│   ├── doc_export.py       # writes documents as Word (.docx) and PDF, standard library only
│   └── server.py           # HTTP server: routes requests to handler functions
├── frontend/
│   └── selfactorshub-frontend.html  # the web app (Education → Dictionary is searchable)
└── tests/
    └── test_backend.py     # starts a real server and tests every endpoint
```

## Running it

```bash
cd zimlegal-backend
python3 run.py
```

You should see:

```
ZimLegal backend running at http://0.0.0.0:8000
Press Ctrl+C to stop.
```

The frontend's `CONFIG.API_BASE_URL` is already set to
`http://localhost:8000`, so once this is running, just open the frontend
HTML file in your browser and it will connect automatically (the little
try/catch around every `apiCall` in the frontend means it still limps
along with local sample data if the backend isn't reachable, but with the
backend running you'll be talking to real endpoints — real signup/login,
a real forum, a real directory, etc).

A SQLite file called `zimlegal.db` will be created automatically the first
time you run it, seeded with a few sample directory entries and forum
posts (the case-law library starts empty — see the /simplify endpoint below).

## Running the tests

```bash
cd zimlegal-backend
python3 tests/test_backend.py
```

This starts a real copy of the server on a test port and makes real HTTP
requests against every endpoint (signup, login, chat, document generation,
simplification, the directory, the forum, case law, and error cases like
wrong passwords or missing fields), so a passing run means the whole stack
is actually working together, not just individual functions in isolation.

## Endpoints

All of these match `CONFIG.ENDPOINTS` in the frontend exactly.

| Method | Path                  | Auth required | Purpose |
|--------|-----------------------|:---:|---------|
| GET    | `/health`             | No  | Used by the frontend to show backend-online status |
| POST   | `/auth/register`      | No  | `{full_name, email, password}` → `{access_token, user}` |
| POST   | `/auth/login`         | No  | `{email, password}` → `{access_token, user}` |
| POST   | `/chat`               | No  | `{message, user}` → `{response, grounded, sources, engine}` -- **IECMS questions only, answered only from your knowledge base** (with AI: retrieval-augmented). Anything the knowledge base doesn't cover gets a polite "I can only answer IECMS questions" reply with `grounded: false` |
| POST   | `/chat/welcome`       | No  | `{user}` (optional) → `{response, kind}` -- the opening message, e.g. "Welcome to the Self Actors Hub ZW, Tendai! … How can I assist you today?" Use it for the first bubble in the chat window |
| GET    | `/documents/templates` | No | → `{courts, templates: [{id, label, description, courts, fields}]}` -- the documents the wizard offers and the questions each asks |
| POST   | `/documents/generate` | No  | `{document_type, court_tier, case_details}` → `{title, content, blocks, sources_used, warnings, court_tier}` |
| POST   | `/documents/download` | No  | same body as `/documents/generate` plus `"format": "docx"` or `"pdf"` → the document as a Word or PDF file |
| POST   | `/simplify`           | No  | `{text}` → `{simplified_text, key_points, outcome, citations, grounded, engine}` -- **the Judgment Summarizer** -- also returns `decision`, `headline`, `case_details`, `cases_cited` and `legal_terms`. Also saves legal submissions into the Case Law library. |
| GET    | `/directory`          | No  | → `{results: [...]}` -- the browse list (firms hidden from it are left out). `GET /directory?q=kanokanga` → every firm matching the search, **including the search-only ones** (see "The lawyers directory" below) |
| GET    | `/forum/posts`        | No  | → `[{id, author_name, title, body, created_at}, ...]` |
| POST   | `/forum/posts`        | **Yes** | `{title, body}` → created post (author taken from your login token) |
| GET    | `/case-law`           | No  | → `[{id, title, simplified_text, citations, court_tier, created_at}, ...]` |
| GET    | `/dictionary`         | No  | → `{terms: [...], ai_enabled}` -- the A-Z glossary to show before searching |
| GET    | `/dictionary?q=affidavit` | No | → `{term, found, is_legal_term, plain_meaning, legal_meaning, example, related_terms, glossary_meaning, source, engine, cached, suggestions}` -- **the Legal Dictionary** (see below) |
| GET    | `/dictionary/suggest?q=affi` | No | → `{suggestions: [...]}` -- instant type-ahead from the glossary (never calls the AI) |

For the one protected endpoint, send your login token like this:

```
Authorization: Bearer <the access_token from login/register>
```

(This is exactly what the frontend's `apiCall()` helper already does
automatically whenever a token is present.)

## The lawyers directory

`GET /directory` returns the browse list: the free legal-aid organisations
and **six** private practitioners (two full rows of three cards). More Harare
firms from a pasted list are stored as **search-only**: they are left out of
the browse list and show up only when someone searches, through
`GET /directory?q=<words>`. A search considers every entry; a firm matches when
every word typed appears in its name, type, focus areas, location or
description (not case-sensitive). Each result has a `search_only` true/false
field.

- **The search box must call the backend.** If the frontend only filters the
  list it already loaded, it will never see the search-only firms. Make the
  search box request `/directory?q=<what was typed>`, and show the normal
  `/directory` list when it is empty.
- **Add or edit entries** in `_DIRECTORY_ENTRIES` at the top of the directory
  section in `app/database.py` (the last value, `True`, means search-only). New
  entries are added on the next start; an existing database is upgraded, and
  nothing is duplicated. Entries are matched by name, so to change an entry
  that is already in `zimlegal.db`, edit it there or delete `zimlegal.db` and
  restart (the forum posts and users in it would be lost too).
- The pasted firms have no street address or confirmed pro-bono arrangement,
  so they show the location "Harare" and are marked `paid` (no Pro Bono
  badge). Their ratings are a snapshot from the list, so check them before
  relying on a number.

## The Judgment Summarizer

Paste a judgment or court order (the whole thing, heading included) and get:

- **Case details** read from the heading: case number, parties, court, judge, date.
- **What the court decided**: the numbered order at the end ("It is ordered that:
  1. ... 2. ..."), or the verdict and sentence in a criminal case, plus a one-line
  headline such as "Guilty of murder; Sentence: 20 years imprisonment."
- **A short summary** and **key points**: what the case was about, the facts, the
  defence and the court's own findings.
- **Earlier cases the court referred to**, listed separately. Judgments quote other
  cases ("In S v Wairosi it was held that ..."); those are the law the court
  applied, never its decision, so they are kept out of the summary and the decision.
- **Laws referred to** (Acts with their chapter numbers) and the **legal words**
  in the judgment, which link to the Legal Dictionary for their meaning.

How it works:

- **With an AI provider** (Ollama or ChatGPT), the model writes the summary and the
  decision. A long judgment is first cut down to its main parts, so it is one request. The case details,
  cases cited and legal words are still read straight from the text.
- **Without AI**, `app/judgment.py` does it by rules: it sets the heading aside,
  sorts each sentence (facts, defence, findings, decision, quoted case) and takes the
  decision from the end of the judgment. It only reuses the judgment's own words.
- Either way, the result is saved to the Case Law library under the case name.

## The Document Wizard (templates, Word and PDF)

The wizard on the Draft page offers these documents (all in `app/documents.py`):

- **Affidavit (founding or opposing)**: numbered paragraphs, the deponent's
  signature and a Commissioner of Oaths section, kept together on one page.
- **Notice of Opposition**: with the address for service and the TO / AND TO
  lines for the Registrar (or Clerk of Court) and the Applicant.
- **Application for a Protection Order** (Domestic Violence Act): Magistrates
  Court only -- if another court is chosen, it is prepared for the Magistrates
  Court and the person is told why.
- **Summons, Court Application, Notice of Appeal**: the original simple layout.

Each template lists its questions, so the wizard builds its form from
`GET /documents/templates`: **add a template to `TEMPLATES` in
`app/documents.py` and it appears in the wizard** with no frontend change.
Every document comes with warnings shown above the preview (e.g. sign an
affidavit only in front of a Commissioner of Oaths; deadlines for opposing).

**Downloads.** "Download Word" and "Download PDF" call `/documents/download`,
which rebuilds the document on the server and returns a `.docx` or `.pdf`
(A4, Times 12 pt, 1.5 spacing, page numbers on the PDF). Both are written by
`app/doc_export.py` using only Python's standard library -- nothing to install.

**Legal review is required.** These templates are drafting aids that follow
the general shape of Zimbabwean court documents; they are not official
court forms. Have a registered legal practitioner review them before real use.

## The Legal Dictionary

Type any word or short phrase ("affidavit", "ex parte", "garnishee order") and
get its meaning in plain English, how courts use it, an example, and related
words to look up next.

- **With Ollama on** (`AI_PROVIDER=ollama`), any word can be looked up: the
  model is asked for the meaning (see `call_define()` in
  `app/ollama_provider.py`). ChatGPT works the same way with `AI_PROVIDER=openai`.
- **Every AI answer is saved** in the `dictionary_cache` table, so a word is
  only ever asked once -- the second person to look it up gets it instantly.
  Words the model says it doesn't know are not saved. After switching to a
  better model, empty the cache with `python3 -c "from app import database; database.clear_dictionary_cache()"`.
- **Without AI, or if Ollama isn't running**, the app's own glossary
  (`LEGAL_TERMS` in `app/legal_content.py`) answers instead, and unknown words
  get "Did you mean...?" suggestions. Adding a line to `LEGAL_TERMS` adds it to
  the dictionary too.
- When a word is in the glossary, an AI answer also shows the glossary's
  meaning and source next to it, and every AI answer carries a note that it
  isn't legal advice. `engine` says which one answered (`ai:ollama`,
  `ai:openai` or `glossary`).
- Only words and short phrases are accepted (letters, up to 6 words / 60
  characters); anything else gets a 400 with a message pointing to the Judgment
  Summarizer.

**On the page:** the Dictionary card on the Education page of
`frontend/selfactorshub-frontend.html` opens a search box. Typing filters the
built-in word list (with Shona and Ndebele words) instantly; pressing Search
calls `GET /dictionary?q=...` and shows the meaning, how courts use it, an
example and related words (click one to look it up). If the word is also in the
built-in list, its meaning and translations are shown underneath. If the
backend can't be reached, words from the built-in list still work.

## The chatbot's knowledge base

The chatbot answers **IECMS questions only (the Integrated Electronic Case
Management System), and only from files you put in the `knowledge_base/`
folder**. It has no built-in answers and never uses outside knowledge. If your
files don't cover a question, it says so instead of guessing.

1. Put your IECMS material in `knowledge_base/` as `.md` or `.txt` files you
   write (see `knowledge_base/README.md` and `_EXAMPLE_format.md` for the
   format), as **Word files (.docx)** -- paragraphs styled Heading 1 / Heading 2
   split the document into sections, so give each question or topic its own
   heading -- and/or as **PDF files** (needs `pip install pypdf`; text PDFs only,
   not scans). Old `.doc` files can't be read: open them in Word and Save As
   `.docx`. Any file that is skipped is named in the terminal at start-up. Answers from a PDF name the page, e.g. "IECMS User Guide,
   page 12".
2. Restart the backend. It prints `Knowledge base: N entries loaded`.
3. Ask the chatbot a question.

How it answers:
- **The knowledge base is searched first, always.** If no passage matches the
  question, the chatbot replies that it can only answer IECMS questions from
  its guidance (`grounded: false`) and the AI is never called.
- **With AI on:** only the matching passages are handed to the model, with an
  instruction to answer from them alone. If the model says the passages don't
  answer the question, you get the same "can only answer IECMS questions"
  reply, and the sources shown are limited to your own files.
- **With AI off (or if the AI call fails):** the chatbot shows the best-matching
  passage from your files, with its source.
- A passage counts as a match when it shares at least `CHAT_MIN_OVERLAP`
  (default 2) meaningful words with the question (see `app/config.py`). Raise
  it if the chatbot answers things it shouldn't; lower it to 1 if it refuses
  questions your files really do cover.

**Friendly and flexible (no AI needed).** `app/conversation.py` handles the
chatter around the questions, so the chatbot doesn't feel like a form:
- "hie", "hello", "good morning", "mhoro", "how are you", "thanks", "bye",
  "who are you" and "what can you do" get a natural reply. The first one is
  the welcome message: *"Welcome to the Self Actors Hub ZW, Tendai! … How can
  I assist you today?"* (the first name comes from the `user` the frontend
  sends with the message; with no name it just says "Welcome to the Self
  Actors Hub ZW!"). Change the name with `APP_NAME` in `app/config.py` and the
  wording in `app/conversation.py`.
- A greeting in front of a question is ignored: "hi, how do I log in?" is
  answered as "how do I log in?".
- Small talk never searches the knowledge base or calls the AI, and the
  response carries `"kind": "greeting"` (or `thanks`, `goodbye`, ...) and
  `"engine": "conversation"` so the frontend can treat it differently.
- Everyday words match the guide's wording through the `SYNONYMS` list in
  `app/retrieval.py` ("register" finds "sign up"/"registration", "pay" finds
  "payment", "forgot my password" finds "Recovering Your Password", "sign in"
  means log in). Add your own when you see a question go unanswered.
- A short "what is X?" question prefers the introduction/overview of X, and
  "what does X stand for?" prefers the list of abbreviations.

Check what a PDF produced before relying on it:
`python -m app.knowledge "knowledge_base/Some Act.pdf"` prints what the
chatbot would learn from it. Tables and multi-column layouts read poorly.

The search matches the *words* in the question to the words in your files (it
does not understand meaning), so write headings like the questions people ask
and use the everyday words they use. The chatbot is only as accurate as the
files you give it, so put only material you have checked, and always fill in
the `Source:` line.

## Optional: AI providers (ChatGPT or Ollama)

By default, `/simplify` and `/chat` use the offline
rule-based logic in `app/legal_content.py`. You can optionally have them
call a real AI model instead — pick **one** of two providers with a single
environment variable. The rest of the app (routes, database, auth,
frontend) doesn't need to change either way.

```bash
export AI_PROVIDER=openai   # or: export AI_PROVIDER=ollama
```

### Option A: ChatGPT (OpenAI)

```bash
pip install openai                       # the ChatGPT library (Ollama doesn't need it)
export AI_PROVIDER=openai
export OPENAI_API_KEY="sk-..."           # your real key from platform.openai.com
python3 run.py
```

- Needs an internet connection and a paid API key (small per-request cost).
- Whatever text is submitted — which may be a real, sensitive legal
  document — is sent to OpenAI's servers.
- Generally the strongest answers of the two options.

### Option B: Ollama (runs locally, on your own machine)

```bash
# 1. Install Ollama from https://ollama.com and make sure it's running
# 2. Download a model once (only needs to happen once):
ollama pull llama3.2
# 3. Turn it on for this app -- no pip install needed at all:
export AI_PROVIDER=ollama
python3 run.py
```

- **No pip install, no API key, no cost, no internet needed** (after the
  one-time model download) — `app/ollama_provider.py` talks to Ollama's
  local HTTP server using only Python's built-in `urllib`.
- **The text people submit never leaves your machine** — the strongest
  privacy option here, which matters given this app handles real legal
  documents.
- Quality and speed depend on your own hardware and which model you pull
  (`llama3.2` is a reasonable, fairly small default — see
  [ollama.com/library](https://ollama.com/library) for other options; a
  larger model generally answers better but runs slower on modest hardware).
- Override the defaults if needed: `OLLAMA_BASE_URL` (default
  `http://localhost:11434`) and `OLLAMA_MODEL` (default `llama3.2`).

**Summarizing long judgments with Ollama.** Small local models are slow on an
ordinary laptop, so the Ollama provider keeps every summary to ONE request:
- A judgment longer than `OLLAMA_CHUNK_CHARS` (default 4500 characters) is first
  cut down by `judgment.digest()` to the parts that matter -- case details, what
  the case is about, facts, defence, the court's findings and the FULL decision.
  Quoted cases are left out. (Sending a long judgment part by part took many
  minutes, because every part was a separate request.)
- The answer is capped at `OLLAMA_SUMMARY_MAX_TOKENS` (default 500) and the
  context window at `OLLAMA_NUM_CTX` (default 4096), which keeps it quick.
- If the model takes longer than `OLLAMA_SUMMARY_TIMEOUT` (default 90 seconds),
  the rule-based summary is shown instead, so nobody waits forever. Raise it on a
  very slow computer, e.g. `$env:OLLAMA_SUMMARY_TIMEOUT = "300"`.
- The chatbot answers straight from the knowledge base: it shows the
  matching passage at once and does not ask the AI model. To let llama3.2
  reword the passages instead, set `$env:CHAT_USE_AI = "1"` (slower).
- The Judgment Summarizer shows nothing until the summary is finished, then
  the whole result at once.
- Summonses: **Civil Summons** (Magistrates Court, Harare, 7 days to enter
  appearance, the claim as (1) amount, (2) interest, (3) costs) and **Divorce
  Summons** (High Court, Harare, 10 days, decree of divorce, custody,
  maintenance, division of property, costs). The Declaration / particulars of
  claim is attached separately. Names accept letters only (no numbers) and
  amounts accept numbers only.
- Speed: the model is pre-loaded when the backend starts and kept in memory
  for `OLLAMA_KEEP_ALIVE` (default 30m). The chatbot (`POST /chat/stream`) and
  the dictionary (`GET /dictionary/stream`) send llama3.2's answer word by word
  as it is written, so people start reading after a second or two. A word's
  meaning is saved after the first look-up, so the next time it is instant.
  If no words arrive for `OLLAMA_CHAT_TIMEOUT` (60 s) / `OLLAMA_DEFINE_TIMEOUT`
  (40 s), the knowledge-base passage / glossary meaning is shown instead.
- The Judgment Summarizer (`POST /simplify/stream`) shows the case details,
  key points and the court's decision AT ONCE (read straight from the
  judgment), then llama3.2 writes a short plain-English summary into it word
  by word. The model only reads the main parts of the judgment
  (`OLLAMA_SUMMARY_INPUT_CHARS`, 2500) and writes one short paragraph
  (`OLLAMA_SUMMARY_STREAM_TOKENS`, 220), which is what keeps it quick.
- On a slow laptop, `ollama pull llama3.2:1b` and `$env:OLLAMA_MODEL = "llama3.2:1b"`
  give the smaller llama3.2, about twice as fast.
- Phone numbers in the document forms take digits only, with an optional +
  at the start; national ID numbers must look like 63-123456 A 75. Both are
  checked in the browser and again on the server.
- All court documents are prepared for the Harare courts (the area of the
  study), so the town is not asked for.
- Expect it to be slow: a long judgment can take a few minutes on a laptop
  CPU, and the first request after starting Ollama is slower still while
  the model loads.

### What both options have in common

The AI-eligible endpoints (`/simplify`, `/chat` and `/dictionary`) now
include an `"engine"` field in their response — `"ai:openai"`,
`"ai:ollama"`, or `"rule_based"` — so you can see which one actually
answered.

**Chat also does retrieval (the "R" in RAG)** before calling either
provider: `app/retrieval.py` does a lightweight keyword search over your
knowledge base and hands only what it finds to the AI model as the material
to answer from — so an AI-mode chat answer is based on your files, not on
what the model already knew. This
uses simple keyword overlap rather than embeddings/a vector database on
purpose (see the comments at the top of `retrieval.py` for why, and when
it'd be worth upgrading).

**What happens if something goes wrong** (expired key, no internet, Ollama
not running, unexpected response): the request automatically falls back
to the offline rule-based version instead of failing — see
`_run_simplify()` / `_run_chat()` in `app/server.py`.
Nothing breaks for the person using the app; check your terminal for a
one-line log message when this happens.

**To turn AI mode back off:** unset `AI_PROVIDER` (or don't set it in a
new terminal) and restart.

**Adding a third provider later:** write a module with the same four
functions as `app/ai_provider.py`/`app/ollama_provider.py`
(`is_configured()`, `call_simplify()`, `call_chat()`, `call_define()`,
raising `app.ai_errors.AIProviderError` on failure), then add one line to
the `_PROVIDERS` dict in `app/server.py`. Nothing else needs to change.

## Things to change before real users touch this

1. **`SECRET_KEY` in `app/config.py`** — currently a placeholder string.
   Anyone who knows it could forge login tokens. Set it from an environment
   variable to a long random value.
2. **Case law library in `app/database.py`** — starts empty and fills up
   only from real, grounded submissions to `/simplify` (see
   `create_case_law_entry()`). Since anyone can submit text, it's worth
   having a Zimbabwean legal practitioner spot-check the library
   periodically before treating it as reliable reference material.
3. **CORS** — currently wide open (`Access-Control-Allow-Origin: *`) to
   make local development easy. Once you know which domain the frontend
   will be served from, restrict it in `app/server.py`.
4. **Legal review** — the document templates, the plain-language legal
   dictionary, and the simplifier's outcome-detection phrases in
   `app/legal_content.py`, plus everything you put in `knowledge_base/`,
   should be checked by a Zimbabwean legal practitioner before this is used
   for real cases.
5. **Deployment** — `python3 run.py` is fine for local testing. For a real
   deployment, run it behind a process manager and a reverse proxy (e.g.
   nginx) that handles HTTPS.

## Extending it

- **Add a plain-language term:** add a line to `LEGAL_TERMS` in
  `app/legal_content.py` (used by the simplifier, with or without AI).
- **Teach the chatbot something:** add or edit a file in `knowledge_base/` and restart (see above). Change the out-of-scope reply with `OUT_OF_SCOPE_RESPONSE` in `app/legal_content.py`.
- **Add a document template variant:** extend `generate_document()`.
- **Improve the offline simplifier:** without AI, `simplify_judgment()` picks
  the most important sentences of a judgment (`summarize_judgment()` -- it
  never invents wording) and explains each legal term the first time it
  appears. It spots the court's order using `OUTCOME_PHRASES` /
  `PRIMARY_OUTCOME_PHRASES`; add more phrasing you see in real judgments to
  improve it.
- **Swap in a real AI model later:** the document generation handler is
  the one remaining feature that's rule-based only. Chat, simplification,
  and the simplifier already support this — via `app/ai_provider.py`
  (ChatGPT) and `app/ollama_provider.py` (local Ollama), selected with
  `AI_PROVIDER` (see "Optional: AI providers" above). Use either file as
  a template for wiring up document generation the same way, or for
  adding a third provider.
