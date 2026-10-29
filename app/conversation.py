"""
conversation.py
---------------
The friendly, flexible side of the chatbot. Everything here is plain,
readable rules -- no AI needed -- and none of it gives out legal or IECMS
facts (those only ever come from the knowledge base, see server.py):

  - Small talk: "hie", "good morning", "thanks", "bye", "how are you",
    "who are you / what can you do" get a natural reply instead of a refusal.
  - The welcome message: "Welcome to the Self Actors Hub ZW, Tendai! ..."
  - Question tweaks that help the knowledge-base search: a short "what is X?"
    question is steered towards the introduction/overview of the guide.

To change what the chatbot says, edit the texts in this file. To change the
name it welcomes people to, set APP_NAME in app/config.py.
"""

import re

from app import config
from app import retrieval

# Questions shown when someone asks what the chatbot can do. Make sure your
# knowledge base really answers them.
EXAMPLE_QUESTIONS = [
    "How do I register an account on IECMS?",
    "How do I file a case?",
    "I forgot my password",
]

_GUEST_NAMES = {"guest", "user", "anonymous", "null", "none", "undefined", "visitor"}


# ---------------------------------------------------------------------------
# Who is talking
# ---------------------------------------------------------------------------

def first_name(user):
    """The user's first name from whatever the frontend sent as `user`: an
    object like {"full_name": "Tendai Moyo", "email": ...}, a plain name, or
    nothing. Returns "" if there's no usable name."""
    if isinstance(user, dict):
        name = user.get("full_name") or user.get("name") or user.get("first_name") or ""
    elif isinstance(user, str):
        name = user
    else:
        name = ""
    parts = str(name).split()
    if not parts or "@" in parts[0]:
        return ""
    name = parts[0].strip(".,!?;:\"'")
    if not name or name.lower() in _GUEST_NAMES or len(name) > 30:
        return ""
    return name


# ---------------------------------------------------------------------------
# Replies
# ---------------------------------------------------------------------------

def welcome_message(user=None):
    name = first_name(user)
    who = f", {name}" if name else ""
    return (
        f"Welcome to the {config.APP_NAME}{who}! I can help you find your way around IECMS, "
        "the Integrated Electronic Case Management System -- from creating an account to "
        "filing a case. How can I assist you today?"
    )


def _about_me():
    examples = ", ".join(f"“{q}”" for q in EXAMPLE_QUESTIONS)
    return (
        f"I'm the {config.APP_NAME} assistant. I answer questions about IECMS (the Integrated "
        "Electronic Case Management System) using the guidance I've been given -- for example "
        f"how to sign up, log in or file a case. Try asking: {examples}. "
        "I can't give legal advice or help with topics outside IECMS."
    )


def reply(kind, user=None):
    """The reply for a kind of small talk returned by classify()."""
    name = first_name(user)
    who = f", {name}" if name else ""
    if kind == "greeting":
        return welcome_message(user)
    if kind == "how_are_you":
        return ("I'm doing well, thank you for asking! I'm here to help you with IECMS. "
                "How can I assist you today?")
    if kind == "thanks":
        return "You're welcome! If you have another question about IECMS, just ask."
    if kind == "goodbye":
        return f"Goodbye{who}! Come back any time you have a question about IECMS. Take care!"
    if kind == "about":
        return _about_me()
    if kind == "ack":
        return "Alright! Ask me anything else about IECMS whenever you're ready."
    return welcome_message(user)


# ---------------------------------------------------------------------------
# Recognising small talk
# ---------------------------------------------------------------------------
# A greeting at the start ("hie", "hello there", "good morning") is peeled off
# first, so "hi, how do I log in?" still gets a real answer to the question.

_GREETING = (
    r"(?:good\s+(?:morning|afternoon|evening|day)|hi+e*|hie|hey+|hello+|hallo|hullo|halo|howdy|"
    r"greetings|yo|sup|mhoro|mhoroi|makadii|salibonani|sawubona)"
)
_GREETING_FILLER = r"(?:there|everyone|all|team|bot|chatbot|assistant|friend|again|sir|madam)"
_LEADING_GREETING = re.compile(
    rf"^\s*(?:{_GREETING}\b[\s,!.:;\-]*)+(?:{_GREETING_FILLER}\b[\s,!.:;\-]*)*", re.IGNORECASE
)

