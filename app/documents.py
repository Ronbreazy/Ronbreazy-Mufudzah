"""
documents.py
------------
The Document Wizard's templates. Each template says:

  - label / description   what the wizard shows when choosing a document
  - courts                which courts it can be used in (None = any)
  - fields                the questions the wizard asks, in order
  - build                 the function that lays the document out

The frontend asks GET /documents/templates for this list and builds its form
from it, so a new template added here appears in the wizard automatically.

A built document is a list of "blocks" (see app/doc_export.py for the
styles). The same blocks become the on-screen preview, the plain-text copy,
the Word file and the PDF, so all four always match.

IMPORTANT: these are DRAFTING AIDS, not forms approved by any court. They
follow the general shape of documents filed in Zimbabwean courts, but every
template must be reviewed by a registered Zimbabwean legal practitioner
before the system is used for real cases, and each generated document
carries warnings telling the self actor to have it checked before filing.
"""

import datetime
import re

from app.legal_content import COURT_NAMES, CASE_NO_PREFIX, COURT_SOURCES

COURT_LABELS = {
    "magistrates_court": "Magistrates Court",
    "high_court": "High Court",
    "supreme_court": "Supreme Court",
    "constitutional_court": "Constitutional Court",
}

REVIEW_WARNING = ("This is an auto-generated draft, not a substitute for advice from a qualified "
                  "legal practitioner. Have it reviewed before filing.")
BLANK_WARNING = "Some fields were left blank and have been marked with placeholders."


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _value(details, name, placeholder):
    value = str(details.get(name) or "").strip()
    return value if value else f"[{placeholder}]"


def _paragraphs(text):
    """A textarea answer -> separate paragraphs. A blank line, or a new line,
    starts a new paragraph; numbering the person typed ("1.", "a)") is
    removed because the document numbers the paragraphs itself."""
    parts = [p.strip() for p in re.split(r"\n+", str(text or "")) if p.strip()]
    return [re.sub(r"^\(?([0-9]{1,3}|[a-zA-Z])[.)]\s+", "", p) for p in parts]


def _ordinal(n):
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def _dated(place):
    today = datetime.date.today()
    return f"DATED AT {place.upper()} THIS {_ordinal(today.day)} DAY OF {today.strftime('%B %Y').upper()}"


def _to_blocks(court_tier, location, others=()):
    """The TO: block at the end of a document (the Registrar or Clerk of
    Court), then an AND TO: block for each other person served."""
    if court_tier == "magistrates_court":
        office = f"THE CLERK OF COURT\nMagistrates Court\n{location.upper()}"
    else:
        office = f"THE REGISTRAR\n{COURT_LABELS.get(court_tier, 'High Court')} of Zimbabwe\n{location.upper()}"
    blocks = [{"style": "to", "label": "TO:", "text": office}]
    for text in others:
        blocks.append({"style": "to", "label": "AND TO:", "text": text})
    return blocks


def _heading_blocks(court_tier, location, case_number, applicant, respondent, roles=("Applicant", "Respondent")):
    """The heading every court document starts with: the court, where it
    sits, the case number and the parties."""
    # Laid out like a filed court document: the court on the left with the
    # case number on the same line, HELD AT under it, the parties with their
    # roles in capitals, and (added by each template) the document's name
    # between two lines.
    year = datetime.datetime.now().strftime("%y")
    prefix = CASE_NO_PREFIX.get(court_tier, "CASE")
    number = case_number.upper() if case_number else f"{prefix} ……/{year}"
    if not number.startswith("CASE NO"):
        number = f"CASE NO. {number}"
    return [
        {"style": "courtcase", "text": f"IN THE {COURT_NAMES.get(court_tier, 'COURT OF ZIMBABWE')}", "right": number},
        {"style": "courtleft", "text": f"HELD AT {location.upper()}"},
        {"style": "small", "text": "In the matter between:"},
        {"style": "blank"},
        {"style": "party", "text": applicant.upper(), "role": roles[0].upper()},
        {"style": "partysep", "text": "And"},
        {"style": "party", "text": respondent.upper(), "role": roles[1].upper()},
    ]


# ---------------------------------------------------------------------------
# Fields shared by several templates
# ---------------------------------------------------------------------------

# The system is built for the Harare courts (the area of study), so every
# document is filed at Harare and the person isn't asked for a town.
COURT_TOWN = "Harare"
CASE_NUMBER_FIELD = {"name": "case_number", "label": "Case number (if the case already has one)", "type": "text",
                     "required": False, "placeholder": "e.g. HC 1234/26",
                     "help": "Leave blank for a new case; the registry gives the number when you file."}


# ---------------------------------------------------------------------------
# Template: affidavit (founding or opposing)
# ---------------------------------------------------------------------------

