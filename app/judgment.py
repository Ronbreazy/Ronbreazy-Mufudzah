"""
judgment.py
-----------
The Judgment Summarizer, rule-based version (used when no AI model is
switched on, or when the model can't be reached).

A pasted judgment is read in five passes:

  1. The CASE DETAILS are taken from the heading -- case number, parties,
     court, judge and date -- and the heading is then set aside, so lines
     like "Mr O Shava, for the defence" never end up in the summary.
  2. Each remaining sentence is sorted into what kind of sentence it is:
     what the case is about, the facts alleged, the plea or defence, the
     court's own findings, the court's decision, or a quotation of ANOTHER
     case ("In S v Wairosi it was held that ...").
  3. Sentences about other cases are kept out of the summary and the
     decision -- they are the law the court applied, not what it decided.
     Their names are listed separately as "cases referred to".
  4. The DECISION is taken from the end of the judgment: a numbered order
     ("It is ordered that: 1. ... 2. ...") if there is one, otherwise the
     verdict, sentence or ruling sentences ("the accused is found guilty
     of murder", "the appeal is dismissed with costs").
  5. A short plain summary is put together from those pieces.

It only ever reuses the judgment's own words (it never invents wording),
so it is a reading aid: always check it against the judgment itself.
"""

import re

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

CASE_NUMBER = re.compile(
    r"\b(?:HH|HB|HMA|HMT|HCC|HCH|HCHCR|HCHC|HCB|HC|SC|CCZ|CC|LC|HCMA)\s?\d{1,5}\s?[-/]\s?\d{2,4}\b")
JUDGE = re.compile(r"\b([A-Z][A-Z'\-]{2,})\s+(J|JA|JP|CJ|DCJ|AJ|AJA)\b")
_NOT_JUDGES = {"ZIMBABWE", "HARARE", "BULAWAYO", "COURT", "MUTARE", "MASVINGO", "GWERU"}
COURT = re.compile(
    r"\b(HIGH COURT OF ZIMBABWE|SUPREME COURT OF ZIMBABWE|CONSTITUTIONAL COURT OF ZIMBABWE|"
    r"LABOUR COURT OF ZIMBABWE|ELECTORAL COURT OF ZIMBABWE|ADMINISTRATIVE COURT OF ZIMBABWE|"
    r"MAGISTRATES?'? COURT(?: OF ZIMBABWE)?)\b", re.IGNORECASE)
DATE_LINE = re.compile(r"^[A-Z][A-Za-z ]{2,20}[,:]\s*\d{1,2}.*\b(?:19|20)\d{2}\b")
ACT_END = re.compile(r"\bAct\s*\[Chapter\s*\d+\s*:\s*\d+\]")
HEADING_WORDS = re.compile(
    r"^(?:Introduction|Background|Analysis|Disposition|Sentence|Verdict|Findings|Conclusion|"
    r"Discussion|Facts|Evidence|Issues?|Judgment|Order|The law|The State case|The defence case|"
    r"The State's case|The defence|The issues?|Submissions|Mitigation|Aggravation|Reasons for sentence)"
    r"\s+(?=[A-Z])")
BODY_START_HEADING = re.compile(
    r"^(introduction|background|judgment|judgement|ruling|reasons for judgment|the facts|facts)\s*:?$",
    re.IGNORECASE)

# A case name such as "S v Wairosi", "Daly v Liverpool Corporation",
# "Mashingaidze v Chipunza".
CASE_NAME = re.compile(
    r"\b((?:[A-Z][A-Za-z'.&-]*\s){0,3}[A-Z][A-Za-z'.&-]*)\s+v\.?\s+"
    r"((?:[A-Z][A-Za-z'.&()-]*)(?:\s(?:[A-Z][A-Za-z'.&()-]*|and|of|&))*)")
LAW_REPORT = re.compile(
    r"\[\d{4}\]|\b(?:19|20)\d{2}\s*\(\d\)\s*ZLR\b|\bZLR\b|\bAll ER\b|"
    r"\b(?:HH|HB|HMA|SC|HCM|CCZ)\s?\d{1,4}\s?[-/]\s?\d{2,4}\b")
PRECEDENT_WORDS = re.compile(
    r"\b(it was held|was held that|in the case of|the court held|the court in|"
    r"as was stated in|as stated in|see also|per [A-Z]|it was stated)\b", re.IGNORECASE)

