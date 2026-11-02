"""
PDF QUIZ MAKER  -  one single file (website + logic together). No API key needed.

Run it with:   streamlit run app.py

What it does
  1. You upload a PDF that has numbered facts under category headings.
  2. It reads the PDF and splits it into categories + facts.
  3. You choose how many questions you want (mix of categories).
  4. It makes 4-option MCQs. You click an option -> it shows Right / Wrong.

Two question styles (sidebar):
  - Quick     : instant, no setup. Questions built with rules from the PDF text.
  - Local AI  : optional. A free AI model (Ollama) running on your own computer writes more natural questions.
"""
import io
import json
import random
import re
import urllib.request
from collections import defaultdict

import streamlit as st
from pypdf import PdfReader

OLLAMA_URL = "http://localhost:11434"
FACTS_PER_CALL = 3          # small local models do better with few facts per request


# =====================================================================
# PART 1 - READ THE PDF  ->  categories + numbered facts
# =====================================================================
ITEM_NO = re.compile(r"(?<![\d.,])(\d{1,3})[.)]\s+")                     # "12. " or "12) "
GLUED_HEADER = re.compile(r"(?<=[a-z\)\.”\"'])([A-Z]{3,}(?:\s+[A-Z]{2,})+)\s*$")  # ...)STATE NEWS


def is_header(line):
    t = line.strip()
    return len(t) >= 6 and re.fullmatch(r"[A-Z][A-Z &/,\-]+", t) is not None


def nice_title(t):
    t = t.strip().title().replace(" And ", " and ")
    return t


def split_into_items(block):
    """Cut one block of text into numbered items. Returns a list of groups (normally just one).
    - numbers glued to words ('tech31. National') still work (must be exactly the next number)
    - a number at the start of a line may skip up to 2 numbers (1,2,4 is fine)
    - if numbering restarts at 1 (a heading we could not detect) a new group is started"""
    marks, last, group = [], 0, 0
    for m in ITEM_NO.finditer(block):
        n = int(m.group(1))
        line_start = m.start() == 0 or block[m.start() - 1] == "\n"
        if n == 1 and last >= 3 and line_start:
            group, last = group + 1, 0
        if n == last + 1 or (line_start and last < n <= last + 3):
            marks.append((m.start(), m.end(), group))
            last = n
    groups = defaultdict(list)
    for k, (_, end, g) in enumerate(marks):
        stop = marks[k + 1][0] if k + 1 < len(marks) else len(block)
        groups[g].append(block[end:stop])
    return [groups[g] for g in sorted(groups)]


def item_parts(raw):
    """Join wrapped lines; lines starting with '-' are sub-points. Returns [main, note, note...]"""
    parts = []
    for line in [l.strip() for l in raw.splitlines() if l.strip()]:
        if not parts or line[0] in "-–—":
            parts.append(line.lstrip("-–— ").strip())
        else:
            parts[-1] += " " + line
    return [re.sub(r"\s+", " ", p).strip(" ;") for p in parts if p.strip(" -–—;")]


@st.cache_data(show_spinner="Reading your PDF...")
def parse_pdf(data: bytes):
    reader = PdfReader(io.BytesIO(data))
    return parse_text("\n".join((p.extract_text() or "") for p in reader.pages))


def parse_text(text):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    blocks, order, current = defaultdict(list), [], "General"
    for idx, line in enumerate(lines):
        nxt = lines[idx + 1] if idx + 1 < len(lines) else ""
        # a heading = ALL CAPS line, OR any short line that sits right before a "1." line
        short_title = (re.match(r"1[.)]\s", nxt) and not re.match(r"[\d\-–—]", line)
                       and len(line.split()) <= 8 and not line.endswith((".", ";", ",")))
        if is_header(line) or short_title:
            current = nice_title(line) if line.isupper() else line.strip(" :-")
            continue
        tail = None
        m = GLUED_HEADER.search(line)
        if m:
            tail, line = nice_title(m.group(1)), line[: m.start()].strip()
        if current not in order:
            order.append(current)
        blocks[current].append(line)
        if tail:
            current = tail

    items, seen = [], set()
    for cat in order:
        for k, group in enumerate(split_into_items("\n".join(blocks[cat]))):
            name = cat if k == 0 else f"{cat} (part {k + 1})"
            for raw in group:
                parts = item_parts(raw)
                txt = "; ".join(parts)
                key = re.sub(r"\W+", "", txt.lower())
                if len(txt.split()) < 4 or key in seen:      # skip tiny / duplicate facts
                    continue
                seen.add(key)
                items.append({"id": len(items), "cat": name, "text": txt, "main": parts[0] if parts else txt})

    prepare_items(items)
    return items, list(dict.fromkeys(i["cat"] for i in items))