def _build_affidavit(court_tier, d):
    founding = d.get("affidavit_kind") != "opposing"
    deponent = _value(d, "deponent_name", "YOUR FULL NAME")
    other = _value(d, "other_party_name", "OTHER PARTY'S NAME")
    location = COURT_TOWN
    applicant, respondent = (deponent, other) if founding else (other, deponent)
    my_role = "Applicant" if founding else "Respondent"
    their_role = "Respondent" if founding else "Applicant"

    occupation = str(d.get("deponent_occupation") or "").strip()
    id_number = str(d.get("id_number") or "").strip()
    about_me = f"I am the {my_role} in this matter. I am an adult"
    about_me += f" {occupation}" if occupation else ""
    about_me += f" residing at {_value(d, 'deponent_address', 'YOUR ADDRESS')}."
    if id_number:
        about_me += f" My national identity number is {id_number}."

    blocks = _heading_blocks(court_tier, location, str(d.get("case_number") or "").strip(), applicant, respondent)
    blocks.append({"style": "title", "text": "FOUNDING AFFIDAVIT" if founding else "OPPOSING AFFIDAVIT"})
    blocks.append({"style": "para", "text": f"I, the undersigned, {deponent.upper()}, do hereby make oath and state that:"})

    paras = [
        about_me,
        "The facts set out in this affidavit are within my own personal knowledge, unless the context shows "
        "otherwise, and are to the best of my knowledge and belief both true and correct.",
    ]
    other_address = str(d.get("other_party_address") or "").strip()
    paras.append(f"The {their_role} is {other.upper()}" + (f", of {other_address}." if other_address else "."))
    if not founding:
        paras.append(f"I have read the {their_role}'s application and the affidavit filed in support of it, "
                     "and I oppose the application for the reasons set out below.")

    number = 0
    for text in paras:
        number += 1
        blocks.append({"style": "num", "num": f"{number}.", "text": text})

    blocks.append({"style": "heading", "text": "THE FACTS" if founding else "MY ANSWER TO THE APPLICATION"})
    facts = _paragraphs(d.get("facts")) or ["[Set out the facts, one point per paragraph.]"]
    for text in facts:
        number += 1
        blocks.append({"style": "num", "num": f"{number}.", "text": text})

    blocks.append({"style": "heading", "text": "RELIEF SOUGHT" if founding else "CONCLUSION"})
    relief = str(d.get("relief_sought") or "").strip()
    number += 1
    if founding:
        text = ("WHEREFORE I pray for an order in the following terms: "
                + (relief if relief else "[describe the order you are asking the court to make]"))
    else:
        text = "WHEREFORE I pray that the application be dismissed" + (f", and that {relief}" if relief else "") + "."
    blocks.append({"style": "num", "num": f"{number}.", "text": text, "keep_with_next": True})

    blocks.append({"style": "sig", "text": "DEPONENT", "keep_with_next": True})
    blocks.append({"style": "para", "text":
                   "Thus sworn to (or affirmed) at ______________________ on this ______ day of "
                   "______________________ 20____, before me, the deponent having acknowledged that he/she "
                   "knows and understands the contents of this affidavit.", "keep_with_next": True})
    blocks.append({"style": "sig", "text": "COMMISSIONER OF OATHS\nFull names: ______________________\n"
                                           "Designation: ______________________\nAddress: ______________________"})

    warnings = [
        "Do NOT sign this affidavit at home. You must sign it in front of a Commissioner of Oaths (for example "
        "at a police station, a court or a lawyer's office), who signs and stamps it at the same time.",
        "Everything in an affidavit must be true. Making a false statement under oath is a criminal offence.",
    ]
    if not founding:
        warnings.append("An opposing affidavit is filed together with a Notice of Opposition, and there is a strict "
                        "time limit after you were served. Ask the court registry for your deadline immediately.")
    title = "Founding Affidavit" if founding else "Opposing Affidavit"
    return blocks, title, warnings


# ---------------------------------------------------------------------------
# Template: notice of opposition
# ---------------------------------------------------------------------------

def _build_notice_of_opposition(court_tier, d):
    applicant = _value(d, "applicant_name", "NAME OF THE PERSON WHO BROUGHT THE APPLICATION")
    respondent = _value(d, "respondent_name", "YOUR FULL NAME")
    location = COURT_TOWN
    case_number = _value(d, "case_number", "CASE NUMBER ON THE APPLICATION")
    address = _value(d, "service_address", "YOUR ADDRESS FOR SERVICE")

    blocks = _heading_blocks(court_tier, location, case_number, applicant, respondent)
    blocks.append({"style": "title", "text": "NOTICE OF OPPOSITION"})
    blocks.append({"style": "para", "text": f"TAKE NOTICE that the Respondent, {respondent.upper()}, intends to "
                                            "oppose the application made in this matter."})
    blocks.append({"style": "para", "text": "TAKE NOTICE FURTHER that the Respondent's opposing affidavit is "
                                            "filed together with this notice, and that the Respondent, who is "
                                            "acting in person, will accept notice and service of all process and "
                                            f"documents in these proceedings at: {address}."})
    blocks.append({"style": "dated", "text": _dated(location)})

    contact = ["RESPONDENT (acting in person)", address]
    for key, label in (("phone", "Tel"), ("email", "Email")):
        value = str(d.get(key) or "").strip()
        if value:
            contact.append(f"{label}: {value}")
    blocks.append({"style": "sigblock", "text": "\n".join(contact)})

    applicant_address = str(d.get("applicant_address") or "").strip() or "[Applicant's or their lawyers' address]"
    blocks.extend(_to_blocks(court_tier, location, [f"{applicant.upper()}\nAPPLICANT\n{applicant_address}"]))

    warnings = [
        "There is a strict time limit for filing a Notice of Opposition after you were served with the "
        "application. If you miss it, the court may decide the case without hearing you. Check your "
        "deadline with the court registry immediately.",
        "A Notice of Opposition must be filed together with an opposing affidavit. Use the Affidavit template "
        "and choose 'Opposing'.",
        "After filing, a copy must be served on the Applicant (or their lawyers), and proof of service kept.",
    ]
    return blocks, "Notice of Opposition", warnings


# ---------------------------------------------------------------------------
# Template: application for a protection order (Domestic Violence Act)
# ---------------------------------------------------------------------------

# (key, how it reads in the order, how it reads on the form)
PROTECTION_ORDER_OPTIONS = [
    ("no_violence", "not to commit any act of domestic violence against me",
     "must not commit any act of domestic violence against me"),
    ("no_contact", "not to contact me by phone, text message, social media or through other people",
     "must not contact me by phone, text message, social media or through other people"),
    ("stay_away_home", "not to enter or come near my home", "must not enter or come near my home"),
    ("stay_away_work", "not to come near my place of work", "must not come near my place of work"),
    ("protect_children", "not to threaten, harm or harass my children or other members of my household",
     "must not threaten, harm or harass my children or other members of my household"),
    ("belongings", "to allow me to collect my personal belongings from our shared home",
     "must allow me to collect my personal belongings from our shared home"),
    ("weapons", "to hand over to the police any firearm or other weapon in their possession",
     "must hand over to the police any firearm or other weapon in their possession"),
]


