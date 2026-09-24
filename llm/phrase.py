"""phrase — render the engine's terminal as brief natural language. LLM job 3 of 3.

The model adds nothing the engine did not decide (§4.2). That is enforced two ways:
1. the model is only ever shown the terminal's own KB content, never the KB at large;
2. ``guard`` runs deterministic checks on the output per terminal kind — a refusal must
   still refuse, a caution must keep every condition, a question must still ask — and on
   ANY violation the reply falls back to ``render_verbatim``: the terminal's KB text,
   unphrased. A safety-relevant reply is therefore never worse than the KB's own words.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path

import yaml

from engine.terminals import REASON_NOT_ISOLATED, REASON_RECURRED, REASON_SECOND_RESET, RESET_DONE_NOTE, Terminal

# How each refusal reason is to be framed (the verdict text is the KB's; only the framing differs).
_REASON_FRAMING = {
    REASON_SECOND_RESET: "the pilot states the relay was already reset once earlier this trip",
    REASON_RECURRED: "the relay re-tripped after the first reset — the fault is real; do not reset "
                     "again; get relief",
    "abnormality_found": "an abnormality was found in the checks",
    REASON_NOT_ISOLATED: "the abnormal equipment could not be isolated",
}
from llm.interface import LLMProvider

PROMPT_PATH = Path(__file__).with_name("prompts") / "phrase.md"
MAX_CHARS = 600


@dataclass(frozen=True)
class PhraseResult:
    text: str
    used_fallback: bool
    violations: tuple[str, ...] = field(default=())


# ---------------------------------------------------------------------------
# deterministic rendering (fallback + the content the model is shown)
# ---------------------------------------------------------------------------

_STOP = {"everything", "whether", "otherwise", "further", "normal", "abnormality", "condition",
         "anything", "before", "after", "through", "again", "should", "please", "maximum",
         # instruction meta-words with everyday synonyms (Ch.7 texts: "Report whether…",
         # "For quick trouble shooting…", "Ensure…" → "make sure")
         "report", "trouble", "shooting", "ensure", "properly", "position", "convenient",
         "placed", "identified", "making", "proper",
         # generic instruction / procedure words a spoken rendering naturally replaces (batch 3)
         "necessary", "precautions", "precaution", "operation", "operated", "operating", "setting",
         "required", "followed", "following", "inform", "remarks", "opportunity", "observe", "observing",
         "according", "manual", "upwards", "downwards", "rectify", "reduced", "prohibited", "destination"}

_PAREN = re.compile(r"\([^)]*\)")


def _substance(text: str) -> str:
    """The part of a KB step the rendering must keep: its action clause, without a leading
    "If …," condition and without parenthetical notes (cause lists, cross-references)."""
    text = re.sub(r"^\s*If\s.+?[,;:]", "", text, count=1, flags=re.IGNORECASE | re.DOTALL)
    return _PAREN.sub(" ", text)


def _stem(w: str) -> str:
    """Crude stem so 'closing' survives as 'close', 'pressed' as 'press' (substance guard)."""
    return re.sub(r"(ing|ed|es|s|e)$", "", w)


_TRUCK_SWAP = {"1": "2", "2": "1"}


def _foreign_identifiers(t: Terminal, low: str) -> list[str]:
    """Identifiers of the OTHER truck named in the reply (RSI-1 for an RSI-2 step): the phrase
    prompt forbids naming a component from a different truck or circuit."""
    own = set(_identifiers(t.message))
    out = []
    for i in own:
        m = re.fullmatch(r"([A-Z]+-?)([12])", i)
        if m:
            other = f"{m.group(1)}{_TRUCK_SWAP[m.group(2)]}"
            if other not in own and re.search(rf"(?<![\w-]){re.escape(other.lower())}(?![\w-])", low):
                out.append(other)
    return sorted(set(out))


_VOCAB_PATH = Path(__file__).resolve().parents[1] / "kb" / "vocabulary.yaml"


@lru_cache(maxsize=1)
def _load_vocabulary() -> tuple[dict, ...]:
    if not _VOCAB_PATH.exists():
        return ()
    return tuple(yaml.safe_load(_VOCAB_PATH.read_text(encoding="utf-8")) or ())


def _wrong_equipment_noun(t: Terminal, low: str) -> list[str]:
    """A term named in this terminal's own KB text must never be rendered with a
    recorded wrong substitute (kb/vocabulary.yaml) — e.g. 'breakers' for 'breathers'.
    Reject list: does not require exact wording, only refuses a known confusion."""
    kb_low = " ".join([t.message, *t.guidance]).lower()
    hits = []
    for entry in _load_vocabulary():
        term = entry["applies_when"].lower()
        # allow_suffix: the KB writes this identifier with a unit number (MVMT-1, MVMT1,
        # MVMT2) as well as bare, and all forms mean the same component. Only the KB-side
        # match widens; the forbidden-phrase match keeps the strict boundary.
        if entry.get("allow_suffix"):
            pat = rf"(?<![\w-]){re.escape(term)}-?\d*(?![\w-])"
        else:
            pat = rf"(?<![\w-]){re.escape(term)}(?![\w-])"
        if not re.search(pat, kb_low):
            continue
        for bad in entry["forbidden"]:
            if re.search(rf"(?<![\w-]){re.escape(bad.lower())}(?![\w-])", low):
                hits.append(f"{entry['applies_when']}->{bad}")
    return hits


def _key_words_present(clause: str, low: str) -> bool:
    """A KB clause survives phrasing if its distinctive words (≥5 letters, or an identifier) do."""
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9-]+", clause) if len(w) >= 5 or w.isupper()]
    return all(w.lower() in low for w in words)


def render_verbatim(t: Terminal) -> str:
    if t.kind == "confirm":
        return " ".join([t.message, *t.guidance]).strip()
    if t.kind == "ask_step":
        hold = f" Hold the {t.hold_action.replace('_', ' ')} until this is done." if t.hold_action else ""
        if t.overview:
            items = "\n".join(f"{i}. {x}" for i, x in enumerate(t.overview, 1))
            return (f"Checks to do, in order:\n\n{items}\n\nWhat comes next depends on what these "
                    f"checks find.{hold} Start with check 1 and tell me what you found.")
        if t.do_now:
            return f"Do this now: {t.message}{hold} Then tell me what you found."
        return f"Next check: {t.message}{hold} Done?"
    if t.kind == "ask_history":
        return t.message
    if t.kind == "caution":
        cond = "If no abnormality was found in the checks: " if t.conditional else ""
        return f"{cond}{t.message}"
    if t.kind == "refuse":
        return t.message
    if t.kind in ("confirm_fault", "clarify", "ask_config"):
        return t.message
    if t.kind == "defer_to_TLC":
        return t.message
    raise AssertionError(t.kind)


_IF_HEAD = re.compile(r"^\s*If\s+(.+?)[,;:]\s*(.+)$", re.IGNORECASE | re.DOTALL)
_IDENT = re.compile(r"\b[A-Z][A-Z0-9]*(?:[-/][A-Z0-9]+)*\b")
# Standalone numeric VALUES in a step — pressures, positions, bit numbers, speeds, seconds,
# loads. A paraphrase may reword them but must not drop them (seen live 2026-09-18: §11.02's
# reverser-bit numbers rendered as "the bit positions ... as laid out"). Digits inside an
# identifier (TM-1, HMCS-2) are not values; _identifiers already covers those.
_NUM = re.compile(r"(?<![\w\-/])(\d+(?:\.\d+)?)(?:st|nd|rd|th)?(?![\w])")
LONG_LIST = 6            # a check naming this many components may be spoken in short form
_SYMPTOM = re.compile(r"\b(smoke|smell|abnormal\w*|fire|heat|hot|temperature|leak\w*|damage\w*)\b", re.I)
_OFFER = re.compile(r"\b(list|detail|components?|items?)\b[^.?!]*\?", re.I)


def _numbers(text: str) -> list[str]:
    """Numeric values in a KB step's ACTION clause, deduplicated ('5.0', '8', '30')."""
    return list(dict.fromkeys(_NUM.findall(_substance(text))))