# =====================================================================
# PART 2 - QUICK MODE: real questions built with smart rules (no setup, no internet)
# =====================================================================
EDGE = " \t\"“”‘’'()[],.:;-–—"
CONNECT = {"of", "and", "for", "the", "de", "la", "del", "van", "von", "&"}
SUBJ_STOP = {"India", "Indian", "Indias", "The", "This", "It", "New", "Govt", "Government", "First", "Key"}
VERBS = set("""takes appointed appoints becomes wins won gets launches launched launch signs signed sets secures secured
announces announced approves approved releases released declares declared makes made inaugurates inaugurated unveils
unveiled introduces introduced completes completed receives received raises raised issues issued imposes impose imposed
conducts conducted organised organized celebrates celebrated concludes concluded begins opens opened extends extended
allows allowed permits bars barred bans expands expanded adds added dedicates dedicated flags held hosts hosted clinched
clinches claims claimed created creates developed develops tests test-fires achieves attains reports records gives
passed retired joins joined acquires acquire acquired to is are was has have will plans proposes proposed sends
flagged selected rises receives commissions grants gets handed handover bags crowned crowns named names elected
secured clinches publishes published presents presented starts started debuts debuted""".split())
PERSON_VERBS = {"appointed", "appoints", "becomes", "wins", "won", "claims", "claimed", "retired", "named", "elected",
                "clinched", "clinches", "bags", "crowned"}
PASSIVE_FOR_PERSON = {"appointed", "named", "elected", "selected", "awarded", "conferred", "honoured", "crowned"}
TITLES = set("dr shri smt prof mr mrs ms justice minister chief president general lt col captain mountaineer physicist".split())
STATES = ["Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", "Gujarat", "Haryana",
          "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur",
          "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana",
          "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal", "Delhi", "Ladakh", "Puducherry", "Chandigarh"]
COUNTRIES = ["Japan", "Russia", "Bhutan", "Nepal", "Maldives", "Mauritius", "Qatar", "Brazil", "Armenia", "Vietnam",
             "Thailand", "Malaysia", "Norway", "Hungary", "Colombia", "Zambia", "Bangladesh", "Iran", "Israel",
             "France", "Germany", "Australia", "Canada", "Sri Lanka", "Oman", "Singapore", "Indonesia", "Egypt"]
PLACE_ALIASES = {"gujrat": "Gujarat", "orissa": "Odisha"}
ORG_WORDS = set("""bank limited ltd corporation ministry department institute university council commission authority
board force navy army airlines airways foundation society committee association company group motors industries
enterprises services cabinet court college school federation agency mission organisation organization party club
fc team union air office centre bureau laboratory laboratories shipyard port""".split())
PAST = {"takes": "took", "wins": "won", "becomes": "became", "gets": "got", "launches": "launched", "launch": "launched",
        "signs": "signed", "secures": "secured", "approves": "approved", "releases": "released",
        "announces": "announced", "unveils": "unveiled", "inaugurates": "inaugurated",
        "introduces": "introduced", "declares": "declared", "completes": "completed", "receives": "received",
        "raises": "raised", "imposes": "imposed", "impose": "imposed", "conducts": "conducted",
        "celebrates": "celebrated", "concludes": "concluded", "begins": "began", "opens": "opened",
        "extends": "extended", "allows": "allowed", "expands": "expanded", "adds": "added", "bans": "banned",
        "bars": "barred", "claims": "claimed", "clinches": "clinched", "appoints": "appointed",
        "makes": "made", "proposes": "proposed", "issues": "issued", "tests": "tested",
        "achieves": "achieved", "attains": "attained", "hosts": "hosted", "joins": "joined",
        "acquires": "acquired", "plans": "planned", "gives": "gave", "sets": "set", "sends": "sent",
        "grants": "granted", "dedicates": "dedicated", "develops": "developed", "creates": "created",
        "starts": "started", "presents": "presented", "publishes": "published", "crowns": "crowned"}