def _build_protection_order(court_tier, d):
    complainant = _value(d, "complainant_name", "YOUR FULL NAME")
    respondent = _value(d, "respondent_name", "NAME OF THE PERSON YOU NEED PROTECTION FROM")
    location = COURT_TOWN

    blocks = _heading_blocks("magistrates_court", location, str(d.get("case_number") or "").strip(),
                             complainant, respondent, roles=("Complainant", "Respondent"))
    blocks.append({"style": "title", "text": "APPLICATION FOR A PROTECTION ORDER"})
    blocks.append({"style": "court", "text": "(Domestic Violence Act [Chapter 5:16])"})
    blocks.append({"style": "blank"})

    blocks.append({"style": "heading", "text": "1. DETAILS OF THE COMPLAINANT"})
    blocks.append({"style": "para", "text": f"Full name: {complainant}\n"
                                            f"Address: {_value(d, 'complainant_address', 'YOUR ADDRESS')}"
                   + (f"\nPhone: {d.get('complainant_phone').strip()}" if str(d.get('complainant_phone') or '').strip() else "")})

    blocks.append({"style": "heading", "text": "2. DETAILS OF THE RESPONDENT"})
    blocks.append({"style": "para", "text": f"Full name: {respondent}\n"
                                            f"Address: {_value(d, 'respondent_address', 'THEIR ADDRESS, IF KNOWN')}\n"
                                            f"Relationship to me: {_value(d, 'relationship', 'HOW YOU ARE RELATED')}"})

    blocks.append({"style": "heading", "text": "3. THE ACTS OF DOMESTIC VIOLENCE"})
    blocks.append({"style": "para", "text": "The Respondent has committed the following acts of domestic violence "
                                            "against me (with the dates on which they happened, as far as I know them):"})
    acts = _paragraphs(d.get("acts")) or ["[Describe what happened, when and where, one incident per paragraph.]"]
    for i, text in enumerate(acts, start=1):
        blocks.append({"style": "num", "num": f"3.{i}", "text": text})

    children = str(d.get("children") or "").strip()
    if children:
        blocks.append({"style": "heading", "text": "4. CHILDREN AFFECTED"})
        blocks.append({"style": "para", "text": children})

    section = 5 if children else 4
    blocks.append({"style": "heading", "text": f"{section}. THE ORDER I AM ASKING FOR"})
    blocks.append({"style": "para", "text": "I ask the court to grant a protection order directing the Respondent:"})
    chosen = d.get("orders") or []
    if isinstance(chosen, str):
        chosen = [chosen]
    letters = "abcdefghijklmnopqrstuvwxyz"
    items = [text for key, text, _label in PROTECTION_ORDER_OPTIONS if key in chosen]
    items += _paragraphs(d.get("other_orders"))
    if not items:
        items = ["[choose the protections you need]"]
    for i, text in enumerate(items):
        blocks.append({"style": "num", "num": f"({letters[i % 26]})", "text": text + ("." if i == len(items) - 1 else ";")})

    if str(d.get("urgent") or "").lower() in ("yes", "true", "1", "on"):
        section += 1
        blocks.append({"style": "heading", "text": f"{section}. URGENCY"})
        blocks.append({"style": "para", "text": "I am in immediate danger. I ask the court to grant an interim "
                                                "protection order today, before the Respondent is heard, to protect "
                                                "me until the matter is finally decided."})

    blocks.append({"style": "heading", "text": "DECLARATION"})
    blocks.append({"style": "para", "text": "I declare that the information in this application is true and correct "
                                            "to the best of my knowledge."})
    blocks.append({"style": "dated", "text": _dated(location)})
    blocks.append({"style": "sigblock", "text": "COMPLAINANT"})

    warnings = [
        "Protection orders are applied for at the Magistrates Court. The court has an official application form "
        "and the clerk of court will help you complete it free of charge; take this draft with you so you have "
        "all the details ready.",
        "If you are in immediate danger, go to the nearest police station or call the police on 995 first.",
        "You may be asked to confirm this information under oath in front of the clerk of court or a magistrate.",
    ]
    return blocks, "Application for a Protection Order", warnings


# ---------------------------------------------------------------------------
# Template: the original simple documents (summons, application, appeal)
# ---------------------------------------------------------------------------

def _build_simple(court_tier, d, label):
    applicant = _value(d, "applicant_name", "APPLICANT NAME")
    respondent = _value(d, "respondent_name", "RESPONDENT NAME")
    location = COURT_TOWN
    blocks = _heading_blocks(court_tier, location, str(d.get("case_number") or "").strip(), applicant, respondent)
    blocks.append({"style": "title", "text": label.upper()})
    blocks.append({"style": "heading", "text": "ORDER SOUGHT:"})
    blocks.append({"style": "para", "text": _value(d, "relief_sought", "Describe the order you are asking the court for.")})
    blocks.append({"style": "dated", "text": _dated(location)})
    blocks.append({"style": "sigblock", "text": "APPLICANT\n(acting in person)"})
    blocks.extend(_to_blocks(court_tier, location, [f"{respondent.upper()}\nRESPONDENT"]))
    return blocks, label, []