def _identifiers(text: str) -> list[str]:
    """Equipment identifiers in a KB step's ACTION clause, in order, deduplicated."""
    return list(dict.fromkeys(m for m in _IDENT.findall(_substance(text)) if len(m) >= 2))


# Ch.7 ladders chain their steps with "If unsuccessful, …" / "If still unsuccessful, …" /
# "If not successful, …": a connector, not a condition the pilot reports. Stripped before
# the payload is built, so it is never shown as `condition_already_met: unsuccessful` (seen
# live: "The safety relays are already showing unsuccessful").
_LADDER_HEAD = re.compile(r"^\s*If\s+(still\s+)?(un|not\s+)?successful\s*[,;:]\s*", re.IGNORECASE)


def _strip_ladder_head(text: str) -> str:
    return _LADDER_HEAD.sub("", text, count=1)


def terminal_payload(t: Terminal) -> str:
    lines = [f"kind: {t.kind}"]
    if t.kind == "ask_step":
        t = replace(t, message=_strip_ladder_head(t.message))
    m = _IF_HEAD.match(t.message) if t.kind == "ask_step" else None
    if m:
        # A conditional KB step reached because its condition holds: the pilot is asked about
        # the ACTION, never about the condition (seen live: "have you checked whether it drops
        # in a particular position?" for "isolate that traction motor").
        lines.append(f"condition_already_met: {' '.join(m.group(1).split())}")
        lines.append(f"content: {' '.join(m.group(2).split())}")
    else:
        lines.append(f"content: {t.message}")
    if t.guidance:
        lines.append(f"guidance: {' '.join(t.guidance)}")
    if t.kind == "ask_step":
        ids = _identifiers(t.message)
        if len(ids) >= LONG_LIST:
            lines.append(f"long_list: yes — {len(ids)} components; subsystem tags: {', '.join(ids)}")
    if t.kind == "ask_step" and t.do_now:
        lines.append("do_now: the pilot has said this check is NOT done. Tell them to do it now and "
                     "report what they find. Do NOT ask whether they have done it.")
    if t.kind == "ask_step" and t.preconditions_met:
        lines.append("preconditions_met: " + ", ".join(p.replace("_", " ") for p in t.preconditions_met)
                     + " — already confirmed by the pilot; the action is cleared. Do NOT ask about "
                       "them again; put the action and its working precautions.")
    if t.hold_action:
        lines.append(f"hold_action: the pilot intends to {t.hold_action.replace('_', ' ')} — "
                     f"say plainly that this waits until the check is done")
    if t.conditional:
        lines.append("conditional: yes — applies only if no abnormality was found")
    if t.reasons:
        lines.append("reasons: " + "; ".join(f"{r} ({_REASON_FRAMING.get(r, r)})" for r in t.reasons))
    if t.unrecognised_claims:
        lines.append(f"unrecognised_claims (mention briefly that these are not in the procedure): "
                     f"{', '.join(t.unrecognised_claims)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# guard
# ---------------------------------------------------------------------------

_NEGATION = re.compile(r"\b(not|don'?t|never|no|neither|nor|without)\b", re.I)
_RESET = re.compile(r"\bre-?set", re.I)
# "reset" referring to a reset ALREADY MADE — as a noun ("after your reset", "the first
# reset") or a past-tense verb with a subject ("after you reset it", "it was reset") — is
# not an instruction. Stripped before the instruction check.
_RESET_AS_EVENT = re.compile(
    r"\b(after|following|since|before|post|the|your|that|this|a|first|earlier|previous|last|second)"
    r"(\s+(first|last|earlier|previous|second|initial))?\s+re-?set(s|ting)?\b"
    r"|\b(you|we|i|they|he|she|pilot|it|was|were|been|already|having|have|had)"
    r"(\s+(have|had|already|just|then))?\s+re-?set\b", re.I)


def _sentences(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"(?<=[.!?;])\s+|\n+", text) if p.strip()]