NUM = re.compile(
    r"(?<![A-Za-z\d.,])(?:(?:Rs\.?|USD|EUR|₹|\$)\s?)?\d[\d,]*(?:\.\d+)?\s?"
    r"(?:%|cr\b|crore\b|lakh\b|million\b|billion\b|trillion\b|kN\b|MW\b|yrs?\b|years?\b|"
    r"days?\b|st\b|nd\b|rd\b|th\b)?", re.I)
CAP_TOK = r"(?:[A-Z][\w’'&/\-]*\.?|\d+)"
CAP_SPAN = re.compile(rf"[A-Z][\w’'&/\-]*\.?(?:\s+(?:(?:of|and|for|the|de|la|del|van|von)\s+)*{CAP_TOK})*")
BAD_WORDS = set("""to is are was has have will by with takes appointed appoints becomes wins won gets launches launched
signs signed secured secures announces approves approved releases released declares declared makes made conferred
awarded passed concludes begins opens rises strengthen offer offers complete completes names launch adds inaugurates
inaugurated organised organized unveils unveiled introduces raises allows permits""".split())
HEADLINE_WORDS = set("""The This That India Indian Indias Government Govt Ministry Department Launches Launched Appointed
Appoints Becomes Wins Gets Gives Signs Approves Approved Declares Releases Released Announces Introduces Unveils Unveiled
Takes Held Opens Inaugurates Celebrates Completes Receives Gains Raises Allows Permits Proposes Adds Sets Key First New
Grants Awards Honours Passed Away Under Over Also""".split())


def sig_tokens(text):
    """Meaningful words of a fact - used to avoid 'wrong' options that are secretly also right."""
    bad = {"india", "indian", "limited", "bank", "ministry", "government", "launches", "launched", "with", "from",
           "that", "this", "first", "scheme", "national", "state", "year", "years", "crore", "which", "their"}
    return {w for w in re.findall(r"[a-z]{4,}", text.lower()) if w not in bad}


def find_candidates(text):
    """Parts of a sentence that can be swapped to build answer options: numbers and names."""
    out = []

    def add(s, e, kind):
        raw = text[s:e]
        s += len(raw) - len(raw.lstrip(EDGE))
        e -= len(raw) - len(raw.rstrip(EDGE))
        ans = text[s:e]
        if len(ans) < 2 or len(ans.split()) > 5 or len(ans) > 40:
            return
        if kind == "txt":
            words = [w.lower().strip(EDGE) for w in ans.split()]
            verbish = any(len(w) > 5 and w.endswith(("ed", "ates", "ises", "izes"))
                          and w not in ("united", "limited") for w in words)
            weak = len(words) == 1 and len(ans) < 5 and not ans.isupper() and not re.search(r"\d", ans)
            if (not ans[0].isupper() or ans in HEADLINE_WORDS or ans.split()[0] in HEADLINE_WORDS or verbish
                    or weak or any(w in BAD_WORDS for w in words) or not re.search(r"[A-Za-z]{3}", ans)):
                return
        out.append({"start": s, "end": e, "text": ans, "kind": kind, "wc": len(ans.split()),
                    "pos": "start" if s == 0 else "mid"})

    for m in NUM.finditer(text):
        tok = m.group(0).strip()
        digits = re.search(r"\d[\d,]*(?:\.\d+)?", tok).group(0)
        has_unit = tok.lower() != digits.lower()
        before = text[max(0, m.start() - 1): m.start()]
        if not has_unit and len(digits.replace(",", "")) < 3 and "." not in digits:
            continue
        if before == "-" and not has_unit:
            continue
        kind = "year" if re.fullmatch(r"(19|20)\d\d", tok) else "num"
        add(m.start(), m.start() + len(m.group(0).rstrip()), kind)

    for m in CAP_SPAN.finditer(text):
        s0, e0 = m.start(), m.end()
        start = None
        for t in list(re.finditer(r"\S+", text[s0:e0])) + [None]:
            word = t.group(0).strip(EDGE) if t else None
            if t is None or word in HEADLINE_WORDS:
                if start is not None:
                    add(s0 + start, s0 + (t.start() if t else e0 - s0), "txt")
                start = None
            elif start is None:
                start = t.start()
    seen, uniq = set(), []
    for c in out:
        if (c["start"], c["end"]) not in seen:
            seen.add((c["start"], c["end"]))
            uniq.append(c)
    return uniq