ABOUT = re.compile(
    r"\b(is charged with|was charged with|are charged with|stands charged|faces a charge|"
    r"seeks (?:an )?order|seeks|applies for|application for|this is an application|"
    r"this is an appeal|appeals? against|noted an appeal|the appeal is against|"
    r"claims? (?:payment|damages|the sum)|sued|issued summons|review of)\b", re.IGNORECASE)
ALLEGATION = re.compile(r"\b(it is alleged|it was alleged|the allegations are|the state alleges|allegedly|"
                        r"testified|told the court|gave evidence|the evidence of)\b",
                        re.IGNORECASE)
DEFENCE = re.compile(
    r"\b(pleaded not guilty|pleaded guilty|in (?:his|her|their) defence|defence (?:outline|case)|"
    r"opposed the application|opposes the application|denied|raised a point in limine|"
    r"raised (?:a |the )?(?:preliminary|special) plea)\b", re.IGNORECASE)
FINDING = re.compile(
    r"\b(we find|i find|the court finds|we are satisfied|i am satisfied|the court is satisfied|"
    r"we reject|i reject|we accept|i accept|cannot succeed|can not succeed|has proved|have proved|"
    r"has been proved|failed to prove|did not prove|has not proved|was therefore|is therefore|"
    r"beyond (?:a )?reasonable doubt|on a balance of probabilities|there is no merit|"
    r"there is merit|has merit|lacks merit|the evidence shows|it is clear that)\b", re.IGNORECASE)
DECISION = re.compile(
    r"\b(found guilty|found not guilty|is guilty of|are guilty of|not guilty and (?:is )?acquitted|"
    r"is acquitted|are acquitted|acquitted and discharged|convicted of|is convicted|are convicted|"
    r"sentenced to|is sentenced|are sentenced|"
    r"(?:appeal|application|claim|review|action|matter|point in limine|special plea|exception)s? "
    r"(?:is|are|be|was) (?:hereby )?(?:dismissed|allowed|upheld|granted|refused|struck off|removed|"
    r"set aside|postponed|referred)|"
    r"is (?:hereby )?(?:dismissed|upheld|granted|set aside|declared|struck off)|"
    r"declared (?:unlawful|invalid|null|void|unconstitutional)|"
    r"judgment is (?:hereby )?entered|shall pay|is (?:hereby )?ordered|it is (?:hereby )?ordered|"
    r"is interdicted|are interdicted|is directed to|are directed to|with costs|costs shall)\b",
    re.IGNORECASE)
ORDER_START = re.compile(
    r"\b(?:it is (?:hereby |accordingly )?ordered(?: that)?|the following order is made|"
    r"order(?:ed)? as follows|in the result,? (?:i|we) make the following order|"
    r"i accordingly order(?: that)?|accordingly,? it is ordered(?: that)?|"
    r"in the result,? it is ordered(?: that)?)\s*:?\s*", re.IGNORECASE)
LEAD_IN = re.compile(r"^(accordingly|in the result|consequently|therefore|in the circumstances|"
                     r"for (?:these|the foregoing|the above) reasons|in conclusion|wherefore|"
                     r"in the premises|resultantly)[,:]?\s+", re.IGNORECASE)

_ABBREVIATIONS = ["Mr.", "Mrs.", "Ms.", "Dr.", "No.", "Nos.", "v.", "vs.", "e.g.", "i.e.", "para.",
                  "paras.", "s.", "ss.", "Pvt.", "Ltd.", "Co.", "Inc.", "St.", "J.", "Adv."]
_DOT = "․"  # a stand-in for a full stop that must not end a sentence


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _clean_line(line):
    return " ".join(line.replace(" ", " ").split())


def _sentences(text):
    """Splits running text into sentences, without breaking on common
    abbreviations ("Mr. Moyo", "S v. Banda", "No. 3") or on numbered items."""
    t = " ".join(text.split())
    for abbr in _ABBREVIATIONS:
        t = re.sub(r"(?<![A-Za-z])" + re.escape(abbr), abbr.replace(".", _DOT), t)
    t = re.sub(r"\b([A-Z])\.(?=\s?[A-Z][a-z])", r"\1" + _DOT, t)   # initials: "O. Shava"
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z(\"'“])", t)
    out = []
    for p in parts:
        p = p.replace(_DOT, ".").strip()
        p = HEADING_WORDS.sub("", p)
        if not p:
            continue
        # A lone "1." or "(a)" belongs to the next sentence.
        if out and re.fullmatch(r"\(?\d{1,2}[.)]|\(?[a-z][.)]", out[-1]):
            out[-1] = out[-1] + " " + p
        else:
            out.append(p)
    return out