def _unnegated_reset_sentences(text: str) -> list[str]:
    """Sentences that read as a reset INSTRUCTION: a reset verb with no negation, after
    discounting noun uses that merely refer to a reset already made."""
    out = []
    for p in _sentences(text):
        stripped = _RESET_AS_EVENT.sub(" ", p)
        if _RESET.search(stripped) and not _NEGATION.search(p):
            out.append(p)
    return out


_HOLD_WORDS = re.compile(r"\b(hold|held|holds|holding|wait|waits|waited|waiting|do not (?:move|resume|proceed))\b")


def _added_hold(t: Terminal, low: str) -> bool:
    """A hold / wait the ENGINE did not issue — but not the step's own 'wait for 15 seconds' or
    'DJ closed and held' (Ch.7 ladders), which the payload carries."""
    key = lambda w: "hold" if w.startswith("h") else ("wait" if w.startswith("w") else w)
    own = {key(m.group(1)) for m in _HOLD_WORDS.finditer(t.message.lower())}
    return any(key(m.group(1)) not in own for m in _HOLD_WORDS.finditer(low))


def guard(t: Terminal, text: str) -> tuple[str, ...]:
    v: list[str] = []
    s = text.strip()
    low = s.lower()
    if not s:
        return ("empty",)
    if len(s) > max(MAX_CHARS, int(1.3 * len(" ".join([t.message, *t.guidance])))):
        v.append("too_long")                       # the payload's own length bounds a long step
    if re.search(r"^\s*([-*#]|\d+\.)\s", s, re.M):
        v.append("markdown_structure")
    # Recorded wrong-noun substitutions (kb/vocabulary.yaml) — checked for EVERY terminal
    # kind: a caution's or confirm's guidance carries the same equipment text as a step.
    wrong_nouns = _wrong_equipment_noun(t, low)
    if wrong_nouns:
        v.append("equipment_noun_substituted:" + ",".join(wrong_nouns))

    if t.kind == "refuse":
        # An isolation-failed refusal's KB text is "contact TLC" (§6.1.2(b)) with no literal
        # "do not reset"; every other refusal must keep an explicit negated reset.
        needs_negation = t.reasons != (REASON_NOT_ISOLATED,)
        if needs_negation and not any(_RESET.search(p) and _NEGATION.search(p) for p in _sentences(s)):
            v.append("refusal_not_negated")
        if _unnegated_reset_sentences(s):
            v.append("refusal_instructs_reset")
        if "tlc" not in low:
            v.append("refusal_missing_TLC")
        if "relief" in t.message.lower() and "relief" not in low:
            v.append("refusal_missing_relief")
        if "fire extinguisher" in t.message.lower() and "extinguisher" not in low:
            v.append("refusal_missing_fire_precaution")
        # (f)(ii) framing must reflect the actual situation (engine-decided reason)
        if REASON_RECURRED in t.reasons and not re.search(
                r"\b(re-?trip\w*|re-?lock\w*|recur\w*|second time|twice|once now|(acted|tripped|dropped|locked) again"
                r"|after (the|your|that) reset|after you reset)\b", low):
            v.append("refusal_recurrence_framing_missing")
        if REASON_SECOND_RESET in t.reasons and REASON_RECURRED not in t.reasons and not re.search(
                r"\b(already|earlier|once|second time|twice|again)\b", low):
            v.append("refusal_prior_reset_framing_missing")
    elif t.kind == "caution":
        for key, needle in (("once", "once"), ("interval", "10 min"), ("TLC", "tlc"), ("log", "log")):
            if needle in t.message.lower() and needle not in low:
                v.append(f"caution_missing_{key}")
        # ...and must not ADD conditions from another procedure (seen live: QLM's 10-minute
        # checks and log-book remark rendered on to the QLA caution, which has neither)
        for key, needle in (("interval", "10 min"), ("log", "log"), ("relief", "relief")):
            if needle in low and needle not in t.message.lower():
                v.append(f"caution_added_{key}")
        if t.conditional and "abnormal" not in low:
            v.append("caution_dropped_condition")
    if t.kind == "ask_step":
        # The rendering must keep the step's substance: its equipment identifiers (HMCS-2,
        # RSI-2, L4...) and the distinctive words of its ACTION clause (isolate, traction,
        # motor, permissible...). Seen live: an action rendered as a question about its own
        # "If ..." condition, and "the equipment listed above" for a full checklist.
        action = _substance(t.message)
        idents = set(_identifiers(t.message))
        words = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z-]{5,}", action)} - _STOP
        lost_i = [i for i in idents if i.lower() not in low]
        lost_w = [w for w in words if _stem(w) not in low and w[:6] not in low]   # energisation ≈ energise
        # Sanctioned SHORT FORM for a long component list (phrase prompt): the symptom, at
        # least one of the payload's own identifiers as the subsystem tag, and an offer of the
        # exact list. Anything else must keep the substance.
        short_form = (len(idents) >= LONG_LIST and len(lost_i) < len(idents)
                      and _SYMPTOM.search(low) and _OFFER.search(s))
        nums = _numbers(t.message)
        lost_n = [n for n in nums if n not in set(_NUM.findall(low))]
        if not short_form and ((idents and len(lost_i) / len(idents) > 0.34)
                               or (words and len(lost_w) / len(words) > 0.6)
                               or (nums and len(lost_n) / len(nums) > 0.25)):
            v.append("ask_step_lost_substance")           # a paraphrase keeps most; a substitution loses most
        foreign = _foreign_identifiers(t, low)
        if foreign:
            v.append("ask_step_named_other_circuit:" + ",".join(foreign))
    if t.kind == "ask_step" and t.do_now:
        if re.search(r"\bhave you\b|\bdid you\b|\bhas .* been\b", low):
            v.append("do_now_asked_again")               # they said no; do not ask again
        if t.hold_action and not re.search(r"\b(before|until|after|hold|wait|first)\b", low):
            v.append("hold_action_dropped")
        if not t.hold_action and _added_hold(t, low):
            v.append("added_hold_instruction")
    elif t.kind in ("ask_step", "ask_history", "confirm_fault", "clarify", "ask_config"):
        # an ask_step (a due, ungated step) may also be put as the action with a report-back
        # closer — the model does this for mid-ladder actions and it is not a safety matter;
        # questions of the other kinds must stay questions
        report_back = t.kind == "ask_step" and re.search(r"\b(report|tell me|let me know)\b", low)
        if "?" not in s and not report_back:
            v.append("question_not_asked")
        if t.kind == "ask_history" and t.message.startswith("Which applies now"):
            # the branch question must keep every alternative the KB names
            alts = [a.strip() for a in t.message[len("Which applies now:"):].split("? Or")[0].split("; or")]
            if any(not _key_words_present(a, low) for a in alts):
                v.append("branch_question_dropped_alternative")
        if t.kind == "ask_step" and t.hold_action and not re.search(r"\b(before|until|after|hold|wait|first)\b", low):
            v.append("hold_action_dropped")
        if t.kind == "ask_step" and not t.hold_action and _added_hold(t, low):
            v.append("added_hold_instruction")            # the engine issued no hold
    elif t.kind == "confirm":
        if any("10 min" in g.lower() for g in t.guidance) and "10 min" not in low:
            v.append("confirm_missing_guidance")
        if (any("do not reset" in g.lower() for g in t.guidance)
                and not re.search(r"\b(not|never) reset\b|\bonly once\b|\bsecond time\b", low)):
            v.append("confirm_missing_forward_rule")      # §6.1.1(f)(ii) warning must survive
        if "relief" in low and not any("relief" in g.lower() for g in (t.message, *t.guidance)):
            v.append("confirm_added_relief")              # not in this payload → not the pilot's instruction
        if RESET_DONE_NOTE in t.message and re.search(r"\breset qlm once and\b", low):
            v.append("confirm_reinstructs_done_reset")    # the reset is done; do not tell them to do it
    elif t.kind == "defer_to_TLC":
        if "tlc" in t.message.lower() and "tlc" not in low:
            v.append("defer_missing_TLC")
        if "relief" in t.message.lower() and "relief" not in low:
            v.append("defer_missing_relief")             # §7.04(b) "ask for relief engine"
        if "procedure set" in t.message and "verify:" in t.message:
            names = [n.strip().lower() for n in t.message.split("verify:")[1].split(".")[0].split(",")]
            if any(n and n not in low for n in names):
                v.append("out_of_scope_dropped_coverage")     # §5.6: every covered fault must be named
    return tuple(v)