def classify_name(subj, verb=None):
    """Returns 'state', 'country', 'person', 'org' or None (not sure -> no question)."""
    key = PLACE_ALIASES.get(subj.lower(), subj)
    if key in STATES:
        return "state"
    if key in COUNTRIES:
        return "country"
    low = [w.lower().strip(EDGE) for w in subj.split()]
    first_acronym = re.match(r"[A-Z]{3,}\b", subj) is not None
    if any(w in ORG_WORDS for w in low) or first_acronym:
        return "org"
    if any(w in TITLES for w in low) or re.search(r"(^|\s)[A-Z]\.?(\s|$)", subj):
        return "person"
    if verb is None:                                       # partner side of "A + B": companies, mostly
        return "org" if len(low) >= 1 else None
    if (verb == "person_verb" or verb in PERSON_VERBS) and len(low) >= 2:
        return "person"
    return None


def tidy_predicate(pred):
    words = pred.split()
    if words and not (words[0].isupper() and len(words[0]) > 1):
        words[0] = words[0][0].lower() + words[0][1:]
    small = {"As", "For", "To", "In", "At", "On", "Of", "And", "The", "By", "With", "From", "Under", "Over", "Off"}
    return " ".join([words[0]] + [w.lower() if w in small else w for w in words[1:]])


def analyze_subject(main):
    """'Manish agarwal takes charge as general manager of X'  ->  subject + predicate."""
    words = main.split()
    k = next((i for i, w in enumerate(words[:8]) if i >= 1 and w.lower().strip(EDGE) in VERBS), None)
    if k is None:
        return None
    sw = words[:k]
    subj = " ".join(sw).strip(EDGE)
    if re.search(r"[+;:]|\s[-–—]\s", subj) or len(subj) > 40 or len(subj) < 2 or not sw[0][0].isupper():
        return None
    if len(sw) == 1 and (subj in SUBJ_STOP or subj.lower() in ("centre",)):
        return None
    for j, w in enumerate(sw):          # lower-case only as connector, after a connector, or as a surname (2 words)
        if w[0].islower() and w.lower() not in CONNECT and not (sw[j - 1].lower() in CONNECT) and not (j == 1 and len(sw) == 2):
            return None
    verb = words[k].lower().strip(EDGE)
    rest = " ".join(words[k + 1:]).strip(" .,;:")
    if verb == "to":
        pred = "will " + rest
    elif verb in PAST:
        pred = PAST[verb] + (" " + rest if rest else "")
    else:
        pred = words[k] + (" " + rest if rest else "")
    pred = tidy_predicate(pred.strip(" .,;:"))
    if len(pred.split()) < 4 or subj.lower() in pred.lower():
        return None
    person_verb = verb in PERSON_VERBS or (verb == "takes" and rest.lower().startswith("charge"))
    kind = classify_name(subj, "person_verb" if person_verb else "other")
    if kind == "person" and verb in PASSIVE_FOR_PERSON:
        pred = "was " + pred
    if kind is None or (kind in ("state", "country") and verb == "to"):
        return None
    return {"text": subj, "pred": pred, "kind": kind, "wc": len(sw)}


def analyze_partner(main):
    """'SBI Life + J&K Bank to Offer Insurance Solutions' -> A, B, purpose."""
    m = re.match(r"^([^+;:]{2,45}?)\s\+\s([^+]+)$", main)
    if not m:
        return None
    a, rest = m.group(1).strip(EDGE), m.group(2).strip()
    sp = CAP_SPAN.match(rest)
    if not sp or not a or not a[0].isupper() or a in SUBJ_STOP:
        return None
    b, purpose = sp.group(0).strip(EDGE), rest[sp.end():].strip(" -–—:,.")
    if not re.match(r"(to|for|in|under|towards)\b", purpose) or len(purpose.split()) < 3:
        return None
    words = purpose.split()
    if len(words) > 1 and words[0] == "to" and words[1][0].isupper() and words[1].lower() not in ("india",):
        words[1] = words[1].lower() if not words[1].isupper() else words[1]
    kind = classify_name(a)
    if kind is None:
        return None
    return {"a": a, "b": b, "purpose": " ".join(words), "kind": kind}