_THANKS_WORDS = {"thanks", "thank", "thankyou", "thx", "ty", "appreciate", "ndatenda", "siyabonga", "tatenda"}
_THANKS_FILLER = {"you", "so", "much", "very", "a", "lot", "alot", "for", "your", "the", "that", "was",
                  "great", "good", "again", "guys", "bot", "dear", "many", "i", "it", "help", "ok", "okay"}

_GOODBYE = re.compile(
    r"(?:ok |okay )?(?:bye|bye bye|goodbye|good bye|see you|see you later|see you soon|take care|cya|talk later)"
    r"(?: thanks| thank you)?"
)
_HOW_ARE_YOU = re.compile(
    r"how (?:are|r) (?:you|u)(?: doing| today| now)?|hows it going|how is it going|how do you do|"
    r"how are things|how you doing|how is you|are you ok|are you okay|whats up|wassup|makadii"
)
_ABOUT = [re.compile(p) for p in (
    r"(?:who|what) (?:are|r) (?:you|u)(?: exactly)?",
    r"what (?:can|do) (?:you|u) do(?: for me)?",
    r"what (?:can|may|should) i ask(?: you)?",
    r"what (?:do|can) (?:you|u) (?:know|help (?:me )?with)(?: about)?",
    r"(?:can|could|will) (?:you|u) help(?: me)?(?: please)?",
    r"how (?:can|could) (?:you|u) help(?: me)?",
    r"help(?: me)?(?: please)?",
    r"i need help(?: please)?",
    r"(?:introduce|tell me about) (?:yourself|you)",
    r"what is your name|whats your name|your name",
)]
_ACK = re.compile(
    r"(?:ok|okay|k|alright|all right|cool|great|nice|good|fine|sure|got it|i see|noted|understood|"
    r"perfect|awesome|wow)(?: thanks| thank you)?"
)


def _normalise(text):
    text = (text or "").lower().replace("’", "'").replace("'", "")
    return " ".join(re.findall(r"[a-z0-9]+", text))


def classify(message):
    """Works out whether `message` is small talk.

    Returns (kind, question):
      - kind is "greeting", "how_are_you", "thanks", "goodbye", "about", "ack",
        or None when it's a real question.
      - question is the message with any leading greeting removed ("hi, how do
        I log in?" -> "how do I log in?"); use it as the question when kind is None.
    """
    match = _LEADING_GREETING.match(message or "")
    greeted = bool(match)
    question = (message[match.end():] if match else message or "").strip()
    norm = _normalise(question)
    if not norm:
        return ("greeting" if greeted else None), (question or message or "")

    words = norm.split()
    if any(w in _THANKS_WORDS for w in words) and all(w in _THANKS_WORDS | _THANKS_FILLER for w in words):
        return "thanks", question
    if _GOODBYE.fullmatch(norm):
        return "goodbye", question
    if _HOW_ARE_YOU.fullmatch(norm):
        return "how_are_you", question
    if any(p.fullmatch(norm) for p in _ABOUT):
        return "about", question
    if _ACK.fullmatch(norm):
        return "ack", question
    return None, question


# ---------------------------------------------------------------------------
# Helping the knowledge-base search
# ---------------------------------------------------------------------------

_DEFINE = re.compile(
    r"(?:what (?:is|are|was|were)|whats|tell me (?:more )?about|explain|define|describe|meaning of|"
    r"about|who is|what do you know about) (?P<subject>.+)"
)
_MEANING = re.compile(r"what does (?P<subject>.+?) (?:mean|stand for)")


def search_boost(question):
    """Extra words that make the search prefer the right KIND of passage.
    They only influence ranking -- a passage still has to match the
    question's own words to be used at all.

    A short "what is X?" question is really asking for the introduction or
    overview of X; "what does X stand for?" is asking for the list of
    abbreviations. Longer questions ("what is the website address for X?")
    are left alone because they are asking for something specific.
    """
    norm = _normalise(question)
    meaning = _MEANING.fullmatch(norm)
    if meaning and len(retrieval._tokens(meaning.group("subject"))) <= 2:
        return "introduction acronym abbreviation"
    define = _DEFINE.fullmatch(norm)
    if define and len(retrieval._tokens(define.group("subject"))) <= 2:
        return "introduction overview"
    return ""