REPEAT_MESSAGE = "Nothing further is required by the procedure at this point."
REPEAT_CUE = ("repeat: the pilot asked again / acknowledged without new information; this is what "
              "they were told last turn. Render the content sentence, then the standing follow-up "
              "briefly — no verbatim replay of the earlier confirmation.")


def for_repeat(t: Terminal) -> Terminal:
    """A repeat is rendered from an engine-owned sentence (not the KB confirmation again) with
    the same guidance; the sentence is the engine's, so the verbatim fallback reads right too.
    Engine facts riding on the message (the reset-done note) are kept."""
    keep = " " + RESET_DONE_NOTE if RESET_DONE_NOTE in t.message else ""
    return replace(t, message=REPEAT_MESSAGE + keep)


def phrase(t: Terminal, provider: LLMProvider, repeat: bool = False) -> PhraseResult:
    """``repeat``: the pilot gave no new information and this is the same terminal as last
    turn. A confirm is then rendered from the engine's "nothing further" sentence; a pending
    question / caution / refusal is simply restated (it is still pending)."""
    if t.verbatim:
        return PhraseResult(render_verbatim(t), False, ())        # the pilot asked for the KB text itself
    system = PROMPT_PATH.read_text(encoding="utf-8")
    repeat = repeat and t.kind == "confirm"
    if repeat:
        t = for_repeat(t)
    payload = terminal_payload(t) + ("\n" + REPEAT_CUE if repeat else "")
    try:
        text = provider.text(system, payload)
    except Exception as exc:  # provider failure → the KB's own words, never silence
        return PhraseResult(render_verbatim(t), True, (f"provider_error:{type(exc).__name__}",))
    violations = guard(t, text)
    if violations:
        return PhraseResult(render_verbatim(t), True, violations)
    return PhraseResult(text.strip(), False)