# ---------------------------------------------------------------------------
# Templates: summons
# ---------------------------------------------------------------------------
# Two summonses, both for the Harare courts:
#   * a civil summons in the Magistrates Court (money owed, a contract,
#     damages) -- the defendant has 7 days to enter appearance; and
#   * a divorce summons in the High Court -- 10 days to enter appearance.
# Each has the parts a valid summons needs: the court heading with the
# registry and a blank case number, the command to the officer who serves it
# (Messenger of Court / Sheriff), the time to respond, the numbered list of
# what the Plaintiff claims, the warning about judgment in default, and the
# signature and address-for-service blocks. The Declaration (particulars of
# claim) is a separate document attached to the summons.

def _money(value):
    value = str(value or "").strip()
    if not value:
        return None
    value = value.replace("US$", "").replace("USD", "").lstrip("$").replace(",", "").strip()
    if re.fullmatch(r"\d+(\.\d{1,2})?", value):
        whole, _, cents = value.partition(".")
        value = f"{int(whole):,}" + (f".{cents.ljust(2, '0')}" if cents else "")
    return value


def _plaintiff_contact(d, address_label):
    lines = ["PLAINTIFF", "(acting in person)",
             f"{address_label}: {_value(d, 'plaintiff_address', 'YOUR PHYSICAL ADDRESS')}"]
    postal = str(d.get("plaintiff_postal_address") or "").strip()
    if postal:
        lines.append(f"Postal address: {postal}")
    phone = str(d.get("plaintiff_phone") or "").strip()
    if phone:
        lines.append(f"Tel: {phone}")
    return "\n".join(lines)


def _build_civil_summons(court_tier, d):
    """Magistrates Court civil summons (Harare)."""
    plaintiff = _value(d, "plaintiff_name", "YOUR FULL NAME")
    defendant = _value(d, "defendant_name", "DEFENDANT'S FULL NAME")
    p_address = _value(d, "plaintiff_address", "YOUR PHYSICAL ADDRESS")
    d_address = _value(d, "defendant_address", "DEFENDANT'S PHYSICAL ADDRESS")
    location = COURT_TOWN
    warnings = []
    if court_tier != "magistrates_court":
        warnings.append("This civil summons is issued out of the Magistrates Court, so it has been prepared for the "
                        "Magistrates Court. A claim above the Magistrates Court's monetary limit is brought in the "
                        "High Court: ask a legal practitioner or the Legal Aid Directorate for help with that summons.")
    case_number = str(d.get("case_number") or "").strip()
    year = datetime.datetime.now().strftime("%y")
    number = case_number.upper() if case_number else f"CIV ……/{year}"
    if not number.startswith("CASE NO"):
        number = f"CASE NO. {number}"

    blocks = [
        {"style": "courtleft", "text": f"IN THE COURT OF THE MAGISTRATE FOR THE PROVINCE OF {location.upper()}"},
        {"style": "courtcase", "text": f"HELD AT {location.upper()}", "right": number},
        {"style": "small", "text": "In the matter between:"},
        {"style": "blank"},
        {"style": "party", "text": plaintiff.upper(), "role": "PLAINTIFF"},
        {"style": "partysep", "text": "And"},
        {"style": "party", "text": defendant.upper(), "role": "DEFENDANT"},
        {"style": "title", "text": "CIVIL SUMMONS"},
        {"style": "heading", "text": "To: The Messenger of Court or his/her lawful Deputy:"},
        {"style": "para", "text": f"COMMAND {defendant.upper()} (Defendant) of {d_address} that within 7 (seven) days "
                                  "after the service of this summons upon him/her (exclusive of the day of service, "
                                  "weekends and public holidays), he/she do enter an appearance to defend with the "
                                  f"Clerk of the Court at {location} to answer the claim of {plaintiff.upper()} "
                                  f"(Plaintiff) of {p_address}."},
        {"style": "heading", "text": "The Plaintiff\u2019s claim against the Defendant is for:"},
    ]
    amount = _money(d.get("claim_amount"))
    reason = str(d.get("claim_reason") or "").strip().rstrip(".")
    items = [f"Payment of the sum of US${amount or '……………'} being {reason or '[WHAT THE MONEY IS FOR, e.g. money lent and advanced]'}."]
    rate = str(d.get("interest_rate") or "").strip().rstrip("%")
    since = str(d.get("interest_from") or "").strip()
    if rate or since:
        items.append(f"Interest thereon at the prescribed rate{f' of {rate}%' if rate else ''} per annum from "
                     f"{since or 'the date of summons'} to the date of payment.")
    items.append("Costs of suit.")
    for n, text in enumerate(items, start=1):
        blocks.append({"style": "num", "num": f"({n})", "text": text})
    blocks += [
        {"style": "heading", "text": "TAKE NOTE THAT:"},
        {"style": "para", "text": "If you, the Defendant, fail to enter an appearance to defend within the specified "
                                  "7 days, judgment may be given against you without further notice."},
        {"style": "dated", "text": _dated(location)},
        {"style": "sigblock", "text": "CLERK OF THE COURT"},
        {"style": "sigblock", "text": _plaintiff_contact(d, "Physical address for service")},
    ]
    warnings += [
        "The summons must be issued (signed and stamped) by the Clerk of the Court, who gives it a case number "
        "when you pay the filing fee. Take it to the Civil Court registry in Harare, or file it on IECMS.",
        "The Messenger of Court then serves it on the Defendant and returns proof of service to the court.",
        "Attach your particulars of claim (the facts showing why the money is owed) to the summons.",
    ]
    return blocks, "Civil Summons", warnings


