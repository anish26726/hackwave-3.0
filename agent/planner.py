# AccessOS — Task Planner (Phase 7)
#
# Rule-based compound goal decomposer.
# Splits multi-step user commands into an ordered list of single-step sub-tasks.
# No extra AI API call — fast, free, deterministic.
#
# Examples:
#   "Open Chrome and search for AI hackathons"
#       → ["open chrome", "search for AI hackathons"]
#
#   "First find report.pdf, then open it, and read it to me"
#       → ["find report.pdf", "open it", "read it to me"]
#
#   "Open Chrome, go to github.com, and read the webpage"
#       → ["open chrome", "go to github.com", "read the webpage"]

import re
from dataclasses import dataclass, field
from typing import Optional

# ── Configuration ─────────────────────────────────────────────────────────
MAX_STEPS = 10          # Hard cap on steps per plan
MIN_STEP_WORDS = 2      # Ignore fragments shorter than this


# ── Action verbs recognised as the start of a new step ───────────────────
# 'and <verb>' is treated as a step boundary only when the verb is one of these.
_ACTION_VERBS = (
    r'open|search|go|find|read|navigate|visit|refresh|reload|click|type|scroll|'
    r'close|create|rename|move|copy|delete|download|play|pause|send|submit|save|'
    r'show|display|tell|get|fetch|look\s+up|check|print|write|launch|start|stop'
)

# ── Step-splitting connectors ─────────────────────────────────────────────
# Order matters — more specific patterns first
_CONNECTORS = [
    # "first … then …"  / "first … and then …"
    r'\bfirst\b',
    # Explicit sequencers
    r'\band\s+then\b',
    r'\bthen\b',
    r'\bafter\s+that\b',
    r'\bafterwards?\b',
    r'\bnext\b(?!\s+(?:to|tab|page|result))',  # "next" but not "next tab/page/result"
    r'\bfinally\b',
    r'\blastly\b',
    r'\balso\b',
    r'\bfollowed\s+by\b',
    # "and <action_verb>" — plain 'and' between two action phrases.
    # Lookahead keeps the verb in the NEXT fragment (correct step start).
    rf'\band\s+(?={_ACTION_VERBS})',
]

# Compile a single splitter that matches any connector
_CONNECTOR_RE = re.compile(
    r'\s*(?:' + '|'.join(_CONNECTORS) + r')\s*',
    re.IGNORECASE,
)

# Comma-split only when both sides have ≥ MIN_STEP_WORDS words
_COMMA_RE = re.compile(r'\s*,\s*')

# Words / patterns that indicate this is a compound goal
_MULTI_INDICATORS = re.compile(
    r'\b(?:'
    r'and\s+then|then|after\s+that|afterwards?|'
    r'first\b.+\bthen\b|finally|lastly|followed\s+by|'
    r'also\b'
    r')\b'
    # Also match: "and <action_verb>"
    rf'|and\s+(?:{_ACTION_VERBS})\b',
    re.IGNORECASE,
)

# Strip leading action words that bleed across connector splits
_LEADING_NOISE = re.compile(
    r'^(?:and|also|then|please|just|now|quickly|after\s+that)\s+',
    re.IGNORECASE,
)


@dataclass
class TaskPlan:
    """Represents a decomposed multi-step task plan."""
    original: str
    steps: list[str]
    max_steps: int = MAX_STEPS

    @property
    def is_multi(self) -> bool:
        return len(self.steps) > 1

    def __str__(self) -> str:
        numbered = ' → '.join(f'({i+1}) {s}' for i, s in enumerate(self.steps))
        return f'Plan: {numbered}'


def is_multi_step(text: str) -> bool:
    """
    Return True if *text* describes a compound goal that should be decomposed
    into multiple sub-tasks.

    Conservative — only returns True when connectors are clearly present.
    Single-step commands must NOT be split.
    """
    t = text.strip()
    if len(t.split()) < 4:
        # Very short commands are never multi-step
        return False

    # Must contain a connector word
    if not _MULTI_INDICATORS.search(t):
        # Secondary check: 3+ comma-separated clauses
        parts = _COMMA_RE.split(t)
        if len(parts) >= 3 and all(len(p.split()) >= MIN_STEP_WORDS for p in parts):
            return True
        return False

    return True


def decompose(text: str) -> TaskPlan:
    """
    Decompose a compound goal into an ordered list of single-step sub-tasks.

    Returns a TaskPlan. If the goal is not multi-step, TaskPlan.steps will
    contain only the original text as a single step.

    Args:
        text: User's natural language goal.

    Returns:
        TaskPlan with .steps populated.
    """
    text = text.strip()

    if not is_multi_step(text):
        return TaskPlan(original=text, steps=[text])

    # Step 1: Split on strong sequencing connectors first
    parts = _CONNECTOR_RE.split(text)

    # Step 2: If that didn't split much, also try comma splitting on long fragments
    final_parts = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # If a fragment has internal commas, try splitting there too.
        # Filter blank tokens first — trailing commas (e.g. "X, and Y" → "X, ")
        # produce an empty string that would wrongly fail the word-count check.
        sub = [s.strip() for s in _COMMA_RE.split(part) if s.strip()]
        if len(sub) >= 2 and all(len(s.split()) >= MIN_STEP_WORDS for s in sub):
            final_parts.extend(sub)
        else:
            final_parts.append(part)

    # Step 3: Clean each step
    steps = []
    for p in final_parts:
        p = p.strip().strip(',').strip()
        p = _LEADING_NOISE.sub('', p).strip()
        if p and len(p.split()) >= MIN_STEP_WORDS:
            steps.append(p)

    # Step 4: Remove exact duplicate consecutive steps
    deduped = []
    for s in steps:
        if not deduped or s.lower() != deduped[-1].lower():
            deduped.append(s)

    # Step 5: Enforce max cap
    if len(deduped) > MAX_STEPS:
        deduped = deduped[:MAX_STEPS]

    # If splitting produced nothing useful, fall back to original
    if not deduped:
        deduped = [text]

    return TaskPlan(original=text, steps=deduped)


def format_plan_preview(plan: TaskPlan) -> str:
    """Return a human-readable plan string to show the user before execution."""
    if not plan.is_multi:
        return ''
    lines = [f'[plan] Breaking into {len(plan.steps)} steps:']
    for i, step in enumerate(plan.steps, 1):
        lines.append(f'  Step {i}/{len(plan.steps)}: {step}')
    return '\n'.join(lines)