def prepare_items(items):
    """Pre-compute everything the question makers need."""
    for it in items:
        it["low"] = it["text"].lower()
        it["sig"] = sig_tokens(it["text"])
        it["cands"] = find_candidates(it["main"])
        it["subj"] = analyze_subject(it["main"])
        it["partner"] = analyze_partner(it["main"])
        it["spans"] = []                                   # names usable as wrong options in other questions
        if it["subj"]:
            it["spans"].append({"role": it["subj"]["kind"], "text": it["subj"]["text"], "wc": it["subj"]["wc"]})
        if it["partner"]:
            p = it["partner"]
            it["spans"].append({"role": p["kind"], "text": p["a"], "wc": len(p["a"].split())})


def perturb_number(tok, rng):
    """3 believable-but-wrong versions of a number."""
    m = re.search(r"\d[\d,]*(?:\.\d+)?", tok)
    s = m.group(0)
    comma, dec = "," in s, (len(s.split(".")[1]) if "." in s else 0)
    val = float(s.replace(",", ""))
    if dec == 0 and not comma and 1900 <= val <= 2100:
        cands = [val + d for d in (-1, 1, -2, 2, -3, 3, 5, -5)]
    elif dec == 0 and val <= 20:
        cands = [val + d for d in (-1, 1, -2, 2, 3, 5, 10)]
    else:
        cands = [val * f for f in (0.5, 0.75, 0.8, 0.9, 1.1, 1.25, 1.5, 2, 3)]
    out, seen = [], {round(val, dec)}
    rng.shuffle(cands)
    for v in cands:
        v = round(v, dec)
        if v <= 0 or v in seen or ("%" in tok and v > 100):
            continue
        seen.add(v)
        new = f"{v:,.{dec}f}" if comma else f"{v:.{dec}f}"
        res = tok[: m.start()] + new + tok[m.end():]
        suf = re.search(r"\d(st|nd|rd|th)\s*$", res, re.I)
        if suf:
            n = int(v)
            res = res[: suf.start(1)] + ("th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th"))
        out.append(res)
        if len(out) == 3:
            break
    return out


def wrong_options(item, answer, roles, wc, items, rng):
    """3 other names/organisations from the PDF that are clearly NOT right for this fact."""
    a_low = answer.lower()
    base = item["sig"] - sig_tokens(answer)
    entries = []
    for o in items:
        if o["id"] == item["id"]:
            continue
        for sp in o["spans"]:
            d = sp["text"]
            dl = d.lower()
            if sp["role"] not in roles or abs(sp["wc"] - wc) > 2 or dl in item["low"] or dl in a_low or a_low in dl:
                continue
            if not 0.5 <= len(d) / max(1, len(answer)) <= 2:
                continue
            # skip if this name also appears in another fact that talks about the same thing
            if any(dl in p["low"] and len(base & p["sig"]) >= 2 for p in items):
                continue
            entries.append((o["cat"] != item["cat"], abs(sp["wc"] - wc), rng.random(), d))
    entries.sort()
    out, seen = [], set()
    for *_, d in entries:
        if d.lower() not in seen:
            seen.add(d.lower())
            out.append(d)
    near = out[:10]
    return rng.sample(near, 3) if len(near) >= 3 else []


def place_options(item, answer, kind, items, rng):
    group = STATES if kind == "state" else COUNTRIES
    base = item["sig"] - sig_tokens(answer)
    ans = PLACE_ALIASES.get(answer.lower(), answer)
    ok = []
    for d in group:
        dl = d.lower()
        if d == ans or dl in item["low"]:
            continue
        if any(dl in p["low"] and len(base & p["sig"]) >= 2 for p in items):
            continue
        ok.append((not any(dl in p["low"] for p in items), rng.random(), d))   # names seen in the PDF first
    ok.sort()
    pool = [d for *_, d in ok[:8]]
    return rng.sample(pool, 3) if len(pool) >= 3 else []


def as_sentence(t):
    t = t.strip(" ;,")
    t = t[0].upper() + t[1:] if t else t
    return t if t.endswith((".", "!", "?")) else t + "."


def finish(question, options, answer, item, kind):
    rng_opts = options[:]
    random.shuffle(rng_opts)
    return {"question": question, "options": rng_opts, "answer": answer, "explanation": "",
            "category": item["cat"], "source": item["text"], "kind": kind}


LEAD = {"person": "Who", "org": "Which organisation", "state": "Which state", "country": "Which country"}