def _build_divorce_summons(court_tier, d):
    """High Court divorce summons (Harare)."""
    plaintiff = _value(d, "plaintiff_name", "YOUR FULL NAME")
    defendant = _value(d, "defendant_name", "DEFENDANT'S FULL NAME")
    d_address = _value(d, "defendant_address", "DEFENDANT'S ADDRESS")
    location = COURT_TOWN
    warnings = []
    if court_tier != "high_court":
        warnings.append("A divorce summons is issued out of the High Court, so it has been prepared for the High Court.")
    case_number = str(d.get("case_number") or "").strip()

    blocks = [{"style": "formref", "text": "Form No 29\nSummons in matrimonial action\nRule 68 (1)"}]
    blocks += _heading_blocks("high_court", location, case_number, plaintiff, defendant, roles=("Plaintiff", "Defendant"))
    blocks += [
        {"style": "title", "text": "DIVORCE SUMMONS"},
        {"style": "para", "text": f"To the Defendant: {defendant.upper()}"},
        {"style": "para", "text": f"Of: {d_address}"},
        {"style": "heading", "text": "TAKE NOTICE THAT:"},
        {"style": "para", "text": "You are hereby summoned that, within 10 (ten) days after the service of this summons "
                                  "upon you (excluding weekends and public holidays), you must enter an appearance to "
                                  "defend at the Registry of the High Court of Zimbabwe at "
                                  f"{location} if you intend to defend the Plaintiff\u2019s action."},
        {"style": "para", "text": "The Plaintiff\u2019s claim against you is for a decree of divorce and ancillary "
                                  "relief on the grounds of irretrievable breakdown of the marriage, as more fully "
                                  "appears from the attached Annexure \u201cA\u201d (Particulars of Claim / "
                                  "Declaration), for:"},
    ]
    items = ["A decree of divorce."]
    children = str(d.get("children") or "").strip().rstrip(".")
    if children:
        to = "the Plaintiff" if d.get("custody_to") != "defendant" else "the Defendant"
        other = "the Defendant" if to == "the Plaintiff" else "the Plaintiff"
        items.append(f"Custody of the minor child(ren), namely {children}, be awarded to {to}, subject to "
                     f"reasonable rights of access by {other}.")
        child_m = _money(d.get("child_maintenance"))
        if child_m:
            items.append(f"Maintenance for the minor child(ren) in the sum of US${child_m} per month per child until "
                         "they attain majority status or become self-supporting.")
    spouse_m = _money(d.get("spouse_maintenance"))
    if spouse_m:
        until = "remarriage" if d.get("maintenance_until") == "remarriage" else "death or remarriage"
        items.append(f"Maintenance for the Plaintiff in the sum of US${spouse_m} per month until {until}.")
    to_plaintiff = str(d.get("assets_plaintiff") or "").strip().rstrip(".")
    to_defendant = str(d.get("assets_defendant") or "").strip().rstrip(".")
    if to_plaintiff or to_defendant:
        items.append(("An order dividing the matrimonial assets and property of the parties as follows:",
                      [f"Plaintiff to be awarded: {to_plaintiff or 'nil'};",
                       f"Defendant to be awarded: {to_defendant or 'nil'}."]))
    items.append("Costs of suit." if d.get("costs") != "own" else "Each party to bear its own costs.")
    for n, item in enumerate(items, start=1):
        text, subs = item if isinstance(item, tuple) else (item, [])
        blocks.append({"style": "num", "num": f"{n}.", "text": text})
        for letter, sub in zip("abcdefgh", subs):
            blocks.append({"style": "num", "num": f"({letter})", "text": sub, "indent": 1})
    blocks += [
        {"style": "para", "text": "If you fail to enter an appearance within the time stated, judgment may be given "
                                  "against you in your absence."},
        {"style": "para", "text": "The Sheriff of Zimbabwe or his/her lawful Deputy is directed to serve this summons "
                                  "on the Defendant and to return proof of service to the Registrar."},
        {"style": "dated", "text": _dated(location)},
        {"style": "sigblock", "text": "REGISTRAR OF THE HIGH COURT"},
        {"style": "sigblock", "text": _plaintiff_contact(d, "Address for service (within 5 km of the Court)")},
    ]
    warnings += [
        "Attach your Declaration (Annexure \u201cA\u201d) to this summons: when and where you married, why the "
        "marriage has broken down, the children, maintenance and the property.",
        "The Registrar issues (signs and stamps) the summons and gives it a case number when you file it with "
        "the fee, at the High Court in Harare or on IECMS. The Sheriff then serves it on the Defendant.",
    ]
    return blocks, "Divorce Summons", warnings


_MONEY_HELP = "Numbers only, in US dollars, e.g. 500 or 1250.50"

_CIVIL_SUMMONS_FIELDS = [
    {"name": "plaintiff_name", "label": "Your full name (you are the Plaintiff)", "type": "text", "required": True,
     "placeholder": "e.g. Tendai Moyo"},
    {"name": "plaintiff_address", "label": "Your physical address for service", "type": "text", "required": True,
     "placeholder": "Where court papers can be delivered to you"},
    {"name": "plaintiff_postal_address", "label": "Your postal address", "type": "text", "required": False},
    {"name": "plaintiff_phone", "label": "Your phone number", "type": "tel", "required": False,
     "placeholder": "e.g. 0772123456 or +263772123456"},
    {"name": "defendant_name", "label": "Full name of the person you are suing (the Defendant)", "type": "text",
     "required": True},
    {"name": "defendant_address", "label": "The Defendant's physical home or business address", "type": "text",
     "required": True, "help": "The Messenger of Court serves the summons at this address."},
    {"name": "claim_amount", "label": "Amount you are claiming (US$)", "type": "money", "required": True,
     "placeholder": "e.g. 500", "help": _MONEY_HELP},
    {"name": "claim_reason", "label": "What the money is for", "type": "text", "required": True,
     "placeholder": "e.g. money lent and advanced / outstanding rental / goods sold and delivered"},
    {"name": "interest_rate", "label": "Interest rate per year (%), if any", "type": "money", "required": False,
     "placeholder": "e.g. 5"},
    {"name": "interest_from", "label": "Interest from (date)", "type": "text", "required": False,
     "placeholder": "e.g. 1 March 2026"},
    CASE_NUMBER_FIELD,
]