def _is_precedent(sentence):
    """True for a sentence that reports what ANOTHER case decided."""
    if PRECEDENT_WORDS.search(sentence) and (CASE_NAME.search(sentence) or LAW_REPORT.search(sentence)):
        return True
    if LAW_REPORT.search(sentence) and CASE_NAME.search(sentence):
        return True
    return bool(re.search(r"\bin (?:the case of )?[A-Z][\w'.-]* v\.? [A-Z]", sentence))


def _case_names(sentence):
    names = []
    for m in CASE_NAME.finditer(sentence):
        left = m.group(1).split()
        # Keep only the capitalised words right before "v" ("In the case of S v X" -> "S v X").
        while left and left[0].lower() in ("in", "the", "case", "of", "see", "also", "and", "as", "per", "held"):
            left = left[1:]
        right = re.split(r"\s(?:it|was|held|the|that|where|in|at|per|and the)\b", m.group(2))[0]
        right = right.strip(" ,.;")
        right = re.sub(r"(?:\s+(?:[A-Z]{2,5}|\d[\w/()-]*|\[\d{4}\]))+$", "", right).strip(" ,.;")
        if left and right:
            names.append(f"{' '.join(left)} v {right}")
    return names


def _acts(text):
    """Names of Acts with their chapter numbers, e.g. "Criminal Law
    (Codification and Reform) Act [Chapter 9:23]"."""
    found = []
    for m in ACT_END.finditer(text):
        words = text[:m.start()].split()
        name, depth = [], 0
        while words:
            w = words[-1]
            if w.startswith("(") and not re.search(r"[A-Za-z]", w) and depth == 0:
                break   # "(1)" in "s 47 (1) of the ... Act" is a subsection, not part of the name
            depth += w.count(")") - w.count("(")
            if depth > 0 or w[:1].isupper() or w[:1] == "(" or w in ("and", "of", "the", "for", "on", "&"):
                name.insert(0, words.pop())
                continue
            break
        while name and name[0].lower() in ("and", "of", "the", "for", "on", "&"):
            name.pop(0)
        if name:
            found.append(" ".join(name + [m.group(0)]))
    return found


def _title(name):
    t = name.title()
    for small in (" V ", " Of ", " And ", " The ", " For "):
        t = t.replace(small, small.lower())
    return t


def _strip_lead(sentence):
    s = LEAD_IN.sub("", sentence).strip()
    return s[:1].upper() + s[1:] if s else s


def _unique(items):
    seen, out = set(), []
    for i in items:
        key = re.sub(r"\W+", " ", i.lower()).strip()
        if key and key not in seen:
            seen.add(key)
            out.append(i)
    return out


# ---------------------------------------------------------------------------
# Pass 1: the heading
# ---------------------------------------------------------------------------