def q_subject(item, items, rng):
    s = item["subj"]
    if not s:
        return None
    if s["kind"] in ("state", "country"):
        wrong = place_options(item, s["text"], s["kind"], items, rng)
    else:
        wrong = wrong_options(item, s["text"], {s["kind"]}, s["wc"], items, rng)
    if len(wrong) < 3:
        return None
    ans = PLACE_ALIASES.get(s["text"].lower(), s["text"])
    return finish(f"{LEAD[s['kind']]} {s['pred']}?", [ans] + wrong, ans, item, "subject")


def q_partner(item, items, rng):
    p = item["partner"]
    if not p:
        return None
    if p["kind"] in ("state", "country"):
        wrong = place_options(item, p["a"], p["kind"], items, rng)
    else:
        wrong = wrong_options(item, p["a"], {p["kind"]}, len(p["a"].split()), items, rng)
    if len(wrong) < 3:
        return None
    ans = PLACE_ALIASES.get(p["a"].lower(), p["a"])
    return finish(f"Which of the following has partnered with {p['b']} {p['purpose']}?", [ans] + wrong, ans, item, "partner")


def q_statement(item, items, rng, kinds):
    main = item["main"]
    cands = [c for c in item["cands"] if c["kind"] in kinds]
    rng.shuffle(cands)
    for c in cands:
        wrong = perturb_number(c["text"], rng)
        if len(wrong) == 3:
            sentences = [as_sentence(main[: c["start"]] + o + main[c["end"]:]) for o in [c["text"]] + wrong]
            return finish("Which of the following statements is correct?", sentences, sentences[0], item, "statement")
    return None


def offline_question(item, items, rng):
    steps = []
    if item["partner"]:
        steps.append("partner")
    if item["subj"]:
        steps.append("subject")
    if any(c["kind"] == "num" for c in item["cands"]):
        steps.insert(0 if rng.random() < 0.4 else len(steps), "num")
    steps.append("year")
    for st_ in steps:
        q = (q_partner(item, items, rng) if st_ == "partner" else
             q_subject(item, items, rng) if st_ == "subject" else
             q_statement(item, items, rng, {"num"} if st_ == "num" else {"year"}))
        if q:
            return q
    return None


# =====================================================================
# PART 3 - LOCAL AI MODE (optional): Ollama runs a free AI model on YOUR computer. No key, no internet.
# =====================================================================
SYSTEM = """You write multiple-choice quiz questions for exam practice from short fact notes.
The notes are rough: spelling mistakes, fragments, '+' = two parties partnering, '-' = label then value,
';' = extra details of the same fact.

For EACH fact write ONE question with these rules:
1. Test exactly what the fact says. The correct answer MUST be written in the fact. Never change names, numbers, dates, places.
2. Write a natural exam question (Who / Which / What / Where / When / How much / How many). It must NOT be a
   fill-in-the-blank and NOT the note with a word removed. Rephrase it; tense may change; meaning must not.
3. The question must stand alone (include the names/event it needs) and must not contain the answer.
4. Give exactly 4 options of the same kind as the answer (all people / all books / all amounts ...). One correct,
   three believable but definitely wrong. No 'All of the above', 'None of the above'.
5. If a fact is too vague for a clear single-answer question, skip it.

Example fact:  [7] (Books and Authors) Biographical Novel 'Kaho Kalidasa!' of Mahakavi Kalidasa; authour-Yugul Joshi
Example output: {"questions":[{"id":7,"question":"Which biographical novel on Mahakavi Kalidasa was written by Yugul Joshi?","options":["Kaho Kalidasa!","Kalidasa Katha","Meghdoot Ke Pare","Shakuntala Ki Khoj"],"answer_index":0,"explanation":"Yugul Joshi wrote the biographical novel 'Kaho Kalidasa!'."}]}

Reply with JSON only, in exactly this format:
{"questions":[{"id":<fact id>,"question":"...","options":["...","...","...","..."],"answer_index":<0-3>,"explanation":"one short sentence"}]}"""


def ollama_models():
    """List of models installed in Ollama, or None if Ollama is not running."""
    try:
        with urllib.request.urlopen(OLLAMA_URL + "/api/tags", timeout=2) as r:
            return [m["name"] for m in json.load(r).get("models", [])]
    except Exception:
        return None