_DIVORCE_SUMMONS_FIELDS = [
    {"name": "plaintiff_name", "label": "Your full name (you are the Plaintiff)", "type": "text", "required": True,
     "placeholder": "e.g. Sebastian Chiwanza"},
    {"name": "plaintiff_address", "label": "Your address for service (within 5 km of the High Court, Harare)",
     "type": "text", "required": True},
    {"name": "plaintiff_phone", "label": "Your phone number", "type": "tel", "required": False,
     "placeholder": "e.g. 0772123456 or +263772123456"},
    {"name": "defendant_name", "label": "Your spouse's full name (the Defendant)", "type": "text", "required": True,
     "placeholder": "e.g. Catherine Jim"},
    {"name": "defendant_address", "label": "Your spouse's address", "type": "text", "required": True,
     "help": "The Sheriff serves the summons at this address."},
    {"name": "children", "label": "Names of the minor children (if any)", "type": "names", "required": False,
     "placeholder": "e.g. Tadisa Chiwanza and Talinda Chiwanza"},
    {"name": "custody_to", "label": "Who should have custody of the children?", "type": "select", "required": False,
     "options": [{"value": "plaintiff", "label": "Me (the Plaintiff)"},
                 {"value": "defendant", "label": "My spouse (the Defendant)"}]},
    {"name": "child_maintenance", "label": "Maintenance per child per month (US$), if asked for", "type": "money",
     "required": False, "placeholder": "e.g. 100", "help": _MONEY_HELP},
    {"name": "spouse_maintenance", "label": "Maintenance for yourself per month (US$), if asked for", "type": "money",
     "required": False, "placeholder": "e.g. 150"},
    {"name": "maintenance_until", "label": "Your maintenance to last until", "type": "select", "required": False,
     "options": [{"value": "death_or_remarriage", "label": "Death or remarriage"},
                 {"value": "remarriage", "label": "Remarriage"}]},
    {"name": "assets_plaintiff", "label": "Property you ask to be given to you", "type": "textarea", "required": False,
     "placeholder": "e.g. the house at 12 Example Road, Mabelreign, and the Toyota Corolla"},
    {"name": "assets_defendant", "label": "Property to be given to your spouse", "type": "textarea", "required": False},
    {"name": "costs", "label": "Costs", "type": "select", "required": False,
     "options": [{"value": "suit", "label": "The Defendant pays the costs of suit"},
                 {"value": "own", "label": "Each party pays its own costs"}]},
    CASE_NUMBER_FIELD,
]


_SIMPLE_FIELDS = [
    {"name": "applicant_name", "label": "Your name", "type": "text", "required": True, "placeholder": "Full name"},
    {"name": "respondent_name", "label": "Other party", "type": "text", "required": True,
     "placeholder": "Respondent / Defendant"},
    {"name": "relief_sought", "label": "Order sought", "type": "textarea", "required": True,
     "placeholder": "Describe the order you are asking the court to make..."},
]