def _split_heading(text):
    """Returns (case_details, body_text)."""
    raw_lines = [_clean_line(l) for l in text.replace("\r", "").split("\n")]
    lines = [l for l in raw_lines if l]
    details = {"case_number": None, "parties": None, "court": None, "judge": None, "date": None}

    head_text = " ".join(lines[:25])[:1500]
    m = CASE_NUMBER.search(head_text)
    if m:
        details["case_number"] = m.group(0)
    m = COURT.search(head_text)
    if m:
        details["court"] = m.group(1).upper().replace("MAGISTRATE COURT", "MAGISTRATES COURT")
    for m in JUDGE.finditer(head_text):
        if m.group(1) not in _NOT_JUDGES:
            details["judge"] = f"{m.group(1).title()} {m.group(2)}"
            break

    # Parties: "THE STATE / versus / FANUEL MASORA" on separate lines, or "X v Y" on one.
    for i, l in enumerate(lines[:25]):
        if l.lower() in ("versus", "v", "v.", "vs", "vs.", "-v-", "and") and 0 < i < len(lines) - 1:
            details["parties"] = f"{lines[i - 1]} v {lines[i + 1]}"
            break
    if not details["parties"]:
        m = re.search(r"\b([A-Z][A-Z .'&-]{2,60}?)\s+(?:versus|v\.?|vs\.?)\s+([A-Z][A-Z .'&-]{2,60}?)"
                      r"(?=\s+(?:HIGH|SUPREME|CONSTITUTIONAL|MAGISTRATE|LABOUR|[A-Z]+ J\b)|\s*$)", head_text)
        if m:
            details["parties"] = f"{m.group(1).strip()} v {m.group(2).strip()}"
    if details["parties"]:
        # "WALTER MAGAYA (born 6th November 1983, ID No. 59-062334N24)" -> "WALTER MAGAYA"
        p = re.sub(r"\s*\((?:born|b\.|id\b|i\.d\.|national id|n[eé]e|in (?:his|her) capacity|n\.o\.)[^)]*\)?",
                   "", details["parties"], flags=re.IGNORECASE)
        p = re.sub(r",\s*(?:born|id no\.?)\b.*?(?=\s+v\s+|$)", "", p, flags=re.IGNORECASE)
        details["parties"] = re.sub(r"\s{2,}", " ", p).strip(" ,;")
    for l in lines[:25]:
        if DATE_LINE.match(l):
            details["date"] = l
            break

    # Where does the judgment itself start?
    start = None
    for i, l in enumerate(lines[:40]):
        if BODY_START_HEADING.match(l):
            start = i + 1
            break
        jm = re.match(r"^([A-Z][A-Z'\-]{2,}(?: [A-Z][A-Z'\-]{2,})?)\s+(?:J|JA|JP|CJ|DCJ|AJ|AJA)\s*:\s*(.*)$", l)
        if jm and jm.group(2):
            lines[i] = jm.group(2)
            start = i
            break
    if start is None:
        # No marker: the heading is the run of short lines at the top.
        start = 0
        for i, l in enumerate(lines[:40]):
            if len(l) >= 100 or (len(l) > 60 and re.search(r"[a-z]{3,}\s[a-z]{3,}.*[.!?]$", l)):
                start = i
                break
        else:
            start = 0 if len(lines) <= 3 else 0

    body_lines = []
    for l in lines[start:]:
        # Drop sub-headings inside the judgment ("The State case", "Analysis").
        if len(l) < 45 and not re.search(r"[.!?:;]$", l) and not re.match(r"^\(?\d{1,2}[.)]|^\(?[a-z][.)]", l):
            continue
        body_lines.append(l)
    body = " ".join(body_lines)

    # A judgment pasted as one long line keeps its heading at the front.
    # Cut it off at the first sentence that sounds like the judgment itself.
    if len(lines) <= 3 or start == 0:
        m = re.search(r"\b(?:Introduction|INTRODUCTION|Background|JUDGMENT)\b\s+(?=[A-Z])", body[:1800])
        if m:
            body = body[m.end():]
        else:
            m = re.search(r"\b(?:The (?:accused|applicant|appellant|plaintiff|respondent|defendant|State)|"
                          r"This is an?)\b", body[:1800])
            if m and m.start() > 0 and (CASE_NUMBER.search(body[:m.start()]) or COURT.search(body[:m.start()])):
                body = body[m.start():]
        jm = re.search(r"\b[A-Z][A-Z'\-]{2,}\s+(?:J|JA|JP|CJ|DCJ|AJ|AJA)\s*:\s*", body[:1800])
        if jm:
            body = body[jm.end():]
    return details, body


# ---------------------------------------------------------------------------
# Pass 4: the decision
# ---------------------------------------------------------------------------

def _numbered_order(body):
    """The items of a formal order ("It is ordered that: 1. ... 2. ..."),
    or [] if the judgment doesn't end with one."""
    matches = list(ORDER_START.finditer(body))
    if not matches:
        return []
    m = matches[-1]
    tail = body[m.end():].strip()
    if len(tail) > 2500:
        tail = tail[:2500]
    items = re.split(r"(?:^|\s)(?:\(?\d{1,2}[.)]|\(?[a-z]\))\s+", tail)
    # Items marked with dashes or bullets ("- The estate of ... and- The estate of ...")
    split = []
    for i in items:
        split.extend(re.split(r"(?:^|[\s;,])(?:and\s*)?[-\u2013\u2014\u2022]\s*(?=[A-Z])", i))
    items = [re.sub(r"^[\s;:,.\-\u2013\u2014\u2022]+", "", i).strip(" ;,") for i in split]
    items = [re.sub(r"\s*(?:,\s*)?and$", "", i).strip(" ;,") for i in items]
    items = [i for i in items if len(i) > 3]
    if len(items) <= 1:
        # Not numbered: the order is the sentence(s) right after the words.
        first = _sentences(tail)[:2]
        items = first
    cleaned = []
    for i in items:
        i = re.split(r"\s(?:[A-Z][a-z]+ & [A-Z][a-z]+|[A-Z][a-z]+ Legal Practitioners|Messrs)\b", i)[0]
        i = i.strip()
        if i:
            cleaned.append(i[:1].upper() + i[1:])
    return cleaned[:8]


