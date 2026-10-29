#!/usr/bin/env python3
"""
test_backend.py
----------------
This test suite starts a REAL copy of the server on a test port, then acts
like the frontend would: it makes real HTTP requests to every endpoint and
checks the responses. This proves each component actually works together
(server + database + auth + business logic), not just in isolation.

Run it with:
    python3 tests/test_backend.py
"""

import json
import sys
import os
import time
import threading
import unittest
import unittest.mock
import tempfile
import shutil
import urllib.request
import urllib.error
import urllib.parse

# Make sure "app" is importable when running this file directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import database
from app import ai_provider
from app import ollama_provider
from app import textutil
from app import knowledge
from app import retrieval
from app import conversation
from app import dictionary
from app import config
from app.server import run_server
from app.config import DATABASE_PATH

import importlib.util

# PDF tests need pypdf (to read) and reportlab (to make test PDFs). If either
# is missing on this machine, those tests are skipped rather than failing.
HAVE_PDF_TOOLS = bool(importlib.util.find_spec("pypdf") and importlib.util.find_spec("reportlab"))


def make_test_pdf(path, pages, header=None, footer=False):
    """Writes a PDF where each entry of `pages` is a list of paragraphs. Text
    is wrapped to a fixed width like a real PDF, so each visual line is
    separate text and paragraph gaps are only spacing (no blank text lines)."""
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase.pdfmetrics import stringWidth

    c = canvas.Canvas(path, pagesize=A4)
    width, height = A4
    for number, paragraphs in enumerate(pages, start=1):
        if header:
            c.setFont("Helvetica", 8)
            c.drawString(70, height - 40, header)
        y = height - 80
        c.setFont("Helvetica", 10)
        for paragraph in paragraphs:
            line, lines = "", []
            for word in paragraph.split():
                trial = (line + " " + word).strip()
                if stringWidth(trial, "Helvetica", 10) <= width - 140:
                    line = trial
                else:
                    lines.append(line)
                    line = word
            lines.append(line)
            for text_line in lines:
                c.drawString(70, y, text_line)
                y -= 14
            y -= 8
        if footer:
            c.setFont("Helvetica", 8)
            c.drawString(width / 2 - 20, 40, f"Page {number}")
        c.showPage()
    c.save()


TEST_PORT = 8123
BASE_URL = f"http://127.0.0.1:{TEST_PORT}"


def call(method, path, body=None, token=None, expect_status=None):
    """Small helper to make a real HTTP request and parse the JSON response."""
    url = BASE_URL + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            status = resp.status
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        status = e.code
        payload = json.loads(e.read().decode("utf-8"))

    if expect_status is not None and status != expect_status:
        raise AssertionError(f"{method} {path} -> expected {expect_status}, got {status}: {payload}")
    return status, payload