TEMPLATES = {
    "affidavit": {
        "label": "Affidavit (founding or opposing)",
        "description": "A sworn statement of the facts. A founding affidavit supports an application you are "
                       "bringing; an opposing affidavit answers an application brought against you.",
        "courts": ["magistrates_court", "high_court", "supreme_court", "constitutional_court"],
        "fields": [
            {"name": "affidavit_kind", "label": "Which affidavit?", "type": "select", "required": True,
             "options": [{"value": "founding", "label": "Founding: I am bringing the application"},
                         {"value": "opposing", "label": "Opposing: I am answering an application against me"}]},
            {"name": "deponent_name", "label": "Your full name", "type": "text", "required": True,
             "placeholder": "e.g. Rudo Dube"},
            {"name": "deponent_address", "label": "Your residential address", "type": "text", "required": True,
             "placeholder": "e.g. 12 Example Road, Mabelreign, Harare"},
            {"name": "deponent_occupation", "label": "Your occupation", "type": "text", "required": False,
             "placeholder": "e.g. teacher"},
            {"name": "id_number", "label": "National ID number", "type": "idnumber", "required": False,
             "placeholder": "e.g. 63-123456 A 75"},
            {"name": "other_party_name", "label": "The other party's full name", "type": "text", "required": True},
            {"name": "other_party_address", "label": "The other party's address", "type": "text", "required": False},
            CASE_NUMBER_FIELD,
            {"name": "facts", "label": "The facts", "type": "textarea", "required": True,
             "placeholder": "Write each fact as its own paragraph, in the order things happened. "
                            "Put each paragraph on a new line.",
             "help": "Only state what you know to be true. Each new line becomes a numbered paragraph."},
            {"name": "relief_sought", "label": "The order you want (founding) or other request (opposing)",
             "type": "textarea", "required": False,
             "placeholder": "e.g. that the Respondent be ordered to return my property"},
        ],
        "sources": "court",
        "build": _build_affidavit,
    },
    "notice_of_opposition": {
        "label": "Notice of Opposition",
        "description": "Tells the court and the other side that you will oppose an application that was "
                       "served on you. It must be filed with an opposing affidavit, within a strict time limit.",
        "courts": ["magistrates_court", "high_court"],
        "fields": [
            {"name": "respondent_name", "label": "Your full name (you are the Respondent)", "type": "text",
             "required": True},
            {"name": "applicant_name", "label": "Name of the person who brought the application (the Applicant)",
             "type": "text", "required": True},
            dict(CASE_NUMBER_FIELD, required=True, label="Case number (on the application you received)",
                 help="Copy it exactly from the application that was served on you."),
            {"name": "service_address", "label": "Your address for service", "type": "text", "required": True,
             "placeholder": "Where court papers can be delivered to you",
             "help": "Court papers in this case will be delivered to this address."},
            {"name": "phone", "label": "Your phone number", "type": "tel", "required": False, "placeholder": "e.g. 0772123456 or +263772123456"},
            {"name": "email", "label": "Your email address", "type": "text", "required": False},
            {"name": "applicant_address", "label": "The Applicant's (or their lawyers') address", "type": "text",
             "required": False, "help": "Shown on the application you received."},
        ],
        "sources": "court",
        "build": _build_notice_of_opposition,
    },
    "protection_order": {
        "label": "Application for a Protection Order",
        "description": "Asks the Magistrates Court to order someone you have a domestic relationship with to "
                       "stop abusing, threatening or harassing you (Domestic Violence Act).",
        "courts": ["magistrates_court"],
        "fields": [
            {"name": "complainant_name", "label": "Your full name", "type": "text", "required": True},
            {"name": "complainant_address", "label": "Your address", "type": "text", "required": True},
            {"name": "complainant_phone", "label": "Your phone number", "type": "tel", "required": False, "placeholder": "e.g. 0772123456 or +263772123456"},
            {"name": "respondent_name", "label": "Full name of the person you need protection from", "type": "text",
             "required": True},
            {"name": "respondent_address", "label": "Their address (if known)", "type": "text", "required": False},
            {"name": "relationship", "label": "How are you related?", "type": "select", "required": True,
             "options": [{"value": v, "label": v} for v in (
                 "Spouse (married)", "Former spouse", "Partner (living together, not married)", "Former partner",
                 "Parent", "Child", "Brother or sister", "Other relative", "Other person living in the same home")]},
            {"name": "acts", "label": "What has happened?", "type": "textarea", "required": True,
             "placeholder": "Describe each incident on a new line: what they did, when, and where.",
             "help": "Include physical, verbal, emotional, sexual or economic abuse, threats and harassment."},
            {"name": "children", "label": "Children affected (if any)", "type": "textarea", "required": False,
             "placeholder": "Names and ages of children who live with you or were affected"},
            {"name": "orders", "label": "What do you need the order to say?", "type": "checks", "required": True,
             "options": [{"value": k, "label": "The Respondent " + label} for k, _t, label in PROTECTION_ORDER_OPTIONS]},
            {"name": "other_orders", "label": "Anything else the order should say", "type": "textarea",
             "required": False},
            {"name": "urgent", "label": "I am in immediate danger and need an interim order today", "type": "checkbox",
             "required": False},
        ],
        "sources": ["Domestic Violence Act [Chapter 5:16]", "Magistrates Court Act [Chapter 7:10]"],
        "build": _build_protection_order,
    },
    "summons": {"label": "Civil Summons",
                "description": "Starts a claim for money in the Magistrates Court, Harare: a debt, unpaid rent, "
                               "goods sold, or damages. The Defendant has 7 days to enter appearance.",
                "courts": ["magistrates_court"], "fields": _CIVIL_SUMMONS_FIELDS, "sources": "court",
                "build": _build_civil_summons},
    "divorce_summons": {"label": "Divorce Summons",
                        "description": "Starts a divorce in the High Court, Harare: the divorce, custody of the "
                                       "children, maintenance and the sharing of property. The Defendant has 10 "
                                       "days to enter appearance.",
                        "courts": ["high_court"], "fields": _DIVORCE_SUMMONS_FIELDS, "sources": "court",
                        "build": _build_divorce_summons},
    "application": {"label": "Court Application", "description": "A general application asking the court for an order.",
                    "courts": None, "fields": _SIMPLE_FIELDS, "sources": "court",
                    "build": lambda c, d: _build_simple(c, d, "Court Application")},
    "appeal": {"label": "Notice of Appeal", "description": "Asks a higher court to change a lower court's decision.",
               "courts": None, "fields": _SIMPLE_FIELDS, "sources": "court",
               "build": lambda c, d: _build_simple(c, d, "Notice of Appeal")},
}


# Every "..._name" answer is a person's (or a company's) name: no numbers.
for _template in TEMPLATES.values():
    for _field in _template["fields"]:
        if _field["name"].endswith("_name") and _field.get("type") == "text":
            _field["type"] = "name"


# ---------------------------------------------------------------------------
# Public functions (used by server.py)
# ---------------------------------------------------------------------------

def list_templates():
    """What GET /documents/templates returns: everything the wizard needs to
    show the choices and build the form, without the build functions."""
    return {
        "courts": [{"id": k, "label": v} for k, v in COURT_LABELS.items()],
        "templates": [
            {"id": key, "label": t["label"], "description": t["description"],
             "courts": t["courts"] or list(COURT_LABELS), "fields": t["fields"]}
            for key, t in TEMPLATES.items()
        ],
    }


def blocks_to_text(blocks):
    """Plain-text version of a document, for copying and for older clients."""
    lines = []
    for b in blocks:
        style, text = b.get("style"), b.get("text", "")
        if style == "blank":
            lines.append("")
        elif style == "party":
            lines.append(f"{text}        {b.get('role', '')}")
        elif style == "num":
            lines.append(("    " if b.get("indent") else "") + f"{b.get('num', '')} {text}")
        elif style == "sig":
            lines.append("\nSigned: _____________________\n" + text)
        elif style == "title":
            rule = "_" * 60
            lines.append(f"{rule}\n{text.center(60)}\n{rule}\n")
        elif style == "courtcase":
            lines.append(f"{text}        {b.get('right', '')}")
        elif style == "sigblock":
            lines.append("\n" + " " * 34 + "\u2026" * 20 + "\n" + "\n".join(" " * 34 + l for l in text.split("\n")))
        elif style == "to":
            rows = text.split("\n")
            lines.append("\n" + f"{b.get('label', 'TO:'):<9}{rows[0]}" + "".join("\n" + " " * 9 + r for r in rows[1:]))
        elif style == "pagebreak":
            lines.append("\n\n")
        elif style == "dated":
            lines.append("\n" + text)
        else:
            lines.append(text)
    return "\n".join(lines).strip()