def _decision_sentences(sentences):
    n = len(sentences)
    picked = []
    for idx, s in enumerate(sentences):
        if _is_precedent(s):
            continue
        if not DECISION.search(s):
            continue
        # The decision is at the end of a judgment; earlier "ordered"/"granted"
        # sentences are usually history (e.g. an earlier interim order).
        if n > 4 and idx < n * 0.5:
            continue
        picked.append(_strip_lead(s))
    # Verdict and sentence first, then anything else, in reading order; keep it short.
    return _unique(picked)[-4:]


def _headline(decision_text, kind):
    """A one-line plain statement of the result, where it can be worked out."""
    t = decision_text
    low = t.lower()
    parts = []
    m = re.search(r"\bfound not guilty\b|\bnot guilty and (?:is )?acquitted\b|\bis acquitted\b", low)
    if m:
        parts.append("Not guilty – the accused was acquitted")
    else:
        m = re.search(r"\b(?:found guilty|convicted) of ([^.;,]+?)(?: as charged)?(?:[.;,]|$)", t, re.IGNORECASE)
        if m:
            parts.append(f"Guilty of {m.group(1).strip()}")
    m = re.search(r"\bsentenced to ([^.;]+)", t, re.IGNORECASE)
    if m:
        parts.append(f"Sentence: {m.group(1).strip()}")
    if not parts:
        m = re.search(r"\b(appeal|application|claim|review|action)s? (?:is|are) (?:hereby )?"
                      r"(dismissed|allowed|upheld|granted|refused|struck off)", low)
        if m:
            word = {"allowed": "allowed (succeeded)", "upheld": "upheld (succeeded)",
                    "granted": "granted (succeeded)", "dismissed": "dismissed (failed)",
                    "refused": "refused (failed)", "struck off": "struck off the roll"}[m.group(2)]
            parts.append(f"The {m.group(1)} was {word}")
    if parts and re.search(r"\bwith costs\b|\bshall pay\b.*\bcosts\b|\bpay (?:the )?[a-z' ]*costs\b", low):
        parts.append("costs ordered")
    if not parts:
        return None
    head = "; ".join(parts)
    return head[:1].upper() + head[1:] + "."


# ---------------------------------------------------------------------------
# The summarizer
# ---------------------------------------------------------------------------