def ollama_chat(model, user_text):
    body = json.dumps({"model": model, "stream": False, "format": "json",
                       "options": {"temperature": 0.3, "num_ctx": 4096},
                       "messages": [{"role": "system", "content": SYSTEM},
                                    {"role": "user", "content": user_text}]}).encode()
    req = urllib.request.Request(OLLAMA_URL + "/api/chat", body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.load(r)["message"]["content"]


def answer_is_in_fact(answer, fact):
    """Safety check: the answer's words must really be in the PDF fact (stops made-up answers)."""
    toks = re.findall(r"\w+", answer.lower())
    return bool(toks) and sum(t in fact.lower() for t in toks) / len(toks) >= 0.7


def clean_questions(raw_questions, chunk):
    by_id = {it["id"]: it for it in chunk}
    good = {}
    for q in raw_questions:
        try:
            it = by_id[int(q["id"])]
            question = str(q["question"]).strip()
            opts = [str(o).strip() for o in q["options"]]
            answer = opts[int(q["answer_index"])]
        except Exception:
            continue
        if len(opts) != 4 or len({o.lower() for o in opts}) != 4 or not all(opts) or len(question) < 15:
            continue
        a = answer.lower().strip(" .!?")
        if len(a) >= 4 and a in question.lower():
            continue
        if "____" in question or question.lower().startswith("fill in") or not answer_is_in_fact(answer, it["text"]):
            continue
        random.shuffle(opts)
        good[it["id"]] = {"question": question, "options": opts, "answer": answer,
                          "explanation": str(q.get("explanation", "")).strip(),
                          "category": it["cat"], "source": it["text"], "kind": "ai"}
    return good


def ask_local_ai(model, chunk):
    facts = "\n".join(f"[{it['id']}] ({it['cat']}) {it['text']}" for it in chunk)
    err = ""
    for _ in range(2):
        try:
            raw = ollama_chat(model, facts)
            data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
            qs = data.get("questions") if isinstance(data, dict) else data
            if qs is None and isinstance(data, dict) and "question" in data:
                qs = [data]
            return clean_questions(qs or [], chunk), ""
        except Exception as e:
            err = f"{type(e).__name__}: {str(e)[:200]}"
    return {}, err


# =====================================================================
# PART 4 - BUILD A MIXED QUIZ
# =====================================================================
def mixed_order(items, cats, rng):
    """Take one fact from each chosen category in turn -> always a mix."""
    by = defaultdict(list)
    for it in items:
        if it["cat"] in cats:
            by[it["cat"]].append(it)
    for v in by.values():
        rng.shuffle(v)
    names = list(by)
    rng.shuffle(names)
    order = []
    while any(by.values()):
        for n in names:
            if by[n]:
                order.append(by[n].pop())
    return order


def build_quiz(items, cats, n, use_ai, model=None):
    rng = random.Random()
    order = mixed_order(items, cats, rng)
    quiz, errors = [], []
    if not use_ai:
        for it in order:
            q = offline_question(it, items, rng)
            if q:
                quiz.append(q)
            if len(quiz) == n:
                break
        return quiz, errors

    bar = st.progress(0.0, text="Local AI is writing your questions (first one can be slow)...")
    pos = 0
    for _ in range(3):                                   # extra rounds if some facts get skipped
        need = n - len(quiz)
        if need <= 0 or pos >= len(order):
            break
        batch = order[pos: pos + need + max(2, need // 4)]
        pos += len(batch)
        results = {}
        chunks = [batch[i: i + FACTS_PER_CALL] for i in range(0, len(batch), FACTS_PER_CALL)]
        for k, chunk in enumerate(chunks):
            got, err = ask_local_ai(model, chunk)
            results.update(got)
            if err:
                errors.append(err)
            bar.progress(min(1.0, (len(quiz) + (k + 1) / len(chunks) * need) / n),
                         text=f"Local AI is writing your questions... ({len(quiz) + len(results)}/{n})")
        quiz += [results[it["id"]] for it in batch if it["id"] in results][:need]
        if errors and not results:
            break
    bar.empty()
    return quiz, errors


# =====================================================================
# PART 5 - THE WEBSITE
# =====================================================================
def esc(s):
    """Stop Streamlit from treating $ * _ ` as formatting."""
    return re.sub(r"([\\`*_$~#])", r"\\\1", str(s))


def record_answer(i, key):
    st.session_state.answers[i] = st.session_state[key]


def restart_quiz():
    st.session_state.answers = {}
    st.session_state.run += 1


def main():
    st.set_page_config(page_title="PDF Quiz Maker", page_icon="📝", layout="centered")
    st.session_state.setdefault("quiz", [])
    st.session_state.setdefault("answers", {})
    st.session_state.setdefault("run", 0)

    st.title("📝 PDF Quiz Maker")
    st.caption("Upload a PDF → choose how many questions → practise with 4-option MCQs.")

    use_ai, model = False, None
    with st.sidebar:
        st.header("⚙️ Settings")
        mode = st.radio("Question style", ["⚡ Quick (no setup)", "🧠 Local AI (free, better questions)"])
        use_ai = mode.startswith("🧠")
        if use_ai:
            models = ollama_models()
            if models is None:
                st.warning("Ollama is not running. Install it from ollama.com, then run in a terminal:\n\n"
                           "`ollama pull qwen2.5:7b`\n\nand refresh this page.")
            elif not models:
                st.warning("Ollama is running but has no model. Run in a terminal:\n\n`ollama pull qwen2.5:7b`")
            else:
                model = st.selectbox("Installed model", models)
        st.caption("Quick = instant, built from the PDF text with rules.\n\nLocal AI = a free AI model running on "
                   "your own computer. Slower, more natural questions. No API key.")

    up = st.file_uploader("📄 Upload your PDF", type=["pdf"])
    if up is not None:
        items, cats = parse_pdf(up.getvalue())
        if not items:
            st.error("I couldn't find numbered facts in this PDF. Facts should look like '1. some fact'.")
        else:
            with st.expander(f"✅ PDF read: {len(items)} facts in {len(cats)} categories", expanded=False):
                for c in cats:
                    st.write(f"**{c}** – {sum(1 for i in items if i['cat'] == c)} facts")

            chosen_cats = st.multiselect("Categories to use (mix is automatic)", cats, default=cats)
            available = sum(1 for i in items if i["cat"] in chosen_cats)
            if available == 0:
                st.warning("Pick at least one category.")
            else:
                top = min(100, available)
                n = st.slider("How many questions?", 1, top, min(10, top)) if top > 1 else 1
                if st.button("🎯 Generate quiz", type="primary"):
                    if use_ai and not model:
                        st.error("Local AI needs Ollama + a model (see the sidebar), or switch to Quick.")
                    else:
                        with st.spinner("Making questions..."):
                            quiz, errors = build_quiz(items, chosen_cats, n, use_ai, model)
                        if quiz:
                            st.session_state.quiz, st.session_state.answers = quiz, {}
                            st.session_state.run += 1
                            if len(quiz) < n:
                                st.info(f"Made {len(quiz)} good questions (the rest of the facts were too vague to ask about).")
                        else:
                            st.error("Could not make questions. " + (errors[0] if errors else ""))

    quiz, answers = st.session_state.quiz, st.session_state.answers
    if not quiz:
        if up is None:
            st.info("👆 Upload a PDF to begin.")
        return

    score = sum(1 for i, q in enumerate(quiz) if answers.get(i) == q["answer"])
    st.divider()
    a, b, c = st.columns(3)
    a.metric("Questions", len(quiz))
    b.metric("Answered", len(answers))
    c.metric("Correct ✅", score)
    st.progress(len(answers) / len(quiz))

    for i, q in enumerate(quiz):
        st.markdown(f"#### Q{i + 1}. {esc(q['question'])}")
        st.caption(f"📂 {q['category']}")
        if i not in answers:
            key = f"r_{st.session_state.run}_{i}"
            st.radio("Choose one", q["options"], index=None, key=key, format_func=esc,
                     label_visibility="collapsed", on_change=record_answer, args=(i, key))
        else:
            chosen = answers[i]
            if chosen == q["answer"]:
                st.success("✅ Correct!")
            else:
                st.error(f"❌ Wrong. Correct answer: **{esc(q['answer'])}**")
            for k, opt in enumerate(q["options"]):
                mark = "✅" if opt == q["answer"] else ("❌" if opt == chosen else "▫️")
                st.markdown(f"{mark} **{chr(65 + k)}.** {esc(opt)}")
            if q.get("explanation"):
                st.markdown(f"💡 {esc(q['explanation'])}")
            st.caption(f"📄 From the PDF: {esc(q['source'])}")
        st.write("")

    if len(answers) == len(quiz):
        pct = round(100 * score / len(quiz))
        st.success(f"🎉 Quiz finished! You scored {score}/{len(quiz)} ({pct}%).")
    st.button("🔄 Restart these questions", on_click=restart_quiz)


if __name__ == "__main__":
    main()