class DocumentInputError(ValueError):
    """An answer is in the wrong format (e.g. letters in a phone number)."""


# Phone numbers: digits only, with an optional + at the start for a country
# code -- 0772123456, 0242700542 or +263772123456.
_PHONE = re.compile(r"^\+?\d{9,15}$")
# Zimbabwe national ID: 2-digit district code, 6 or 7 digits, a check letter
# and a 2-digit district code -- 63-123456 A 75 / 08-1234567X42.
_ID_NUMBER = re.compile(r"^(\d{2})(\d{6,7})([A-Z])(\d{2})$")

PHONE_ERROR = "Enter a full phone number using numbers only (you may start with + for a country code), e.g. 0772123456 or +263772123456."
ID_ERROR = "Enter the national ID number as on your ID, e.g. 63-123456 A 75: two digits, six or seven digits, a letter, then two digits."


NAME_ERROR = "Use letters only for a name (spaces, - and ' are fine), with no numbers."
NAMES_ERROR = "Use letters only for the names (separate them with commas or \"and\"), with no numbers."
MONEY_ERROR = "Enter an amount using numbers only, e.g. 500 or 1250.50"
_NAME_CHARS = set(" '\u2019.-&")
_MONEY = re.compile(r"^\d{1,12}(\.\d{1,2})?$")


def clean_name(value, several=False):
    value = re.sub(r"\s+", " ", str(value or "")).strip()
    allowed = _NAME_CHARS | ({",", ";"} if several else set())
    ok = any(c.isalpha() for c in value) and all(c.isalpha() or c in allowed for c in value)
    return value if ok else None


def clean_money(value):
    value = str(value or "").strip().replace(",", "")
    return value if _MONEY.match(value) else None


def clean_phone(value):
    value = str(value or "").strip()
    return value if _PHONE.match(value) else None


def clean_id_number(value):
    """Returns the ID number written the standard way ("63-123456 A 75"), or None."""
    compact = re.sub(r"[\s-]", "", str(value or "")).upper()
    m = _ID_NUMBER.match(compact)
    return f"{m.group(1)}-{m.group(2)} {m.group(3)} {m.group(4)}" if m else None


def check_formats(template, details):
    """Checks phone and ID number answers. Tidies the ID number in place.
    Raises DocumentInputError naming the first answer that is wrong."""
    for field in template["fields"]:
        value = str(details.get(field["name"]) or "").strip()
        if not value:
            continue
        if field.get("type") == "name" and not clean_name(value):
            raise DocumentInputError(f"{field['label']}: {NAME_ERROR}")
        if field.get("type") == "names" and not clean_name(value, several=True):
            raise DocumentInputError(f"{field['label']}: {NAMES_ERROR}")
        if field.get("type") == "money":
            tidy = clean_money(value)
            if not tidy:
                raise DocumentInputError(f"{field['label']}: {MONEY_ERROR}")
            details[field["name"]] = tidy
        if field.get("type") == "tel" and not clean_phone(value):
            raise DocumentInputError(f"{field['label']}: {PHONE_ERROR}")
        if field.get("type") == "idnumber":
            tidy = clean_id_number(value)
            if not tidy:
                raise DocumentInputError(f"{field['label']}: {ID_ERROR}")
            details[field["name"]] = tidy


def _missing_required(template, details):
    missing = []
    for field in template["fields"]:
        if not field.get("required"):
            continue
        value = details.get(field["name"])
        if not value or (isinstance(value, str) and not value.strip()):
            missing.append(field["name"])
    return missing


def generate(document_type, court_tier, case_details):
    """Builds a document. Returns {title, content, blocks, sources_used,
    warnings, court_tier}. An unknown document_type falls back to the simple
    application layout, using document_type as the document's title."""
    details = case_details if isinstance(case_details, dict) else {}
    warnings = [REVIEW_WARNING]

    template = TEMPLATES.get(document_type)
    if template is None:
        label = (document_type or "Application").strip() or "Application"
        if court_tier not in COURT_NAMES:
            court_tier = court_tier or ""
        blocks, title, extra = _build_simple(court_tier, details, label)
        missing = [n for n in ("applicant_name", "respondent_name", "relief_sought")
                   if not str(details.get(n) or "").strip()]
    else:
        allowed = template["courts"] or list(COURT_LABELS)
        if court_tier not in allowed:
            court_tier = allowed[0]
            warnings.append(f"This type of document is filed in the {COURT_LABELS[court_tier]}, so it has been "
                            "prepared for that court instead of the one you chose.")
        details = dict(details)
        check_formats(template, details)
        blocks, title, extra = template["build"](court_tier, details)
        missing = _missing_required(template, details)

    warnings.extend(extra)
    if missing:
        warnings.append(BLANK_WARNING)

    sources = template.get("sources") if template else "court"
    sources_used = list(COURT_SOURCES.get(court_tier, [])) if sources == "court" else list(sources)

    # The person preparing the document, so downloaded files get a useful name.
    if document_type == "notice_of_opposition":
        party = details.get("respondent_name")
    else:
        party = (details.get("deponent_name") or details.get("complainant_name") or details.get("plaintiff_name")
                 or details.get("applicant_name"))
    party = str(party or "").strip()
    full_title = f"{title} - {party}" if party else title

    return {
        "title": full_title,
        "content": blocks_to_text(blocks),
        "blocks": blocks,
        "sources_used": sources_used,
        "warnings": warnings,
        "court_tier": court_tier,
    }