def summarize(text, glossary=None):
    """Returns:
        case_details  {case_number, parties, court, judge, date}
        summary       a short plain paragraph: what the case was about and the result
        key_points    the main facts and the court's own findings (no quotations of other cases)
        decision      the court's decision as a list (order items, or verdict/sentence)
        headline      a one-line result, e.g. "Guilty of murder; Sentence: 20 years imprisonment."
        cases_cited   names of other cases the judgment relies on
        acts          Acts of Parliament named in the judgment
        legal_terms   glossary words found in the text (to look up in the Dictionary)
    """
    glossary = glossary or {}
    details, body = _split_heading(text or "")
    sentences = _sentences(body)

    kind = "case"
    low_all = (text or "").lower()
    if re.search(r"\bthe state\b|\bthe accused\b|\bs v\b", low_all):
        kind = "criminal trial"
    if re.search(r"\bappellant\b|\bthis is an appeal\b", low_all):
        kind = "appeal"
    elif re.search(r"\bapplicant\b|\bthis is an application\b", low_all) and kind != "criminal trial":
        kind = "application"
    elif re.search(r"\bplaintiff\b", low_all) and kind == "case":
        kind = "civil claim"

    about, facts, defence, findings, cases = [], [], [], [], []
    for s in sentences:
        if _is_precedent(s):
            cases.extend(_case_names(s))
            continue
        if DECISION.search(s) and not ABOUT.search(s):
            continue          # handled as the decision
        if ABOUT.search(s) and len(about) < 1:
            about.append(s)
        elif ALLEGATION.search(s):
            facts.append(s)
        elif DEFENCE.search(s):
            defence.append(s)
        elif FINDING.search(s):
            findings.append(s)

    # If nothing said what the case is about, the first real sentence usually does.
    if not about:
        for s in sentences:
            if not _is_precedent(s) and not DECISION.search(s):
                about.append(s)
                break

    decision = _numbered_order(body)
    if not decision:
        decision = _decision_sentences(sentences)
    decision = _unique(decision)
    headline = _headline(" ".join(decision), kind) if decision else None

    key_points = _unique(about[:1] + facts[:2] + defence[:2] + findings[:3])[:6]

    # The summary paragraph.
    intro = []
    where = details["court"].title().replace(" Of ", " of ") if details["court"] else None
    if details["parties"] or where:
        bits = f"This is {'an' if kind[0] in 'aeiou' else 'a'} {kind}"
        if details["parties"]:
            bits += f", {_title(details['parties'])}"
        if where:
            bits += f", in the {where}"
        if details["judge"]:
            bits += f", decided by {details['judge']}"
        intro.append(bits + ".")
    summary_parts = intro + about[:1] + facts[:1]
    if findings:
        summary_parts.append(findings[-1])
    if headline:
        summary_parts.append("Result: " + headline)
    elif decision:
        items = [d.rstrip(".") for d in decision[:4]]
        items = [items[0]] + [i[:1].lower() + i[1:] if not i.startswith(("I ", "The State")) else i for i in items[1:]]
        summary_parts.append("The court decided: " + "; ".join(items) + ".")
    summary = " ".join(_unique(summary_parts))

    acts = _unique(_acts(" ".join((text or "").split())))
    lowered = (text or "").lower()
    found_terms = []
    for term in glossary:
        m = re.search(r"\b" + re.escape(term) + r"\b", lowered)
        if m:
            found_terms.append((m.start(), term))
    legal_terms = [t for _pos, t in sorted(found_terms)]
    # "default judgment" already covers "judgment" when both are there.
    legal_terms = [t for t in legal_terms if not any(t != o and t in o for o in legal_terms)]

    return {
        "case_details": {k: v for k, v in details.items() if v},
        "summary": summary,
        "key_points": key_points,
        "decision": decision,
        "headline": headline,
        "cases_cited": _unique(cases)[:8],
        "acts": acts[:6],
        "legal_terms": legal_terms[:10],
        "kind": kind,
    }


# ---------------------------------------------------------------------------
# A short digest for the AI model
# ---------------------------------------------------------------------------

def digest(text, max_chars=4500):
    """The parts of a judgment that matter for a summary, in a few thousand
    characters: the case details, what it is about, the facts and evidence,
    the defence, the court's findings and -- always kept in full -- the
    decision. Quotations of other cases are left out.

    Used so that a long judgment needs only ONE request to the AI model,
    instead of one request per part (which takes many minutes on a computer
    without a graphics card)."""
    details, body = _split_heading(text or "")
    sentences = _sentences(body)
    about, facts, defence, findings, other = [], [], [], [], []
    for s in sentences:
        if _is_precedent(s):
            continue
        if ABOUT.search(s) and not about:
            about.append(s)
        elif ALLEGATION.search(s):
            facts.append(s)
        elif DEFENCE.search(s):
            defence.append(s)
        elif FINDING.search(s):
            findings.append(s)
        elif not DECISION.search(s):
            other.append(s)
    decision = _numbered_order(body) or _decision_sentences(sentences)
    if not about and other:
        about.append(other.pop(0))

    head = [f"{k.replace('_', ' ').title()}: {v}" for k, v in details.items() if v]
    fixed = (("CASE DETAILS", head), ("THE COURT'S DECISION (from the end of the judgment)", decision))
    flexible = [("WHAT THE CASE IS ABOUT", about), ("FACTS AND EVIDENCE", facts),
                ("THE DEFENCE / THE OTHER SIDE'S CASE", defence), ("THE COURT'S FINDINGS", findings),
                ("OTHER PARTS OF THE JUDGMENT", other)]

    def block(title, items):
        return f"{title}:\n" + "\n".join(f"- {i}" for i in items) + "\n\n"

    out = "".join(block(t, i) for t, i in fixed if i)
    budget = max_chars - len(out)
    # Share the room fairly: take sentences from each section in turn.
    taken = {t: [] for t, _ in flexible}
    progress = True
    while progress and budget > 0:
        progress = False
        for title, items in flexible:
            already = len(taken[title])
            if already < len(items) and len(items[already]) + 4 <= budget:
                taken[title].append(items[already])
                budget -= len(items[already]) + 4
                progress = True
    middle = "".join(block(t, taken[t]) for t, _ in flexible if taken[t])
    return out.replace(block(fixed[1][0], decision), middle + block(fixed[1][0], decision)) if decision \
        else out + middle