def call_stream(method, path, body=None):
    """Reads a streaming endpoint: returns the list of JSON events."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE_URL + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.headers["Content-Type"].startswith("application/x-ndjson")
        return [json.loads(line) for line in resp.read().decode("utf-8").splitlines() if line.strip()]


class TestZimLegalBackend(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Run against a throwaway database and an empty knowledge folder, so the
        # tests never touch your real zimlegal.db or the files you have put in
        # knowledge_base/ -- and so what you add there can't change the results.
        cls._scratch = tempfile.mkdtemp()
        os.makedirs(os.path.join(cls._scratch, "knowledge_base"))
        cls._patches = [
            unittest.mock.patch.object(database, "DATABASE_PATH", os.path.join(cls._scratch, "test.db")),
            unittest.mock.patch.object(config, "KNOWLEDGE_DIR", os.path.join(cls._scratch, "knowledge_base")),
        ]
        for patcher in cls._patches:
            patcher.start()
        # Start from a completely clean database so tests are repeatable.
        database.reset_db_for_tests()
        cls.server_thread = threading.Thread(
            target=run_server, args=("127.0.0.1", TEST_PORT), daemon=True
        )
        cls.server_thread.start()
        # Give the server a moment to start listening.
        for _ in range(50):
            try:
                call("GET", "/health", expect_status=200)
                break
            except Exception:
                time.sleep(0.1)
        else:
            raise RuntimeError("Server did not start in time")

    @classmethod
    def tearDownClass(cls):
        for patcher in cls._patches:
            patcher.stop()
        shutil.rmtree(cls._scratch, ignore_errors=True)

    # ---- health -----------------------------------------------------

    def test_health(self):
        status, payload = call("GET", "/health", expect_status=200)
        self.assertEqual(payload["status"], "ok")

    # ---- auth ---------------------------------------------------------

    def test_register_then_login(self):
        status, payload = call("POST", "/auth/register", {
            "full_name": "Tendai Moyo",
            "email": "tendai@example.com",
            "password": "supersecret123",
        }, expect_status=201)
        self.assertIn("access_token", payload)
        self.assertEqual(payload["user"]["email"], "tendai@example.com")

        # Duplicate registration should fail
        call("POST", "/auth/register", {
            "full_name": "Tendai Moyo",
            "email": "tendai@example.com",
            "password": "supersecret123",
        }, expect_status=409)

        # Correct login
        status, payload = call("POST", "/auth/login", {
            "email": "tendai@example.com",
            "password": "supersecret123",
        }, expect_status=200)
        self.assertIn("access_token", payload)

        # Wrong password
        call("POST", "/auth/login", {
            "email": "tendai@example.com",
            "password": "wrong-password",
        }, expect_status=401)

        # Missing fields
        call("POST", "/auth/register", {"email": "x@example.com"}, expect_status=400)

    def test_password_is_never_stored_in_plaintext(self):
        # Sanity check on the underlying storage, not just the API surface.
        call("POST", "/auth/register", {
            "full_name": "Privacy Check",
            "email": "privacy@example.com",
            "password": "hunter2hunter2",
        }, expect_status=201)
        user = database.get_user_by_email("privacy@example.com")
        self.assertNotEqual(user["password_hash"], "hunter2hunter2")
        self.assertGreater(len(user["password_hash"]), 20)

    # ---- chat -----------------------------------------------------------

    def test_chat_has_no_built_in_answers_when_the_knowledge_base_is_empty(self):
        # The chatbot answers ONLY from knowledge_base/, so with nothing loaded
        # even a question it used to have a canned answer for is refused.
        status, payload = call("POST", "/chat", {
            "message": "How do I get a protection order?",
            "user": None,
        }, expect_status=200)
        self.assertFalse(payload["grounded"])
        self.assertEqual(payload["sources"], [])
        self.assertIn("IECMS", payload["response"])

    def test_chat_fallback_reply(self):
        status, payload = call("POST", "/chat", {
            "message": "asdkjfhaslkdjfh nonsense",
            "user": None,
        }, expect_status=200)
        self.assertFalse(payload["grounded"])
        self.assertIn("IECMS", payload["response"])

    def test_chat_requires_message(self):
        call("POST", "/chat", {"message": "   "}, expect_status=400)

    # ---- document generation --------------------------------------------

    def test_generate_document(self):
        status, payload = call("POST", "/documents/generate", {
            "document_type": "Application for Protection Order",
            "court_tier": "magistrates_court",
            "case_details": {
                "applicant_name": "Jane Doe",
                "respondent_name": "John Doe",
                "relief_sought": "An order restraining the respondent from contacting the applicant.",
            },
        }, expect_status=200)
        self.assertIn("MAGISTRATES COURT OF ZIMBABWE", payload["content"])
        self.assertIn("JANE DOE", payload["content"])
        self.assertIn("Magistrates Court Act", " ".join(payload["sources_used"]))
        self.assertTrue(len(payload["warnings"]) >= 1)

    def test_generate_document_missing_fields_still_succeeds_with_placeholders(self):
        status, payload = call("POST", "/documents/generate", {
            "document_type": "Application",
            "court_tier": "high_court",
            "case_details": {},
        }, expect_status=200)
        self.assertIn("[APPLICANT NAME]", payload["content"])
        self.assertTrue(any("blank" in w for w in payload["warnings"]))

    # ---- simplify ---------------------------------------------------------

    def test_simplify_known_terms(self):
        status, payload = call("POST", "/simplify", {
            "text": "The plaintiff filed an affidavit and the defendant was served with summons.",
        }, expect_status=200)
        self.assertTrue(payload["grounded"])
        # The summarizer lists the legal words for the Dictionary instead of
        # writing explanations into the summary.
        self.assertIn("plaintiff", payload["legal_terms"])
        self.assertIn("affidavit", payload["legal_terms"])
        self.assertNotIn("[", payload["simplified_text"])
        self.assertGreater(len(payload["citations"]), 0)

    def test_simplify_requires_text(self):
        call("POST", "/simplify", {"text": ""}, expect_status=400)

    def test_simplify_saves_to_case_law_library(self):
        # A grounded simplification should show up afterwards in /case-law,
        # so the frontend's "Case Law -- simplified from real submissions"
        # list actually reflects what people simplify.
        _, before = call("GET", "/case-law", expect_status=200)
        before_count = len(before)

        call("POST", "/simplify", {
            "text": "The respondent failed to file an affidavit before judgment.",
        }, expect_status=200)

        _, after = call("GET", "/case-law", expect_status=200)
        self.assertEqual(len(after), before_count + 1)
        self.assertIn("respondent failed to file an affidavit", after[0]["title"])

    def test_ungrounded_simplify_is_not_saved(self):
        _, before = call("GET", "/case-law", expect_status=200)
        before_count = len(before)

        call("POST", "/simplify", {"text": "just some ordinary sentence with no legal terms"}, expect_status=200)

        _, after = call("GET", "/case-law", expect_status=200)
        self.assertEqual(len(after), before_count)

    # ---- the merged Jargon Simplifier: short plain-English summary of a judgment ----

    JUDGMENT = (
        "This is an application for a protection order brought by the applicant against the respondent. "
        "The applicant testified that the respondent had threatened her on several occasions. "
        "The respondent denied the allegations and raised a point in limine regarding service. "
        "Accordingly, it is ordered that the respondent is interdicted from contacting the applicant. "
        "Costs shall be in the cause."
    )

    def test_simplify_gives_summary_key_points_and_outcome(self):
        status, payload = call("POST", "/simplify", {"text": self.JUDGMENT}, expect_status=200)
        self.assertTrue(payload["grounded"])
        self.assertEqual(payload["engine"], "rule_based")
        self.assertGreater(len(payload["key_points"]), 0)
        # the court's order is pulled out, without the "Accordingly," lead-in
        self.assertIn("interdicted", payload["outcome"])
        self.assertTrue(payload["decision"][0].startswith("The respondent is interdicted"))
        self.assertIn("interdicted", payload["simplified_text"])
        self.assertIn("in limine", payload["legal_terms"])
        self.assertGreater(len(payload["citations"]), 0)

    def test_simplify_lists_default_judgment_as_a_whole_phrase(self):
        _, payload = call("POST", "/simplify", {
            "text": "The court granted a default judgment against the defendant."}, expect_status=200)
        self.assertIn("default judgment", payload["legal_terms"])
        self.assertNotIn("judgment", payload["legal_terms"])

    CRIMINAL = (
        "HH 293-26\nHCHCR 6795/25\n\nTHE STATE\nversus\nFANUEL MASORA\n\nHIGH COURT OF ZIMBABWE\n"
        "MUCHAWA J\nHARARE: 2 February & 4 March 2026\n\nMrs K Chigwedere, for the state\n"
        "Mr O Shava, for the defence\n\nIntroduction\n"
        "The accused is charged with the murder of Magijo Masora as defined in s 47 (1) of the Criminal Law "
        "(Codification and Reform) Act [Chapter 9:23]. The accused pleaded not guilty to the charge. "
        "In the case of S v Wairosi HCM 57/27 it was held that the weapon used and the severity of the attack "
        "show actual intention to kill. We find that the accused struck the deceased three times with an axe. "
        "We are satisfied that the State has proved beyond reasonable doubt that he intended to kill. "
        "Accordingly, the accused is found guilty of murder as charged. "
        "In the result, the accused is sentenced to 20 years imprisonment."
    )

    def test_judgment_summarizer_reads_case_details_from_the_heading(self):
        _, payload = call("POST", "/simplify", {"text": self.CRIMINAL}, expect_status=200)
        details = payload["case_details"]
        self.assertEqual(details["case_number"], "HH 293-26")
        self.assertEqual(details["parties"], "THE STATE v FANUEL MASORA")
        self.assertEqual(details["court"], "HIGH COURT OF ZIMBABWE")
        self.assertEqual(details["judge"], "Muchawa J")
        # the heading (counsel's names etc.) is not part of the summary
        self.assertNotIn("Chigwedere", payload["simplified_text"])
        self.assertFalse(any("for the defence" in p for p in payload["key_points"]))

    def test_judgment_summarizer_decision_is_this_courts_not_a_quoted_case(self):
        _, payload = call("POST", "/simplify", {"text": self.CRIMINAL}, expect_status=200)
        self.assertEqual(payload["decision"], [
            "The accused is found guilty of murder as charged.",
            "The accused is sentenced to 20 years imprisonment.",
        ])
        self.assertNotIn("Wairosi", payload["outcome"])
        self.assertFalse(any("Wairosi" in p for p in payload["key_points"]))
        self.assertIn("S v Wairosi", payload["cases_cited"])
        self.assertEqual(payload["headline"], "Guilty of murder; Sentence: 20 years imprisonment.")
        self.assertIn("Criminal Law (Codification and Reform) Act [Chapter 9:23]", payload["citations"])

    def test_judgment_summarizer_reads_a_numbered_order(self):
        text = ("The applicant seeks an order declaring the demolition of her house unlawful. "
                "The respondent opposed the application. The court is satisfied that the demolition was unlawful. "
                "In the result, it is ordered that: 1. The demolition of the applicant's house is declared unlawful. "
                "2. The respondent shall pay the applicant's costs.")
        _, payload = call("POST", "/simplify", {"text": text}, expect_status=200)
        self.assertEqual(payload["decision"], [
            "The demolition of the applicant's house is declared unlawful.",
            "The respondent shall pay the applicant's costs.",
        ])

    def test_simplify_without_legal_content_has_no_outcome(self):
        _, payload = call("POST", "/simplify", {
            "text": "This is just a short note with no legal terms and no ruling in it at all."}, expect_status=200)
        self.assertIsNone(payload["outcome"])
        self.assertFalse(payload["grounded"])

    def test_old_summarize_endpoint_is_gone(self):
        # Simplify and summarize are one feature now.
        call("POST", "/summarize", {"text": "anything"}, expect_status=404)

    # ---- optional AI provider integration (mocked -- no real network/key needed) ----
    # These don't call the real OpenAI or Ollama APIs. Instead they set
    # config.AI_PROVIDER and simulate the provider module to prove the
    # *plumbing* is correct: when a provider is selected, it's used and
    # tagged as such; when it fails, the app falls back to the rule-based
    # engine instead of breaking; and a different provider selection
    # routes to a different module.

    def test_simplify_uses_openai_when_configured(self):
        fake_ai_result = {
            "simplified_text": "AI-SIMPLIFIED VERSION",
            "citations": ["Some Act"],
            "grounded": True,
        }
        with unittest.mock.patch.object(config, "AI_PROVIDER", "openai"), \
             unittest.mock.patch.object(ai_provider, "is_configured", return_value=True), \
             unittest.mock.patch.object(ai_provider, "call_simplify", return_value=fake_ai_result):
            status, payload = call("POST", "/simplify", {"text": "The plaintiff filed an affidavit."}, expect_status=200)
        self.assertEqual(payload["engine"], "ai:openai")
        self.assertEqual(payload["simplified_text"], "AI-SIMPLIFIED VERSION")

    def test_simplify_uses_ollama_when_configured(self):
        fake_ai_result = {
            "simplified_text": "OLLAMA-SIMPLIFIED VERSION",
            "citations": ["Some Act"],
            "grounded": True,
        }
        with unittest.mock.patch.object(config, "AI_PROVIDER", "ollama"), \
             unittest.mock.patch.object(ollama_provider, "is_configured", return_value=True), \
             unittest.mock.patch.object(ollama_provider, "call_simplify", return_value=fake_ai_result):
            status, payload = call("POST", "/simplify", {"text": "The plaintiff filed an affidavit."}, expect_status=200)
        self.assertEqual(payload["engine"], "ai:ollama")
        self.assertEqual(payload["simplified_text"], "OLLAMA-SIMPLIFIED VERSION")

    def test_simplify_falls_back_when_ai_call_fails(self):
        with unittest.mock.patch.object(config, "AI_PROVIDER", "openai"), \
             unittest.mock.patch.object(ai_provider, "is_configured", return_value=True), \
             unittest.mock.patch.object(ai_provider, "call_simplify",
                                         side_effect=ai_provider.AIProviderError("simulated failure")):
            status, payload = call("POST", "/simplify", {"text": "The plaintiff filed an affidavit."}, expect_status=200)
        self.assertEqual(payload["engine"], "rule_based")
        self.assertIn("plaintiff", payload["legal_terms"])

    def test_simplify_falls_back_when_ollama_unreachable(self):
        # Simulates Ollama not actually running, e.g. AI_PROVIDER=ollama
        # but nobody started the local server -- this is the single most
        # likely real-world failure mode for this provider.
        with unittest.mock.patch.object(config, "AI_PROVIDER", "ollama"), \
             unittest.mock.patch.object(ollama_provider, "is_configured", return_value=True), \
             unittest.mock.patch.object(ollama_provider, "call_simplify",
                                         side_effect=ollama_provider.AIProviderError("Couldn't reach Ollama")):
            status, payload = call("POST", "/simplify", {"text": "The plaintiff filed an affidavit."}, expect_status=200)
        self.assertEqual(payload["engine"], "rule_based")
        self.assertIn("plaintiff", payload["legal_terms"])

    def test_simplify_passes_through_ai_key_points_and_outcome(self):
        fake_ai_result = {
            "simplified_text": "AI SUMMARY",
            "key_points": ["point one", "point two"],
            "outcome": "The court granted the application.",
            "citations": ["Some Act"],
            "grounded": True,
        }
        with unittest.mock.patch.object(config, "AI_PROVIDER", "ollama"), \
             unittest.mock.patch.object(ollama_provider, "is_configured", return_value=True), \
             unittest.mock.patch.object(ollama_provider, "call_simplify", return_value=fake_ai_result):
            status, payload = call("POST", "/simplify", {"text": "Some judgment text."}, expect_status=200)
        self.assertEqual(payload["engine"], "ai:ollama")
        self.assertEqual(payload["key_points"], ["point one", "point two"])
        self.assertEqual(payload["outcome"], "The court granted the application.")

    def test_simplify_tolerates_ai_result_without_optional_fields(self):
        bare = {"simplified_text": "Just the summary.", "citations": [], "grounded": False}
        with unittest.mock.patch.object(config, "AI_PROVIDER", "ollama"), \
             unittest.mock.patch.object(ollama_provider, "is_configured", return_value=True), \
             unittest.mock.patch.object(ollama_provider, "call_simplify", return_value=bare):
            status, payload = call("POST", "/simplify", {"text": "Some text."}, expect_status=200)
        self.assertEqual(payload["key_points"], [])
        self.assertIsNone(payload["outcome"])

    def test_ai_saying_not_legal_is_overruled_when_glossary_recognises_legal_terms(self):
        # A small local model may say grounded=false for a real judgment.
        # If our own glossary finds legal terms, it still counts as legal text.
        modest = {"simplified_text": "Summary.", "key_points": [], "outcome": None,
                  "citations": [], "grounded": False}
        with unittest.mock.patch.object(config, "AI_PROVIDER", "ollama"), \
             unittest.mock.patch.object(ollama_provider, "is_configured", return_value=True), \
             unittest.mock.patch.object(ollama_provider, "call_simplify", return_value=modest):
            _, legal = call("POST", "/simplify", {"text": "The respondent filed an affidavit."}, expect_status=200)
            _, plain = call("POST", "/simplify", {"text": "I like cooking rice on Sundays."}, expect_status=200)
        self.assertTrue(legal["grounded"])
        self.assertFalse(plain["grounded"])

    def test_chat_uses_ai_with_knowledge_base_passages(self):
        # Confirm the AI provider receives the retrieved knowledge-base text
        # (the R in RAG ran first) and that its answer is passed through.
        self._use_knowledge_files({"tuckshops.md": self.TUCKSHOP})
        captured = {}

        def fake_call_chat(message, retrieved_context_text=""):
            captured["context"] = retrieved_context_text
            return {"response": "Here's what I found.", "grounded": True, "sources": ["Test Source Act"]}

        with unittest.mock.patch.object(config, "AI_PROVIDER", "openai"), unittest.mock.patch.object(config, "CHAT_USE_AI", True), \
             unittest.mock.patch.object(ai_provider, "is_configured", return_value=True), \
             unittest.mock.patch.object(ai_provider, "call_chat", side_effect=fake_call_chat):
            status, payload = call("POST", "/chat", {"message": "How do I renew my tuckshop licence?"}, expect_status=200)

        self.assertEqual(payload["engine"], "ai:openai")
        self.assertEqual(payload["response"], "Here's what I found.")
        self.assertIn("proof of payment", captured["context"])

    def test_ai_is_never_asked_when_the_knowledge_base_has_nothing(self):
        # No match in the knowledge base -> the AI must not be called at all,
        # so it can't answer from its own general knowledge.
        self._use_knowledge_files({"tuckshops.md": self.TUCKSHOP})
        asked = []

        def fake_call_chat(message, retrieved_context_text=""):
            asked.append(message)
            return {"response": "from general knowledge", "grounded": True, "sources": []}

        with unittest.mock.patch.object(config, "AI_PROVIDER", "ollama"), unittest.mock.patch.object(config, "CHAT_USE_AI", True), \
             unittest.mock.patch.object(ollama_provider, "is_configured", return_value=True), \
             unittest.mock.patch.object(ollama_provider, "call_chat", side_effect=fake_call_chat):
            status, payload = call("POST", "/chat", {"message": "How do I get a protection order?"}, expect_status=200)

        self.assertEqual(asked, [])
        self.assertFalse(payload["grounded"])
        self.assertIn("IECMS", payload["response"])

    def test_ai_saying_the_passages_do_not_answer_gives_the_out_of_scope_reply(self):
        self._use_knowledge_files({"tuckshops.md": self.TUCKSHOP})

        def fake_call_chat(message, retrieved_context_text=""):
            return {"response": "I guess maybe...", "grounded": False, "sources": []}

        with unittest.mock.patch.object(config, "AI_PROVIDER", "ollama"), unittest.mock.patch.object(config, "CHAT_USE_AI", True), \
             unittest.mock.patch.object(ollama_provider, "is_configured", return_value=True), \
             unittest.mock.patch.object(ollama_provider, "call_chat", side_effect=fake_call_chat):
            status, payload = call("POST", "/chat", {"message": "How do I renew my tuckshop licence?"}, expect_status=200)

        self.assertFalse(payload["grounded"])
        self.assertIn("IECMS", payload["response"])
        self.assertEqual(payload["sources"], [])

    def test_ai_chat_only_cites_sources_from_the_knowledge_base(self):
        self._use_knowledge_files({"tuckshops.md": self.TUCKSHOP})

        def fake_call_chat(message, retrieved_context_text=""):
            return {"response": "Answer.", "grounded": True, "sources": ["A Made-Up Act"]}

        with unittest.mock.patch.object(config, "AI_PROVIDER", "ollama"), unittest.mock.patch.object(config, "CHAT_USE_AI", True), \
             unittest.mock.patch.object(ollama_provider, "is_configured", return_value=True), \
             unittest.mock.patch.object(ollama_provider, "call_chat", side_effect=fake_call_chat):
            status, payload = call("POST", "/chat", {"message": "How do I renew my tuckshop licence?"}, expect_status=200)

        self.assertEqual(payload["sources"], ["Test Source Act"])

    def test_chat_uses_rule_based_engine_by_default(self):
        # No AI_PROVIDER set in this test environment.
        self._use_knowledge_files({"tuckshops.md": self.TUCKSHOP})
        status, payload = call("POST", "/chat", {"message": "How do I renew my tuckshop licence?"}, expect_status=200)
        self.assertEqual(payload["engine"], "rule_based")
        self.assertTrue(payload["grounded"])

    def test_a_one_word_question_can_still_match(self):
        # "tuckshop" is the only meaningful word, so it needs only that one match.
        self._use_knowledge_files({"tuckshops.md": self.TUCKSHOP})
        status, payload = call("POST", "/chat", {"message": "What is a tuckshop?"}, expect_status=200)
        self.assertTrue(payload["grounded"])

    def test_ai_not_configured_by_default(self):
        # Sanity check on the actual, unmocked state of this test
        # environment: no AI_PROVIDER/OPENAI_API_KEY is set, so AI mode is
        # off and every plain endpoint call above this line used the
        # rule-based engine, exactly as before this feature was added.
        self.assertEqual(config.AI_PROVIDER, "")
        self.assertFalse(ai_provider.is_configured())

    # ---- Ollama: long judgments (chunking, context window, timeout) ----------

    def test_ollama_short_text_is_one_chunk(self):
        chunks = textutil.split_into_chunks("The plaintiff filed an affidavit.", 3500)
        self.assertEqual(chunks, ["The plaintiff filed an affidavit."])

    def test_ollama_long_text_is_split_without_losing_anything(self):
        paragraphs = [f"Paragraph {i}. " + ("The court considered the evidence. " * 30) for i in range(10)]
        text = "\n\n".join(paragraphs)
        chunks = textutil.split_into_chunks(text, 3500)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 3500)
        rejoined = " ".join(chunks)
        for i in range(10):
            self.assertIn(f"Paragraph {i}.", rejoined)

    def test_ollama_one_giant_paragraph_and_giant_sentence_are_still_split(self):
        one_paragraph = "The court held that the claim fails. " * 400
        for chunk in textutil.split_into_chunks(one_paragraph, 1000):
            self.assertLessEqual(len(chunk), 1000)
        giant_sentence = "word " * 2000  # no sentence endings at all
        for chunk in textutil.split_into_chunks(giant_sentence, 1000):
            self.assertLessEqual(len(chunk), 1000)

    def test_ollama_short_judgment_is_one_request(self):
        calls = []

        def fake_chat_json(system_prompt, user_text, max_tokens=None, timeout=None):
            calls.append((system_prompt, user_text))
            return {"simplified_text": "PLAIN SUMMARY", "key_points": ["a", "b"],
                    "outcome": "Application granted.", "citations": ["Some Act"], "grounded": True}

        with unittest.mock.patch.object(ollama_provider, "_chat_json", side_effect=fake_chat_json):
            result = ollama_provider.call_simplify("The applicant filed an affidavit.")
        self.assertEqual(len(calls), 1)
        self.assertIs(calls[0][0], ollama_provider._SIMPLIFY_SYSTEM_PROMPT)
        self.assertEqual(calls[0][1], "The applicant filed an affidavit.")  # sent untouched
        self.assertEqual(result["simplified_text"], "PLAIN SUMMARY")
        self.assertEqual(result["key_points"], ["a", "b"])
        self.assertEqual(result["outcome"], "Application granted.")

    def test_ollama_long_judgment_takes_one_request_with_its_main_parts(self):
        calls = []

        def fake_chat_json(system_prompt, user_text, max_tokens=None, timeout=None):
            calls.append((system_prompt, user_text, max_tokens, timeout))
            return {"simplified_text": "FINAL SUMMARY", "key_points": [], "decision": ["Guilty."],
                    "citations": [], "grounded": True}

        filler = " ".join(f"The witness number {n} said something unimportant about the weather." for n in range(200))
        text = ("THE STATE\nversus\nJOHN BANDA\n\nHIGH COURT OF ZIMBABWE\n\nIntroduction\n"
                "The accused is charged with stock theft. " + filler +
                " In S v Moyo HH 12-20 it was held that the cattle must be identified. "
                "Accordingly, the accused is found guilty of stock theft. "
                "The accused is sentenced to 9 years imprisonment.")
        with unittest.mock.patch.object(ollama_provider, "_chat_json", side_effect=fake_chat_json):
            result = ollama_provider.call_simplify(text)

        self.assertEqual(len(calls), 1)                        # one request, however long the judgment
        prompt, sent, max_tokens, timeout = calls[0]
        self.assertIs(prompt, ollama_provider._SIMPLIFY_SYSTEM_PROMPT)
        self.assertLessEqual(len(sent), config.OLLAMA_CHUNK_CHARS + 200)
        self.assertIn("found guilty of stock theft", sent)     # the decision is always included
        self.assertIn("sentenced to 9 years", sent)
        self.assertIn("charged with stock theft", sent)
        self.assertNotIn("S v Moyo", sent)                     # quoted cases are left out
        self.assertEqual(max_tokens, config.OLLAMA_SUMMARY_MAX_TOKENS)
        self.assertEqual(timeout, config.OLLAMA_SUMMARY_TIMEOUT)
        self.assertEqual(result["simplified_text"], "FINAL SUMMARY")

    def test_normalise_simplified_is_forgiving_about_sloppy_model_output(self):
        r = textutil.normalise_simplified(
            {"simplified_text": "  Summary  ", "key_points": "just one point", "outcome": "null",
             "citations": "Prescription Act", "grounded": "true"}, "Test")
        self.assertEqual(r["simplified_text"], "Summary")
        self.assertEqual(r["key_points"], ["just one point"])  # a string becomes a one-item list, not letters
        self.assertIsNone(r["outcome"])                        # the word "null" means no outcome
        self.assertEqual(r["citations"], ["Prescription Act"])
        self.assertIs(r["grounded"], True)                     # the string "true" is understood
        with self.assertRaises(ai_provider.AIProviderError):
            textutil.normalise_simplified({"key_points": ["x"]}, "Test")  # no summary at all -> fall back

    def test_ollama_request_asks_for_big_context_and_long_timeout(self):
        captured = {}

        class FakeResponse:
            def read(self):
                return json.dumps({"message": {"content": json.dumps(
                    {"simplified_text": "x", "citations": [], "grounded": False})}}).encode()
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False

        def fake_urlopen(request, timeout=None):
            captured["payload"] = json.loads(request.data.decode())
            captured["timeout"] = timeout
            return FakeResponse()

        with unittest.mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            ollama_provider.call_simplify("The defendant filed an affidavit.")

        self.assertEqual(captured["payload"]["options"]["num_ctx"], config.OLLAMA_NUM_CTX)
        # room for the longest text we send (OLLAMA_CHUNK_CHARS, ~4 characters a token) plus the answer
        self.assertGreaterEqual(config.OLLAMA_NUM_CTX * 4, config.OLLAMA_CHUNK_CHARS + 4 * config.OLLAMA_SUMMARY_MAX_TOKENS)
        self.assertEqual(captured["payload"]["options"]["num_predict"], config.OLLAMA_SUMMARY_MAX_TOKENS)
        self.assertEqual(captured["timeout"], config.OLLAMA_SUMMARY_TIMEOUT)
        self.assertGreaterEqual(config.OLLAMA_SUMMARY_TIMEOUT, 60)
        self.assertFalse(captured["payload"]["stream"])

    # ---- knowledge base: the chatbot's reference library ------------------------
    # Uses a made-up topic (tuckshop licences) so test text can never be
    # mistaken for real legal content.

    TUCKSHOP = "\n".join([
        "Title: Tuckshop licences",
        "Source: Test Source Act",
        "",
        "# Tuckshop licences",
        "",
        "Tuckshop licences are handled by the council.",
        "",
        "## How do I renew a tuckshop licence?",
        "Take your old licence and proof of payment to the council office before it expires.",
        "",
        "## What does a tuckshop licence cost?",
        "The fee depends on the size of the shop.",
    ])

    def _use_knowledge_files(self, files):
        """Points the knowledge base at a temporary folder holding `files`
        ({name: text}), loads it, and cleans everything up after the test."""
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        for name, text in files.items():
            with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
                f.write(text)
        patcher = unittest.mock.patch.object(config, "KNOWLEDGE_DIR", folder)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(lambda: database.replace_knowledge([]))  # leave the shared database clean
        knowledge.sync(log=lambda message: None)
        return folder

    def test_knowledge_file_is_cut_into_entries_by_heading(self):
        folder = self._use_knowledge_files({"tuckshops.md": self.TUCKSHOP})
        entries = knowledge.parse_file(os.path.join(folder, "tuckshops.md"))
        titles = [e["title"] for e in entries]
        self.assertIn("Tuckshop licences: How do I renew a tuckshop licence?", titles)
        self.assertIn("Tuckshop licences: What does a tuckshop licence cost?", titles)
        for e in entries:
            self.assertEqual(e["source"], "Test Source Act")
            self.assertEqual(e["file_name"], "tuckshops.md")
        renew = next(e for e in entries if "renew" in e["title"])
        self.assertIn("proof of payment", renew["content"])
        self.assertNotIn("Source:", renew["content"])  # the header lines aren't part of the answer

    def test_word_documents_in_the_knowledge_base_are_read_and_answered(self):
        import zipfile
        W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'

        def para(text, style=None, numbered=False):
            ppr = ""
            if style or numbered:
                ppr = "<w:pPr>" + (f'<w:pStyle w:val="{style}"/>' if style else "") + \
                      ('<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>' if numbered else "") + "</w:pPr>"
            return f"<w:p>{ppr}<w:r><w:t>{text}</w:t></w:r></w:p>"

        body = (para("Tuckshop Guide", "Title")
                + para("How do I renew a tuckshop licence?", "Heading1")
                + para("To renew a tuckshop licence:")
                + para("Take the old licence and proof of payment to the council offices.", numbered=True)
                + para("What does a tuckshop licence cost?", "Heading1")
                + para("A tuckshop licence costs twenty dollars a year."))
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        with zipfile.ZipFile(os.path.join(folder, "Tuckshop Guide.docx"), "w") as z:
            z.writestr("word/document.xml", f"<w:document {W}><w:body>{body}</w:body></w:document>")
        with open(os.path.join(folder, "old.doc"), "wb") as f:
            f.write(b"not readable")
        logged = []
        with unittest.mock.patch.object(config, "KNOWLEDGE_DIR", folder):
            self.addCleanup(lambda: database.replace_knowledge([]))
            knowledge.sync(log=logged.append)
            entries = knowledge.parse_file(os.path.join(folder, "Tuckshop Guide.docx"))
            _, payload = call("POST", "/chat", {"message": "How do I renew my tuckshop licence?"}, expect_status=200)
        titles = [e["title"] for e in entries]
        self.assertIn("Tuckshop Guide: How do I renew a tuckshop licence?", titles)
        self.assertIn("proof of payment", payload["response"])
        self.assertTrue(payload["grounded"])
        self.assertTrue(any("old.doc" in m and "can't be read" in m for m in logged))

    def test_knowledge_file_without_headers_uses_the_file_name(self):
        folder = self._use_knowledge_files({"my_notes.txt": "Just some plain notes about tuckshops."})
        entries = knowledge.parse_file(os.path.join(folder, "my_notes.txt"))
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["title"], "My Notes")
        self.assertEqual(entries[0]["source"], "My Notes")

    def test_knowledge_ignores_template_readme_and_other_file_types(self):
        self._use_knowledge_files({
            "real.md": "Real content about tuckshops.",
            "_EXAMPLE_format.md": "Placeholder that must never reach the chatbot.",
            "README.md": "Instructions, not knowledge.",
            "photo.png": "not text",
        })
        files = {e["file_name"] for e in database.list_knowledge()}
        self.assertEqual(files, {"real.md"})

    def test_knowledge_long_sections_are_split_into_short_entries(self):
        long_text = "\n\n".join(f"Paragraph {i}. " + "Tuckshops need a licence. " * 10 for i in range(8))
        with unittest.mock.patch.object(config, "KNOWLEDGE_CHUNK_CHARS", 400):
            self._use_knowledge_files({"long.md": long_text})
        entries = database.list_knowledge()
        self.assertGreater(len(entries), 1)
        for e in entries:
            self.assertLessEqual(len(e["content"]), 400)

    def test_chatbot_answers_from_the_knowledge_base_without_any_ai(self):
        self._use_knowledge_files({"tuckshops.md": self.TUCKSHOP})
        status, payload = call("POST", "/chat", {"message": "How do I renew my tuckshop licence?"}, expect_status=200)
        self.assertEqual(payload["engine"], "rule_based")
        self.assertTrue(payload["grounded"])
        self.assertIn("proof of payment", payload["response"])
        self.assertEqual(payload["sources"], ["Test Source Act"])

    def test_chatbot_does_not_answer_from_a_weak_knowledge_match(self):
        self._use_knowledge_files({"tuckshops.md": self.TUCKSHOP})
        # shares only one word ("licence") with the knowledge base -- not good enough to show as an answer
        status, payload = call("POST", "/chat", {"message": "driving licence xyzzy plugh"}, expect_status=200)
        self.assertFalse(payload["grounded"])
        self.assertEqual(payload["sources"], [])

    def test_questions_outside_the_knowledge_base_are_refused_not_answered_from_elsewhere(self):
        self._use_knowledge_files({"tuckshops.md": self.TUCKSHOP})
        status, payload = call("POST", "/chat", {"message": "I need a protection order"}, expect_status=200)
        self.assertFalse(payload["grounded"])
        self.assertEqual(payload["sources"], [])
        self.assertNotIn("Domestic Violence Act", payload["response"])

    def test_ai_chat_receives_the_knowledge_excerpts_as_reference_material(self):
        self._use_knowledge_files({"tuckshops.md": self.TUCKSHOP})
        captured = {}

        def fake_call_chat(message, retrieved_context_text=""):
            captured["context"] = retrieved_context_text
            return {"response": "ok", "grounded": True, "sources": []}

        with unittest.mock.patch.object(config, "AI_PROVIDER", "ollama"), unittest.mock.patch.object(config, "CHAT_USE_AI", True), \
             unittest.mock.patch.object(ollama_provider, "is_configured", return_value=True), \
             unittest.mock.patch.object(ollama_provider, "call_chat", side_effect=fake_call_chat):
            call("POST", "/chat", {"message": "How do I renew my tuckshop licence?"}, expect_status=200)

        self.assertIn("Relevant knowledge base excerpts", captured["context"])
        self.assertIn("proof of payment", captured["context"])
        self.assertIn("Test Source Act", captured["context"])
        # chat is knowledge-base only: no glossary, case-law or directory material
        self.assertNotIn("glossary", captured["context"])
        self.assertNotIn("case law", captured["context"])
        self.assertNotIn("directory", captured["context"])

    def test_knowledge_changes_take_effect_after_a_reload(self):
        folder = self._use_knowledge_files({"tuckshops.md": self.TUCKSHOP})
        self.assertGreater(len(database.list_knowledge()), 0)
        os.remove(os.path.join(folder, "tuckshops.md"))
        knowledge.sync()  # what happens when the backend restarts
        self.assertEqual(database.list_knowledge(), [])
        status, payload = call("POST", "/chat", {"message": "How do I renew my tuckshop licence?"}, expect_status=200)
        self.assertFalse(payload["grounded"])

    def test_a_heading_that_matches_the_question_ranks_first(self):
        self._use_knowledge_files({"tuckshops.md": self.TUCKSHOP})
        hits = retrieval.retrieve_knowledge("what does a tuckshop licence cost", k=3)
        self.assertIn("cost", hits[0]["title"])

    def test_a_missing_knowledge_folder_is_harmless(self):
        with unittest.mock.patch.object(config, "KNOWLEDGE_DIR", os.path.join(tempfile.gettempdir(), "no_such_folder_xyz")):
            self.assertEqual(knowledge.sync(), 0)

    # ---- knowledge base: PDF files -----------------------------------------------

    ACT_PAGES = [
        ["1. Short title", "This Act may be cited as the Tuckshop Licences Act.",
         "2. Interpretation", "In this Act, a tuckshop means a small retail premises selling food and drink to the public."],
        ["3. Application for a licence",
         "(1) Any person who wishes to operate a tuckshop shall apply to the council for a licence in the prescribed form, accompanied by proof of identity and the prescribed fee.",
         "(2) The council shall, within thirty days of receiving an application, grant the licence or refuse it and give written reasons for the refusal."],
        ["4. Renewal of licence",
         "A licence shall be renewed annually. The holder of a licence who wishes to renew it shall, before the expiry of the licence, present the old licence and proof of payment of the renewal fee at the council office."],
    ]

    def _write_pdf(self, name, pages, **kwargs):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        make_test_pdf(os.path.join(folder, name), pages, **kwargs)
        return folder

    @unittest.skipUnless(HAVE_PDF_TOOLS, "needs pypdf and reportlab")
    def test_pdf_pages_become_entries_with_page_numbers(self):
        folder = self._write_pdf("tuckshop_act.pdf", self.ACT_PAGES)
        entries = knowledge.parse_file(os.path.join(folder, "tuckshop_act.pdf"))
        self.assertEqual([e["title"] for e in entries],
                         ["Tuckshop Act (page 1)", "Tuckshop Act (page 2)", "Tuckshop Act (page 3)"])
        self.assertEqual(entries[2]["source"], "Tuckshop Act, page 3")
        self.assertEqual(entries[2]["file_name"], "tuckshop_act.pdf")
        self.assertIn("proof of payment of the renewal fee at the council office", entries[2]["content"])

    @unittest.skipUnless(HAVE_PDF_TOOLS, "needs pypdf and reportlab")
    def test_pdf_wrapped_lines_are_joined_but_clauses_and_headings_stay_apart(self):
        folder = self._write_pdf("act.pdf", self.ACT_PAGES)
        page2 = knowledge.parse_file(os.path.join(folder, "act.pdf"))[1]["content"]
        paragraphs = page2.split("\n\n")
        self.assertEqual(paragraphs[0], "3. Application for a licence")  # heading on its own
        self.assertTrue(paragraphs[1].startswith("(1) Any person"))       # clauses separate
        self.assertTrue(paragraphs[2].startswith("(2) The council"))
        self.assertNotIn("\n", paragraphs[1])                              # wrapped lines joined into one

    @unittest.skipUnless(HAVE_PDF_TOOLS, "needs pypdf and reportlab")
    def test_pdf_running_headers_footers_and_page_numbers_are_removed(self):
        pages = [[f"{i}. Section {name}", f"The {name} rules apply to every tuckshop in the district."]
                 for i, name in enumerate(["one", "two", "three", "four", "five"], 1)]
        folder = self._write_pdf("long.pdf", pages, header="THE TUCKSHOP LICENCES ACT [CHAPTER 99:99]", footer=True)
        entries = knowledge.parse_file(os.path.join(folder, "long.pdf"))
        everything = " ".join(e["content"] for e in entries)
        self.assertNotIn("CHAPTER 99:99", everything)
        self.assertNotRegex(everything, r"Page \d")
        self.assertIn("3. Section three", everything)  # the real content is untouched

    @unittest.skipUnless(HAVE_PDF_TOOLS, "needs pypdf and reportlab")
    def test_pdf_a_repeated_line_in_the_body_of_a_page_is_never_removed(self):
        pages = [[f"Opening line {i}.", "Second line.", "This sentence repeats on every page.",
                  "Fourth line.", "Closing line."] for i in range(1, 6)]
        folder = self._write_pdf("repeat.pdf", pages)
        entries = knowledge.parse_file(os.path.join(folder, "repeat.pdf"))
        self.assertEqual(sum(e["content"].count("This sentence repeats on every page.") for e in entries), 5)

    def test_reflow_joins_lines_and_repairs_words_split_by_a_hyphen(self):
        lines = [
            "The holder of a licence shall renew the licen-",
            "ce before it expires, and shall pay the fee at",
            "the council office in person.",
            "A second paragraph begins on a new line and",
            "continues for another full line of text here.",
        ]
        self.assertEqual(knowledge._reflow(lines), [
            "The holder of a licence shall renew the licence before it expires, and shall pay the fee at the council office in person.",
            "A second paragraph begins on a new line and continues for another full line of text here.",
        ])

    @unittest.skipUnless(HAVE_PDF_TOOLS, "needs pypdf and reportlab")
    def test_scanned_pdf_is_skipped_with_a_clear_message_and_other_files_still_load(self):
        folder = self._use_knowledge_files({"notes.md": "Notes about tuckshops and their licences."})
        make_test_pdf(os.path.join(folder, "scan.pdf"), [[], []])  # pages with no text at all
        messages = []
        knowledge.sync(log=messages.append)
        self.assertTrue(any("skipped scan.pdf" in m and "scanned" in m for m in messages), messages)
        self.assertEqual({e["file_name"] for e in database.list_knowledge()}, {"notes.md"})

    def test_missing_pdf_package_is_reported_not_fatal(self):
        folder = self._use_knowledge_files({"notes.md": "Notes about tuckshops."})
        with open(os.path.join(folder, "act.pdf"), "wb") as f:
            f.write(b"%PDF-1.4 not important")
        messages = []
        with unittest.mock.patch.dict(sys.modules, {"pypdf": None}):  # as if pypdf isn't installed
            knowledge.sync(log=messages.append)
        self.assertTrue(any("act.pdf" in m and "pip install pypdf" in m for m in messages), messages)
        self.assertEqual({e["file_name"] for e in database.list_knowledge()}, {"notes.md"})

    def test_damaged_pdf_is_skipped_not_fatal(self):
        folder = self._use_knowledge_files({"notes.md": "Notes about tuckshops."})
        with open(os.path.join(folder, "broken.pdf"), "wb") as f:
            f.write(b"this is not really a pdf at all")
        messages = []
        knowledge.sync(log=messages.append)
        self.assertTrue(any("skipped broken.pdf" in m for m in messages), messages)
        self.assertEqual({e["file_name"] for e in database.list_knowledge()}, {"notes.md"})

    @unittest.skipUnless(HAVE_PDF_TOOLS, "needs pypdf and reportlab")
    def test_chatbot_answers_from_a_pdf_and_names_the_page(self):
        folder = self._use_knowledge_files({})
        make_test_pdf(os.path.join(folder, "tuckshop_act.pdf"), self.ACT_PAGES)
        knowledge.sync()
        status, payload = call("POST", "/chat", {"message": "How do I renew my tuckshop licence?"}, expect_status=200)
        self.assertTrue(payload["grounded"])
        self.assertIn("proof of payment", payload["response"])
        self.assertEqual(payload["sources"], ["Tuckshop Act, page 3"])

    def test_unchanged_files_are_not_read_again(self):
        folder = self._use_knowledge_files({"a.md": "Tuckshops need a licence.", "b.md": "Council fees apply."})
        messages = []
        with unittest.mock.patch.object(knowledge, "parse_file", wraps=knowledge.parse_file) as spy:
            knowledge.sync(log=messages.append)
            self.assertEqual(spy.call_count, 0)  # both were already read by _use_knowledge_files

            with open(os.path.join(folder, "b.md"), "a", encoding="utf-8") as f:
                f.write("\nMore about fees.")
            knowledge.sync(log=messages.append)
            self.assertEqual(spy.call_count, 1)  # only the changed file is read again
            self.assertEqual(spy.call_args[0][0], os.path.join(folder, "b.md"))

            with unittest.mock.patch.object(config, "KNOWLEDGE_CHUNK_CHARS", 500):
                knowledge.sync(log=messages.append)
            self.assertEqual(spy.call_count, 3)  # changed chunk size -> both files read again

    def test_a_knowledge_table_from_the_previous_version_is_upgraded(self):
        import sqlite3
        db_path = os.path.join(tempfile.mkdtemp(), "old.db")
        self.addCleanup(shutil.rmtree, os.path.dirname(db_path), ignore_errors=True)
        old = sqlite3.connect(db_path)
        old.execute("""CREATE TABLE knowledge (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
                       content TEXT NOT NULL, source TEXT NOT NULL, file_name TEXT NOT NULL, created_at TEXT NOT NULL)""")
        old.execute("INSERT INTO knowledge (title, content, source, file_name, created_at) VALUES ('t','c','s','old.md','x')")
        old.commit()
        old.close()
        with unittest.mock.patch.object(database, "DATABASE_PATH", db_path):
            database.init_db()  # what happens when the new version starts on the old database
            self.assertEqual(database.knowledge_file_keys(), {"old.md": ""})  # row kept, blank key -> will be re-read

    # ---- a friendly, flexible chatbot: small talk, welcome, synonyms ----------------

    def test_a_greeting_gets_a_welcome_not_a_refusal(self):
        status, payload = call("POST", "/chat", {"message": "hie"}, expect_status=200)
        self.assertEqual(payload["kind"], "greeting")
        self.assertEqual(payload["engine"], "conversation")
        self.assertFalse(payload["grounded"])
        self.assertTrue(payload["response"].startswith("Welcome to the Self Actors Hub ZW!"))
        self.assertIn("How can I assist you today?", payload["response"])

    def test_the_welcome_uses_the_users_first_name_when_there_is_one(self):
        _, with_object = call("POST", "/chat", {"message": "hello", "user": {"full_name": "Tendai Moyo", "email": "t@x.com"}}, expect_status=200)
        self.assertTrue(with_object["response"].startswith("Welcome to the Self Actors Hub ZW, Tendai!"))
        _, with_string = call("POST", "/chat", {"message": "hello", "user": "Rudo Dube"}, expect_status=200)
        self.assertTrue(with_string["response"].startswith("Welcome to the Self Actors Hub ZW, Rudo!"))
        for no_name in (None, {"email": "only@x.com"}, {"full_name": "Guest"}, "someone@x.com"):
            _, payload = call("POST", "/chat", {"message": "hello", "user": no_name}, expect_status=200)
            self.assertTrue(payload["response"].startswith("Welcome to the Self Actors Hub ZW!"), no_name)

    def test_many_ways_of_saying_hello_are_greetings(self):
        for text in ("Hello there!", "Good morning", "HIE", "mhoro", "Hey!!", "hi bot", "Good afternoon, everyone"):
            self.assertEqual(conversation.classify(text)[0], "greeting", text)

    def test_how_are_you_thanks_goodbye_and_who_are_you(self):
        expected = {
            "how are you?": "how_are_you", "Hi, how are you doing": "how_are_you",
            "thanks": "thanks", "Thank you very much!": "thanks",
            "bye": "goodbye", "see you later": "goodbye",
            "who are you": "about", "what can you do?": "about", "help": "about",
            "ok": "ack", "okay thanks": "thanks",
        }
        for text, kind in expected.items():
            _, payload = call("POST", "/chat", {"message": text}, expect_status=200)
            self.assertEqual(payload["kind"], kind, text)
            self.assertEqual(payload["engine"], "conversation")
        _, about = call("POST", "/chat", {"message": "what can you do"}, expect_status=200)
        self.assertIn("IECMS", about["response"])

    def test_a_greeting_or_thanks_followed_by_a_question_is_answered_as_a_question(self):
        self._use_knowledge_files({"tuckshops.md": self.TUCKSHOP})
        for text in ("hi, how do I renew my tuckshop licence?", "thanks, how do I renew my tuckshop licence?"):
            _, payload = call("POST", "/chat", {"message": text}, expect_status=200)
            self.assertTrue(payload["grounded"], text)
            self.assertIn("proof of payment", payload["response"])
            self.assertNotIn("kind", payload)
        self.assertEqual(conversation.classify("Hi, how do I log in?"), (None, "how do I log in?"))

    def test_small_talk_never_calls_the_search_or_the_ai(self):
        def must_not_run(*args, **kwargs):
            raise AssertionError("the AI was called for small talk")

        with unittest.mock.patch.object(config, "AI_PROVIDER", "ollama"), \
             unittest.mock.patch.object(ollama_provider, "is_configured", return_value=True), \
             unittest.mock.patch.object(ollama_provider, "call_chat", side_effect=must_not_run):
            _, payload = call("POST", "/chat", {"message": "hello"}, expect_status=200)
        self.assertEqual(payload["engine"], "conversation")

    def test_welcome_endpoint_gives_the_opening_message(self):
        _, anonymous = call("POST", "/chat/welcome", {}, expect_status=200)
        self.assertTrue(anonymous["response"].startswith("Welcome to the Self Actors Hub ZW!"))
        _, named = call("POST", "/chat/welcome", {"user": {"full_name": "Tendai Moyo"}}, expect_status=200)
        self.assertTrue(named["response"].startswith("Welcome to the Self Actors Hub ZW, Tendai!"))
        self.assertTrue(named["response"].endswith("How can I assist you today?"))

    def test_everyday_words_match_through_synonyms(self):
        # The guide says "registration" and "sign up"; the person says "register".
        self._use_knowledge_files({"reg.md": "Source: Test Source Act\n\n## Council registration\n"
                                             "Take your ID to the office to sign up for a tuckshop licence."})
        _, payload = call("POST", "/chat", {"message": "how do I register a tuckshop"}, expect_status=200)
        self.assertTrue(payload["grounded"])
        self.assertIn("sign up", payload["response"])

    def test_a_short_what_is_question_prefers_the_introduction(self):
        self._use_knowledge_files({"guide.md": "Title: Tuckshop guide\nSource: Test Source Act\n\n"
                                               "# Tuckshop guide\n\n## Introduction\n"
                                               "A tuckshop is a small shop run under a council licence.\n\n"
                                               "## Fees\nTuckshop fees are paid at the council. The tuckshop fee "
                                               "depends on the tuckshop size. Tuckshop owners pay yearly."})
        _, payload = call("POST", "/chat", {"message": "what is a tuckshop"}, expect_status=200)
        self.assertIn("small shop run under a council licence", payload["response"])
        # a more specific question is left alone
        _, fees = call("POST", "/chat", {"message": "how are tuckshop fees paid"}, expect_status=200)
        self.assertIn("paid at the council", fees["response"])

    def test_words_that_only_ask_the_question_are_not_treated_as_topics(self):
        # "stand for" / "should I use" describe the QUESTION, not what it is about.
        self._use_knowledge_files({"abbr.md": "Source: Test Source Act\n\n## Acronyms and abbreviations\n"
                                              "TSL means Tuckshop Licence System."})
        _, payload = call("POST", "/chat", {"message": "what does TSL stand for"}, expect_status=200)
        self.assertTrue(payload["grounded"])
        self.assertIn("Tuckshop Licence System", payload["response"])

    def test_sign_in_means_login_not_the_word_sign(self):
        self._use_knowledge_files({"howto.md": "Source: Test Source Act\n\n## Logging in\n"
                                               "To log into the system, open the login screen.\n\n"
                                               "## Signing documents\nSigning a document needs a certified copy."})
        _, payload = call("POST", "/chat", {"message": "how do I sign in"}, expect_status=200)
        self.assertIn("login screen", payload["response"])

    # ---- directory ----------------------------------------------------

    def test_directory_lists_seed_data(self):
        status, payload = call("GET", "/directory", expect_status=200)
        names = [r["name"] for r in payload["results"]]
        self.assertIn("Legal Aid Directorate", names)
        self.assertIn("Zimbabwe Lawyers for Human Rights (ZLHR)", names)
        self.assertIn("Legal Resources Foundation (LRF)", names)
        # Confirm shape matches what the frontend expects to map over.
        first = payload["results"][0]
        for key in ("name", "type", "focus_areas", "location", "contact", "description", "fee_basis"):
            self.assertIn(key, first)

    def test_browse_list_shows_six_private_practitioners_and_hides_the_search_only_firms(self):
        _, payload = call("GET", "/directory", expect_status=200)
        results = payload["results"]
        private = [r for r in results if r["type"] in ("law_firm", "private_lawyer")]
        self.assertEqual(len(private), 6)  # two full rows of three cards
        names = {r["name"] for r in private}
        self.assertIn("Muvingi and Mugadza Legal Practitioners", names)
        self.assertIn("Kanokanga & Partners", names)
        self.assertNotIn("Sawyer and Mkushi", {r["name"] for r in results})
        self.assertTrue(all(r["search_only"] is False for r in results))
        # the free legal aid section: the state directorate, ZWLA, ALAC, ZLHR and LRF
        self.assertEqual(len([r for r in results if r["type"] in ("state", "legal_aid_ngo")]), 5)

    def test_search_finds_the_hidden_firms(self):
        _, payload = call("GET", "/directory?q=sawyer", expect_status=200)
        self.assertEqual([r["name"] for r in payload["results"]], ["Sawyer and Mkushi"])
        self.assertTrue(payload["results"][0]["search_only"])
        # case-insensitive, and part of a name is enough
        _, payload = call("GET", "/directory?q=MHISHI", expect_status=200)
        self.assertEqual([r["name"] for r in payload["results"]], ["Mhishi Nkomo Legal Practice"])
        # a featured firm is found by search too
        _, payload = call("GET", "/directory?q=kanokanga", expect_status=200)
        self.assertEqual([r["name"] for r in payload["results"]], ["Kanokanga & Partners"])

    def test_search_matches_focus_areas_and_needs_every_word(self):
        _, payload = call("GET", "/directory?q=employment", expect_status=200)
        self.assertEqual([r["name"] for r in payload["results"]], ["Coghlan Welsh & Guest"])
        _, payload = call("GET", "/directory?q=law+society", expect_status=200)
        self.assertEqual([r["name"] for r in payload["results"]], ["Law Society of Zimbabwe"])
        _, payload = call("GET", "/directory?q=sawyer+employment", expect_status=200)
        self.assertEqual(payload["results"], [])

    def test_search_with_no_match_is_empty_and_an_empty_search_is_the_browse_list(self):
        _, nothing = call("GET", "/directory?q=zzzzzz", expect_status=200)
        self.assertEqual(nothing["results"], [])
        _, browse = call("GET", "/directory", expect_status=200)
        _, blank = call("GET", "/directory?q=", expect_status=200)
        self.assertEqual(blank["results"], browse["results"])

    def test_every_pasted_firm_is_in_the_directory_and_none_claims_pro_bono(self):
        everything = database.list_directory("harare")  # all the pasted firms say Harare
        names = {r["name"] for r in everything}
        for expected in ("Muvingi and Mugadza Legal Practitioners", "Kanokanga & Partners", "Honey & Blanckenberg",
                         "Gambe Law Group (GLG)", "Tafadzwa Ralph Mugabe | Legal Counsel", "Law Society of Zimbabwe"):
            self.assertIn(expected, names)
        pasted = [r for r in everything if "Google" in r["description"] or r["name"] == "Law Society of Zimbabwe"]
        self.assertEqual(len(pasted), 20)  # all twenty from the pasted list
        self.assertTrue(all(r["fee_basis"] == "paid" for r in pasted))  # unknown pro-bono status is never claimed
        self.assertEqual(len({r["name"] for r in database.list_directory("harare")}), len(database.list_directory("harare")))

    def test_starting_again_does_not_duplicate_directory_entries(self):
        before = len(database.list_directory("harare"))
        database.init_db()
        self.assertEqual(len(database.list_directory("harare")), before)

    def test_an_old_directory_table_is_upgraded_and_gets_the_new_firms(self):
        import sqlite3
        db_path = os.path.join(tempfile.mkdtemp(), "old.db")
        self.addCleanup(shutil.rmtree, os.path.dirname(db_path), ignore_errors=True)
        old = sqlite3.connect(db_path)
        old.execute("""CREATE TABLE directory (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, type TEXT NOT NULL,
                       focus_areas TEXT NOT NULL, location TEXT NOT NULL, contact TEXT NOT NULL,
                       description TEXT NOT NULL, fee_basis TEXT NOT NULL)""")
        old.execute("INSERT INTO directory (name,type,focus_areas,location,contact,description,fee_basis) "
                    "VALUES ('Moyo Chambers','private_lawyer','[\"Criminal\"]','Joina City','+263773456789','Criminal defence practice.','paid')")
        old.commit()
        old.close()
        with unittest.mock.patch.object(database, "DATABASE_PATH", db_path):
            database.init_db()  # what happens when the new version starts on the old database
            browse = [r["name"] for r in database.list_directory()]
            self.assertIn("Moyo Chambers", browse)          # the old row is kept
            self.assertIn("Kanokanga & Partners", browse)   # the new featured firm is added
            self.assertNotIn("Sawyer and Mkushi", browse)   # a search-only firm stays hidden...
            self.assertEqual([r["name"] for r in database.list_directory("sawyer")], ["Sawyer and Mkushi"])  # ...until searched

    # ---- Legal Dictionary -------------------------------------------------
    # Ollama/ChatGPT are mocked: these prove the plumbing (AI first, saved for
    # next time, glossary when AI is off or fails), not the model's wording.

    FAKE_DEFINITION = {"found": True, "is_legal_term": True,
                       "plain_meaning": "A court order taking money owed to you from someone who owes your debtor.",
                       "legal_meaning": "Used to collect a judgment debt.", "example": "The bank was served with a garnishee order.",
                       "related_terms": ["Garnishee Order", "writ of execution", "judgment debt"]}

    def _fresh_dictionary(self):
        database.clear_dictionary_cache()
        self.addCleanup(database.clear_dictionary_cache)

    def test_dictionary_browse_lists_the_glossary_a_to_z(self):
        _, payload = call("GET", "/dictionary", expect_status=200)
        terms = [t["term"] for t in payload["terms"]]
        self.assertEqual(terms, sorted(terms))
        self.assertIn("affidavit", terms)
        self.assertFalse(payload["ai_enabled"])  # no AI_PROVIDER in the test environment

    def test_dictionary_without_ai_uses_the_glossary(self):
        self._fresh_dictionary()
        _, payload = call("GET", "/dictionary?q=Affidavits", expect_status=200)
        self.assertTrue(payload["found"])
        self.assertEqual(payload["engine"], "glossary")
        self.assertEqual(payload["term"], "affidavit")  # plural finds the entry
        self.assertIn("written statement sworn to be true", payload["plain_meaning"])
        _, phrase = call("GET", "/dictionary?q=" + urllib.parse.quote("  Ex   Parte? "), expect_status=200)
        self.assertTrue(phrase["found"])

    def test_dictionary_unknown_word_without_ai_is_not_found_with_suggestions(self):
        self._fresh_dictionary()
        _, payload = call("GET", "/dictionary?q=afidavit", expect_status=200)
        self.assertFalse(payload["found"])
        self.assertIsNone(payload["plain_meaning"])
        self.assertIn("affidavit", payload["suggestions"])

    def test_dictionary_asks_ollama_and_saves_the_answer(self):
        self._fresh_dictionary()
        asked = []

        def fake_chat_json(system_prompt, user_text, max_tokens=None, timeout=None):
            asked.append(user_text)
            return dict(self.FAKE_DEFINITION)

        with unittest.mock.patch.object(config, "AI_PROVIDER", "ollama"), \
             unittest.mock.patch.object(ollama_provider, "_chat_json", side_effect=fake_chat_json):
            _, first = call("GET", "/dictionary?q=Garnishee+Order", expect_status=200)
            _, second = call("GET", "/dictionary?q=garnishee%20order", expect_status=200)

        self.assertEqual(asked, ["garnishee order"])  # asked once, tidied up
        self.assertEqual(first["engine"], "ai:ollama")
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(second["plain_meaning"], first["plain_meaning"])
        self.assertNotIn("Garnishee Order", first["related_terms"])  # the word itself isn't "related"

    def test_dictionary_ai_answer_also_shows_the_glossary_meaning(self):
        self._fresh_dictionary()
        with unittest.mock.patch.object(config, "AI_PROVIDER", "ollama"), \
             unittest.mock.patch.object(ollama_provider, "_chat_json", return_value=dict(self.FAKE_DEFINITION)):
            _, payload = call("GET", "/dictionary?q=interdict", expect_status=200)
        self.assertEqual(payload["engine"], "ai:ollama")  # llama3.2 explains glossary words too
        self.assertIn("stopping someone", payload["glossary_meaning"])
        self.assertEqual(payload["source"], "High Court Rules, 2021")

    def test_dictionary_falls_back_to_the_glossary_when_ollama_is_down(self):
        self._fresh_dictionary()
        with unittest.mock.patch.object(config, "AI_PROVIDER", "ollama"), \
             unittest.mock.patch.object(ollama_provider, "_chat_json",
                                        side_effect=ollama_provider.AIProviderError("Couldn't reach Ollama")):
            _, payload = call("GET", "/dictionary?q=subpoena", expect_status=200)
        self.assertEqual(payload["engine"], "glossary")
        self.assertTrue(payload["found"])
        self.assertIsNone(database.get_dictionary_entry("subpoena"))  # a fallback isn't saved  # a fallback isn't saved

    def test_dictionary_does_not_save_a_word_the_model_does_not_know(self):
        self._fresh_dictionary()
        with unittest.mock.patch.object(config, "AI_PROVIDER", "ollama"), \
             unittest.mock.patch.object(ollama_provider, "_chat_json",
                                        return_value={"found": False, "plain_meaning": ""}):
            _, payload = call("GET", "/dictionary?q=blorptastic", expect_status=200)
        self.assertFalse(payload["found"])
        self.assertIsNone(database.get_dictionary_entry("blorptastic"))

    def test_dictionary_tolerates_sloppy_model_output(self):
        r = textutil.normalise_definition({"plain_meaning": " A sworn statement. ", "related_terms": "oath",
                                           "legal_meaning": "null", "is_legal_term": "yes"}, "affidavit", "Test")
        self.assertTrue(r["found"])
        self.assertEqual(r["plain_meaning"], "A sworn statement.")
        self.assertEqual(r["related_terms"], ["oath"])
        self.assertIsNone(r["legal_meaning"])
        self.assertTrue(r["is_legal_term"])
        self.assertFalse(textutil.normalise_definition({"found": True}, "x", "Test")["found"])  # no meaning -> not found

    def test_dictionary_rejects_input_that_is_not_a_word(self):
        for bad in ("12345", "<script>", "a " * 40, "%3F%3F%3F"):
            call("GET", "/dictionary?q=" + bad.replace(" ", "+"), expect_status=400)

    def test_dictionary_suggest_is_instant_and_never_calls_the_ai(self):
        with unittest.mock.patch.object(config, "AI_PROVIDER", "ollama"), \
             unittest.mock.patch.object(ollama_provider, "_chat_json", side_effect=AssertionError("AI called")):
            _, payload = call("GET", "/dictionary/suggest?q=pr", expect_status=200)
        self.assertIn("prescription", payload["suggestions"])
        self.assertIn("prima facie", payload["suggestions"])
        _, empty = call("GET", "/dictionary/suggest?q=", expect_status=200)
        self.assertEqual(empty["suggestions"], [])

    # ---- document wizard: templates and Word / PDF downloads -----------------

    AFFIDAVIT = {"affidavit_kind": "founding", "deponent_name": "Rudo Dube",
                 "deponent_address": "12 Example Road, Harare", "other_party_name": "Tapiwa Moyo",
                 "facts": "1. I lent the Respondent my car on 3 March 2026.\nHe has refused to return it.",
                 "relief_sought": "that the Respondent return my car"}

    def _download(self, body):
        """POST /documents/download and return (status, headers, raw bytes)."""
        req = urllib.request.Request(BASE_URL + "/documents/download", data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def test_templates_endpoint_lists_the_new_documents_and_their_questions(self):
        _, payload = call("GET", "/documents/templates", expect_status=200)
        ids = {t["id"]: t for t in payload["templates"]}
        for expected in ("affidavit", "notice_of_opposition", "protection_order", "summons", "application", "appeal"):
            self.assertIn(expected, ids)
        self.assertEqual(ids["protection_order"]["courts"], ["magistrates_court"])
        self.assertTrue(all("build" not in t for t in payload["templates"]))  # no server code leaks out
        names = [f["name"] for f in ids["affidavit"]["fields"]]
        self.assertIn("facts", names)

    def test_founding_affidavit_numbers_the_facts_and_has_the_commissioners_section(self):
        _, doc = call("POST", "/documents/generate", {"document_type": "affidavit", "court_tier": "high_court",
                                                       "case_details": self.AFFIDAVIT}, expect_status=200)
        self.assertIn("HIGH COURT OF ZIMBABWE", doc["content"])
        self.assertIn("FOUNDING AFFIDAVIT", doc["content"])
        self.assertIn("COMMISSIONER OF OATHS", doc["content"])
        nums = [b for b in doc["blocks"] if b["style"] == "num"]
        self.assertTrue(any(b["text"] == "I lent the Respondent my car on 3 March 2026." for b in nums))  # typed "1." removed
        self.assertEqual([b["num"] for b in nums], [f"{i}." for i in range(1, len(nums) + 1)])
        self.assertTrue(any("Commissioner of Oaths" in w for w in doc["warnings"]))
        self.assertEqual(doc["title"], "Founding Affidavit - Rudo Dube")

    def test_opposing_affidavit_swaps_the_parties(self):
        details = dict(self.AFFIDAVIT, affidavit_kind="opposing")
        _, doc = call("POST", "/documents/generate", {"document_type": "affidavit", "court_tier": "high_court",
                                                       "case_details": details}, expect_status=200)
        parties = [(b["text"], b["role"]) for b in doc["blocks"] if b["style"] == "party"]
        self.assertEqual(parties, [("TAPIWA MOYO", "APPLICANT"), ("RUDO DUBE", "RESPONDENT")])
        self.assertIn("OPPOSING AFFIDAVIT", doc["content"])

    DIVORCE = {"claim_type": "divorce", "plaintiff_name": "Sebastian Chiwanza",
               "plaintiff_address": "Century House, 6th Floor, Harare", "defendant_name": "Catherine Jim",
               "defendant_address": "12 Crowlands Road, Bluffhill, Harare",
               "facts": "The parties married each other on 31 March 2007.\nThe marriage has irretrievably broken down.",
               "prayer": "For a decree of divorce.\nThat each party bears its own costs of suit."}

    # ---- Streaming answers (word by word) ---------------------------------

    def test_dictionary_stream_shows_llama_writing_and_saves_the_answer(self):
        self._fresh_dictionary()
        text = ["MEANING: A written statement ", "sworn to be true.\n", "IN COURT: Used as evidence.\n",
                "EXAMPLE: She filed an affidavit.\n", "RELATED: oath, deponent, affidavit"]
        with unittest.mock.patch.object(config, "AI_PROVIDER", "ollama"), \
             unittest.mock.patch.object(ollama_provider, "stream_text", return_value=iter(text)):
            events = call_stream("GET", "/dictionary/stream?q=Affidavits")
        self.assertEqual(events[0]["type"], "start")
        self.assertIn("written statement", events[0]["glossary_meaning"])  # shown at once
        self.assertEqual("".join(e["text"] for e in events if e["type"] == "delta"), "".join(text))
        result = events[-1]["result"]
        self.assertEqual(result["engine"], "ai:ollama")
        self.assertEqual(result["plain_meaning"], "A written statement sworn to be true.")
        self.assertEqual(result["legal_meaning"], "Used as evidence.")
        self.assertEqual(result["related_terms"], ["oath", "deponent"])
        events = call_stream("GET", "/dictionary/stream?q=affidavit")  # second time: saved, no AI
        self.assertTrue(events[-1]["result"]["cached"])

    def test_dictionary_stream_falls_back_to_the_glossary(self):
        self._fresh_dictionary()
        with unittest.mock.patch.object(config, "AI_PROVIDER", "ollama"), \
             unittest.mock.patch.object(ollama_provider, "stream_text",
                                        side_effect=ollama_provider.AIProviderError("down")):
            events = call_stream("GET", "/dictionary/stream?q=subpoena")
        self.assertEqual(events[-1]["result"]["engine"], "glossary")
        status, _ = call("GET", "/dictionary/stream?q=12345")
        self.assertEqual(status, 400)

    def test_chat_stream_sends_the_answer_word_by_word_from_the_knowledge_base(self):
        self._use_knowledge_files({"tuckshops.md": self.TUCKSHOP})
        pieces = ["1. Take the old licence ", "and proof of payment.\n", "2. Pay the fee."]
        with unittest.mock.patch.object(config, "AI_PROVIDER", "ollama"), unittest.mock.patch.object(config, "CHAT_USE_AI", True), \
             unittest.mock.patch.object(ollama_provider, "stream_text", return_value=iter(pieces)) as ai:
            events = call_stream("POST", "/chat/stream", {"message": "How do I renew a tuckshop licence?"})
        self.assertIn("Reference material", ai.call_args[0][1])
        deltas = [e for e in events if e["type"] == "delta"]
        self.assertGreaterEqual(len(deltas), 2)
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(events[-1]["response"], "".join(pieces))
        self.assertEqual(events[-1]["sources"], ["Test Source Act"])

    def test_chat_stream_says_out_of_scope_when_the_model_finds_nothing(self):
        self._use_knowledge_files({"tuckshops.md": self.TUCKSHOP})
        with unittest.mock.patch.object(config, "AI_PROVIDER", "ollama"), unittest.mock.patch.object(config, "CHAT_USE_AI", True), \
             unittest.mock.patch.object(ollama_provider, "stream_text", return_value=iter(["NOT_", "IN_KB"])):
            events = call_stream("POST", "/chat/stream", {"message": "How do I renew a tuckshop licence?"})
        self.assertEqual([e["type"] for e in events], ["done"])  # nothing half-written was shown
        self.assertEqual(events[0]["response"], __import__("app.legal_content", fromlist=["x"]).OUT_OF_SCOPE_RESPONSE)

    def test_chat_answers_from_the_knowledge_base_without_asking_ollama_by_default(self):
        self._use_knowledge_files({"tuckshops.md": self.TUCKSHOP})
        with unittest.mock.patch.object(config, "AI_PROVIDER", "ollama"), \
             unittest.mock.patch.object(ollama_provider, "stream_text") as stream, \
             unittest.mock.patch.object(ollama_provider, "_chat_json") as chat:
            events = call_stream("POST", "/chat/stream", {"message": "How do I renew a tuckshop licence?"})
            _, plain = call("POST", "/chat", {"message": "How do I renew a tuckshop licence?"}, expect_status=200)
        stream.assert_not_called()
        chat.assert_not_called()
        self.assertIn("proof of payment", events[-1]["response"])
        self.assertIn("proof of payment", plain["response"])
        self.assertEqual(plain["engine"], "rule_based")

    def test_the_website_itself_is_served_at_the_root(self):
        with urllib.request.urlopen(BASE_URL + "/", timeout=5) as resp:
            self.assertTrue(resp.headers["Content-Type"].startswith("text/html"))
            page = resp.read().decode("utf-8")
        self.assertIn("Self-Actor", page)
        self.assertIn("location.origin", page)  # it talks to the computer it was opened from

    def test_chat_stream_without_ai_shows_the_passage_and_greets_people(self):
        self._use_knowledge_files({"tuckshops.md": self.TUCKSHOP})
        events = call_stream("POST", "/chat/stream", {"message": "How do I renew a tuckshop licence?"})
        self.assertIn("proof of payment", events[-1]["response"])
        events = call_stream("POST", "/chat/stream", {"message": "hie"})
        self.assertEqual(events[-1]["engine"], "conversation")

    def test_court_town_is_not_asked_and_documents_are_filed_at_harare(self):
        _, data = call("GET", "/documents/templates", expect_status=200)
        for t in data["templates"]:
            self.assertNotIn("court_location", [f["name"] for f in t["fields"]])
        _, doc = call("POST", "/documents/generate", {"document_type": "summons", "court_tier": "high_court",
                                                       "case_details": dict(self.DIVORCE, court_location="Bulawayo")},
                      expect_status=200)
        self.assertIn("HELD AT HARARE", doc["content"])
        self.assertNotIn("BULAWAYO", doc["content"])

    def test_simplify_stream_shows_the_decision_at_once_then_llama_writes_the_summary(self):
        pieces = ["The court found ", "the accused guilty."]
        with unittest.mock.patch.object(config, "AI_PROVIDER", "ollama"), \
             unittest.mock.patch.object(ollama_provider, "stream_text", return_value=iter(pieces)) as ai:
            events = call_stream("POST", "/simplify/stream", {"text": self.JUDGMENT})
        self.assertEqual(events[0]["type"], "base")
        self.assertTrue(events[0]["writing"])
        self.assertEqual(events[0]["result"]["engine"], "rule_based")  # ready before the model says anything
        self.assertTrue(events[0]["result"]["decision"] or events[0]["result"]["key_points"])
        self.assertEqual([e["text"] for e in events if e["type"] == "delta"], pieces)
        done = events[-1]["result"]
        self.assertEqual(done["simplified_text"], "The court found the accused guilty.")
        self.assertEqual(done["engine"], "ai:ollama")
        self.assertEqual(done["decision"], events[0]["result"]["decision"])  # the orders come from the text itself
        self.assertLessEqual(ai.call_args.kwargs["max_tokens"], 300)  # a short answer is a quick answer

    def test_simplify_stream_keeps_the_built_in_summary_when_llama_is_too_slow(self):
        with unittest.mock.patch.object(config, "AI_PROVIDER", "ollama"), \
             unittest.mock.patch.object(ollama_provider, "stream_text",
                                        side_effect=ollama_provider.AIProviderError("timed out")):
            events = call_stream("POST", "/simplify/stream", {"text": self.JUDGMENT})
        self.assertEqual(events[-1]["result"]["engine"], "rule_based")
        self.assertTrue(events[-1]["result"]["simplified_text"])

    def test_phone_numbers_take_numbers_and_a_leading_plus_only(self):
        base = dict(self.DIVORCE)
        for good in ("0772123456", "+263772123456"):
            call("POST", "/documents/generate", {"document_type": "summons", "court_tier": "high_court",
                                                  "case_details": dict(base, plaintiff_phone=good)}, expect_status=200)
        for bad in ("0772abc456", "077-212-3456", "26+3772123456", "0772 123 456", "12345"):
            status, payload = call("POST", "/documents/generate", {"document_type": "summons", "court_tier": "high_court",
                                                                   "case_details": dict(base, plaintiff_phone=bad)})
            self.assertEqual(status, 400, bad)
            self.assertIn("numbers only", payload["error"])

    def test_national_id_numbers_must_look_like_a_zimbabwe_id(self):
        from app import documents
        self.assertEqual(documents.clean_id_number("63-123456a75"), "63-123456 A 75")
        self.assertEqual(documents.clean_id_number("08 1234567 X 42"), "08-1234567 X 42")
        for bad in ("ABC", "63-123456-75", "631234567", "63-12345 A 75", "63-123456 A 7B", "63-123456 AB 75"):
            self.assertIsNone(documents.clean_id_number(bad), bad)
        _, data = call("GET", "/documents/templates", expect_status=200)
        types = {f["name"]: f["type"] for t in data["templates"] for f in t["fields"]}
        self.assertEqual(types["id_number"], "idnumber")
        self.assertEqual(types["plaintiff_phone"], "tel")

    CIVIL = {"plaintiff_name": "Tendai Moyo", "plaintiff_address": "5 Jason Moyo Avenue, Harare",
             "plaintiff_postal_address": "P.O. Box 12, Harare", "plaintiff_phone": "0772123456",
             "defendant_name": "Rudo Dube", "defendant_address": "12 Crowlands Road, Bluffhill, Harare",
             "claim_amount": "500", "claim_reason": "money lent and advanced",
             "interest_rate": "5", "interest_from": "1 March 2026"}

    def test_civil_summons_follows_the_magistrates_court_form(self):
        _, doc = call("POST", "/documents/generate", {"document_type": "summons", "court_tier": "magistrates_court",
                                                       "case_details": self.CIVIL}, expect_status=200)
        text, blocks = doc["content"], doc["blocks"]
        self.assertEqual(blocks[0]["text"], "IN THE COURT OF THE MAGISTRATE FOR THE PROVINCE OF HARARE")
        self.assertEqual(blocks[1]["text"], "HELD AT HARARE")
        self.assertTrue(blocks[1]["right"].startswith("CASE NO. CIV"))
        parties = [(b["text"], b["role"]) for b in blocks if b["style"] == "party"]
        self.assertEqual(parties, [("TENDAI MOYO", "PLAINTIFF"), ("RUDO DUBE", "DEFENDANT")])
        self.assertEqual([b["text"] for b in blocks if b["style"] == "title"], ["CIVIL SUMMONS"])
        self.assertIn("To: The Messenger of Court or his/her lawful Deputy:", text)
        self.assertIn("COMMAND RUDO DUBE (Defendant) of 12 Crowlands Road", text)
        self.assertIn("within 7 (seven) days", text)
        self.assertIn("Clerk of the Court at Harare", text)
        self.assertIn("(1) Payment of the sum of US$500 being money lent and advanced.", text)
        self.assertIn("(2) Interest thereon at the prescribed rate of 5% per annum from 1 March 2026", text)
        self.assertIn("(3) Costs of suit.", text)
        self.assertIn("TAKE NOTE THAT:", text)
        self.assertIn("CLERK OF THE COURT", text)
        self.assertIn("Postal address: P.O. Box 12, Harare", text)
        self.assertNotIn("DECLARATION", text.upper().replace("ANNEXURE", ""))
        self.assertNotIn("pagebreak", [b["style"] for b in blocks])

    def test_divorce_summons_follows_the_high_court_form(self):
        details = dict(self.DIVORCE, children="Tadisa Chiwanza and Talinda Chiwanza", custody_to="plaintiff",
                       child_maintenance="100", spouse_maintenance="150", maintenance_until="remarriage",
                       assets_plaintiff="the house in Mabelreign", assets_defendant="the Toyota Corolla")
        _, doc = call("POST", "/documents/generate", {"document_type": "divorce_summons", "court_tier": "magistrates_court",
                                                       "case_details": details}, expect_status=200)
        text, blocks = doc["content"], doc["blocks"]
        self.assertIn("Form No 29", blocks[0]["text"])
        self.assertEqual(blocks[1]["text"], "IN THE HIGH COURT OF ZIMBABWE")  # always the High Court
        self.assertTrue(any("High Court" in w for w in doc["warnings"]))
        self.assertEqual([b["text"] for b in blocks if b["style"] == "title"], ["DIVORCE SUMMONS"])
        for phrase in ("To the Defendant: CATHERINE JIM", "within 10 (ten) days",
                       "Registry of the High Court of Zimbabwe at Harare", "irretrievable breakdown",
                       "1. A decree of divorce.",
                       "namely Tadisa Chiwanza and Talinda Chiwanza, be awarded to the Plaintiff",
                       "US$100 per month per child", "US$150 per month until remarriage",
                       "(a) Plaintiff to be awarded: the house in Mabelreign", "Costs of suit.",
                       "judgment may be given against you in your absence", "Sheriff",
                       "REGISTRAR OF THE HIGH COURT", "within 5 km of the Court"):
            self.assertIn(phrase, text)
        self.assertTrue(any("Annexure" in w for w in doc["warnings"]))

    def test_names_cannot_contain_numbers_and_money_is_numbers_only(self):
        for field, bad in (("plaintiff_name", "Tendai M0yo"), ("defendant_name", "Rudo 2"), ("claim_amount", "500 dollars")):
            status, payload = call("POST", "/documents/generate", {"document_type": "summons", "court_tier": "magistrates_court",
                                                                   "case_details": dict(self.CIVIL, **{field: bad})})
            self.assertEqual(status, 400, field)
        status, _ = call("POST", "/auth/register", {"full_name": "R2D2", "email": "r2@example.com", "password": "secret123"})
        self.assertEqual(status, 400)
        _, data = call("GET", "/documents/templates", expect_status=200)
        for t in data["templates"]:
            for f in t["fields"]:
                if f["name"].endswith("_name"):
                    self.assertEqual(f["type"], "name", f["name"])

    def test_summons_downloads_as_word_and_pdf(self):
        for doc_type, details in (("summons", self.CIVIL), ("divorce_summons", self.DIVORCE)):
            for fmt, magic in (("docx", b"PK"), ("pdf", b"%PDF")):
                status, _h, data = self._download({"document_type": doc_type, "court_tier": "high_court",
                                                   "case_details": details, "format": fmt})
                self.assertEqual(status, 200)
                self.assertTrue(data.startswith(magic))

    def test_protection_order_is_always_for_the_magistrates_court(self):
        _, doc = call("POST", "/documents/generate", {
            "document_type": "protection_order", "court_tier": "high_court",
            "case_details": {"complainant_name": "Chipo Ncube", "complainant_address": "Glen View",
                             "respondent_name": "Farai Ncube", "relationship": "Spouse (married)",
                             "acts": "He hit me on 2 October 2026.", "orders": ["no_violence", "no_contact"],
                             "urgent": "yes"}}, expect_status=200)
        self.assertEqual(doc["court_tier"], "magistrates_court")
        self.assertIn("MAGISTRATES COURT OF ZIMBABWE", doc["content"])
        self.assertIn("Domestic Violence Act [Chapter 5:16]", doc["content"])
        self.assertIn("not to contact me", doc["content"])
        self.assertIn("URGENCY", doc["content"])
        self.assertTrue(any("filed in the Magistrates Court" in w for w in doc["warnings"]))

    def test_notice_of_opposition_with_missing_answers_gets_placeholders_and_a_warning(self):
        _, doc = call("POST", "/documents/generate", {"document_type": "notice_of_opposition",
                                                       "court_tier": "high_court", "case_details": {}},
                      expect_status=200)
        self.assertIn("NOTICE OF OPPOSITION", doc["content"])
        self.assertIn("[CASE NUMBER ON THE APPLICATION]", doc["content"])
        self.assertTrue(any("blank" in w for w in doc["warnings"]))

    def test_download_word_document(self):
        import io, zipfile
        status, headers, data = self._download({"document_type": "affidavit", "court_tier": "high_court",
                                                "case_details": self.AFFIDAVIT, "format": "docx"})
        self.assertEqual(status, 200)
        self.assertIn("wordprocessingml", headers["Content-Type"])
        self.assertIn('filename="Founding_Affidavit_-_Rudo_Dube.docx"', headers["Content-Disposition"])
        self.assertIn("Content-Disposition", headers["Access-Control-Expose-Headers"])
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("word/document.xml").decode()
        self.assertIn("FOUNDING AFFIDAVIT", xml)
        self.assertIn("Rudo Dube".upper(), xml)

    def test_download_pdf(self):
        status, headers, data = self._download({"document_type": "protection_order", "court_tier": "magistrates_court",
                                                "case_details": {"complainant_name": "Chipo Ncube"}, "format": "pdf"})
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/pdf")
        self.assertTrue(data.startswith(b"%PDF-1.4"))
        self.assertTrue(data.rstrip().endswith(b"%%EOF"))
        self.assertIn(b"APPLICATION FOR A PROTECTION ORDER", data)
        if HAVE_PDF_TOOLS:  # read it back to prove it is a valid PDF
            from pypdf import PdfReader
            import io
            text = "".join(p.extract_text() for p in PdfReader(io.BytesIO(data)).pages)
            self.assertIn("CHIPO NCUBE", text)

    def test_download_rejects_an_unknown_format(self):
        status, _, data = self._download({"document_type": "summons", "court_tier": "high_court",
                                          "case_details": {}, "format": "exe"})
        self.assertEqual(status, 400)
        self.assertIn("docx", json.loads(data)["error"])

    def test_pdf_wraps_long_text_and_breaks_pages(self):
        from app import doc_export
        long_facts = "\n".join(f"Fact number {i}: " + "the respondent did something important. " * 8 for i in range(40))
        blocks = [{"style": "num", "num": f"{i}.", "text": t} for i, t in enumerate(long_facts.split("\n"), 1)]
        pdf = doc_export.render_pdf(blocks, "Long")
        self.assertGreater(pdf.count(b"/Type /Page "), 3)
        self.assertIn(b"Page 1 of", pdf)
        for raw in doc_export._wrap("word " * 500, doc_export._TEXT_W):
            self.assertLessEqual(doc_export._width(raw), doc_export._TEXT_W + 0.01)

    def test_document_text_cannot_inject_markup_into_word_files(self):
        import io, zipfile
        details = dict(self.AFFIDAVIT, deponent_address="<w:p>Evil & Co</w:p>")
        status, _, data = self._download({"document_type": "affidavit", "court_tier": "high_court",
                                          "case_details": details, "format": "docx"})
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("word/document.xml").decode()
        self.assertIn("&lt;w:p&gt;Evil &amp; Co&lt;/w:p&gt;", xml)

    # ---- forum --------------------------------------------------------

    def test_forum_requires_login_to_post(self):
        call("POST", "/forum/posts", {"title": "Hi", "body": "Test"}, expect_status=401)

    def test_forum_post_and_list(self):
        _, reg = call("POST", "/auth/register", {
            "full_name": "Forum User",
            "email": "forumuser@example.com",
            "password": "anotherpassword",
        }, expect_status=201)
        token = reg["access_token"]

        status, created = call("POST", "/forum/posts", {
            "title": "Can I represent myself in the High Court?",
            "body": "Looking for others' experience with self-representation.",
        }, token=token, expect_status=201)
        self.assertEqual(created["author_name"], "Forum User")

        status, posts = call("GET", "/forum/posts", expect_status=200)
        titles = [p["title"] for p in posts]
        self.assertIn("Can I represent myself in the High Court?", titles)

    def test_forum_rejects_bad_token(self):
        call("POST", "/forum/posts", {"title": "Hi", "body": "Test"},
             token="not-a-real-token", expect_status=401)

    # ---- case law -------------------------------------------------------

    def test_case_law_list_shape(self):
        # Case law starts empty (see database.py) and fills up only from
        # real, grounded simplifications -- run one now so this test has
        # something to check the shape of.
        call("POST", "/simplify", {"text": "The plaintiff's summons named the wrong defendant."}, expect_status=200)
        status, payload = call("GET", "/case-law", expect_status=200)
        self.assertIsInstance(payload, list)
        self.assertGreater(len(payload), 0)
        for entry in payload:
            for key in ("id", "title", "simplified_text", "citations", "court_tier", "created_at"):
                self.assertIn(key, entry)

    # ---- CORS / unknown routes -----------------------------------------

    def test_unknown_route_returns_404(self):
        call("GET", "/does-not-exist", expect_status=404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
