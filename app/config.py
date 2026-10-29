"""
config.py
---------
All the "knobs" for this backend live here, in one place, so you never have
to go hunting through the code to change something.
"""

import os

# Folder this file lives in (used to build absolute paths below).
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Where the SQLite database file is stored. SQLite stores an entire database
# as a single file on disk -- no separate database server to install or run.
DATABASE_PATH = os.path.join(BASE_DIR, "zimlegal.db")

# Used to sign login tokens so we can tell if one has been tampered with.
# CHANGE THIS before putting the app in front of real users -- anyone who
# knows this value could forge a login token. A good approach is to read it
# from an environment variable instead of hardcoding it:
#   SECRET_KEY = os.environ.get("ZIMLEGAL_SECRET_KEY", "dev-only-fallback")
SECRET_KEY = os.environ.get("ZIMLEGAL_SECRET_KEY", "change-this-to-a-long-random-string-before-deploying")

# How long a login token stays valid, in seconds. 24 hours.
TOKEN_LIFETIME_SECONDS = 24 * 60 * 60

# Folder of files the chatbot reads its answers from (see app/knowledge.py and
# the README inside that folder). Add .md or .txt files and restart.
KNOWLEDGE_DIR = os.path.join(BASE_DIR, "knowledge_base")

# Long files are cut into entries of about this many characters, so a question
# is matched against a focused passage rather than a whole Act.
KNOWLEDGE_CHUNK_CHARS = 900

# The chatbot answers IECMS questions ONLY, and ONLY from the knowledge base.
# A passage must share at least this many of the question's meaningful words
# to count as an answer (a one-word question needs just that one word). Raise
# it if the chatbot answers things it shouldn't; lower it to 1 if it refuses
# questions your files really do cover.
CHAT_MIN_OVERLAP = 2

# The name the chatbot welcomes people to ("Welcome to the Self Actors Hub ZW, ...").
APP_NAME = os.environ.get("APP_NAME", "Self Actors Hub ZW")

# Host/port the server listens on. The frontend's CONFIG.API_BASE_URL is
# 'http://localhost:8000', so we match that by default.
HOST = "0.0.0.0"
# Hosting services (e.g. Render) say which port to use in the PORT variable.
PORT = int(os.environ.get("PORT", "8000"))

# ---------------------------------------------------------------------------
# Optional AI upgrade: choose a provider, or leave everything offline
# ---------------------------------------------------------------------------
# The Judgment Summarizer, Judgment Summarizer, and IECMS Chat all work
# entirely offline with plain rule-based Python by default (see
# app/legal_content.py) -- no key needed, nothing to install, nothing to
# pay for. Setting AI_PROVIDER turns on an *optional* upgrade: those three
# features call the chosen AI provider instead, and automatically fall
# back to the offline rule-based version if the call ever fails.
#
#   AI_PROVIDER=openai  -> uses ChatGPT (see app/ai_provider.py) -- needs
#                           `pip install openai` and OPENAI_API_KEY
#   AI_PROVIDER=ollama  -> uses a local Ollama server (see
#                           app/ollama_provider.py) -- needs Ollama
#                           installed and running, no pip install, no key
#   (unset / anything else) -> rule-based only (the default)
AI_PROVIDER = os.environ.get("AI_PROVIDER", "").strip().lower()

# --- ChatGPT (OpenAI) settings, used when AI_PROVIDER=openai ---
# Set this as a real environment variable rather than editing this file,
# so your key never ends up committed to source control:
#   macOS/Linux:   export OPENAI_API_KEY="sk-..."
#   Windows (cmd): set OPENAI_API_KEY=sk-...
#   PowerShell:    $env:OPENAI_API_KEY = "sk-..."
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

# --- Ollama (local LLM) settings, used when AI_PROVIDER=ollama ---
# The default matches where Ollama listens out of the box -- only change
# this if you've moved it to a different host/port.
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:1b")  # the smaller, faster llama3.2

# How long to wait for Ollama to answer, in seconds. Long judgments on a
# laptop CPU can take minutes, so this is generous.
OLLAMA_TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", "300"))
# Context window handed to the model. Ollama's own default is small and it
# silently cuts off anything longer, so we ask for more.
OLLAMA_NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX", "2048"))
# A judgment longer than this many characters is cut down to its main parts
# (case details, facts, findings, the full decision) before it is sent, so
# the summary always takes a single request.
OLLAMA_CHUNK_CHARS = int(os.environ.get("OLLAMA_CHUNK_CHARS", "3000"))
# The Judgment Summarizer waits at most this long (seconds) for the model,
# then shows the rule-based summary instead. Raise it on a slow computer.
OLLAMA_SUMMARY_TIMEOUT = int(os.environ.get("OLLAMA_SUMMARY_TIMEOUT", "120"))
# Longest summary the model may write (in tokens); shorter is faster.
OLLAMA_SUMMARY_MAX_TOKENS = int(os.environ.get("OLLAMA_SUMMARY_MAX_TOKENS", "500"))
# The chatbot and the dictionary write short answers, so they are capped
# lower and give up sooner (then show the knowledge-base passage / glossary).
OLLAMA_CHAT_TIMEOUT = int(os.environ.get("OLLAMA_CHAT_TIMEOUT", "45"))
OLLAMA_CHAT_MAX_TOKENS = int(os.environ.get("OLLAMA_CHAT_MAX_TOKENS", "300"))
# The chatbot answers straight from the knowledge base (the matching passage,
# instantly) and does NOT ask the AI model. Set CHAT_USE_AI=1 to have the
# model reword the passages instead (slower on a laptop).
CHAT_USE_AI = os.environ.get("CHAT_USE_AI", "0").strip().lower() in ("1", "true", "yes", "on")
# How many knowledge-base passages the chatbot hands the model (fewer = faster).
CHAT_AI_PASSAGES = int(os.environ.get("CHAT_AI_PASSAGES", "2"))
OLLAMA_DEFINE_TIMEOUT = int(os.environ.get("OLLAMA_DEFINE_TIMEOUT", "30"))
OLLAMA_DEFINE_MAX_TOKENS = int(os.environ.get("OLLAMA_DEFINE_MAX_TOKENS", "220"))
# The streamed Judgment Summarizer: how much of the judgment the model reads
# (a long one is cut down to its main parts) and how long its paragraph may be.
OLLAMA_SUMMARY_INPUT_CHARS = int(os.environ.get("OLLAMA_SUMMARY_INPUT_CHARS", "2500"))
OLLAMA_SUMMARY_STREAM_TOKENS = int(os.environ.get("OLLAMA_SUMMARY_STREAM_TOKENS", "220"))
# How long Ollama keeps the model in memory after a request ("30m", "-1" = always).
OLLAMA_KEEP_ALIVE = os.environ.get("OLLAMA_KEEP_ALIVE", "30m")
