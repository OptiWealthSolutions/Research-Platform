"""Boolean full-text search shared by /api/papers and the MCP surface.

Grammar (case-insensitive), precedence NOT > AND > OR:
    expr    := or
    or      := and ( OR and )*
    and     := not ( [AND] not )*        # juxtaposition is an implicit AND
    not     := NOT not | '-' primary | primary
    primary := '(' expr ')' | "phrase" | term

Before 2026-11-13 the tokenizer never emitted parentheses and two juxtaposed
terms were parsed as the first one only ("rate cut" matched every "rate").
"""
from __future__ import annotations

import re

_OPS = {"AND", "OR", "NOT"}


def tokenize(q: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    i, n = 0, len(q or "")
    while i < n:
        c = q[i]
        if c.isspace():
            i += 1
            continue
        if c in "()":
            tokens.append(("OP", c))
            i += 1
            continue
        if c == '"':
            j = q.find('"', i + 1)
            j = n if j < 0 else j
            phrase = q[i + 1:j].strip().lower()
            if phrase:
                tokens.append(("PHRASE", phrase))
            i = j + 1
            continue
        if c == "-" and i + 1 < n and not q[i + 1].isspace():
            tokens.append(("OP", "NOT"))
            i += 1
            continue
        j = i
        while j < n and not q[j].isspace() and q[j] not in '()"':
            j += 1
        word = q[i:j]
        if word.upper() in _OPS:
            tokens.append(("OP", word.upper()))
        elif word:
            tokens.append(("TERM", word.lower()))
        i = j
    return tokens


def parse(q: str):
    toks = tokenize(q)
    pos = 0

    def peek():
        return toks[pos] if pos < len(toks) else None

    def p_or():
        nonlocal pos
        node = p_and()
        while peek() == ("OP", "OR"):
            pos += 1
            node = ("OR", node, p_and())
        return node

    def p_and():
        nonlocal pos
        node = p_not()
        while True:
            t = peek()
            if t == ("OP", "AND"):
                pos += 1
                node = ("AND", node, p_not())
            elif t is not None and t not in (("OP", "OR"), ("OP", ")")):
                node = ("AND", node, p_not())
            else:
                return node

    def p_not():
        nonlocal pos
        if peek() == ("OP", "NOT"):
            pos += 1
            return ("NOT", p_not())
        return p_primary()

    def p_primary():
        nonlocal pos
        t = peek()
        if t is None:
            return None
        if t == ("OP", "("):
            pos += 1
            node = p_or()
            if peek() == ("OP", ")"):
                pos += 1
            return node
        pos += 1
        if t[0] == "OP":            # stray operator: ignore it
            return None
        return t

    tree = p_or() if toks else None
    return tree


def terms(tree) -> list[str]:
    """Positive terms/phrases of the expression (for an SQL LIKE pre-filter)."""
    if tree is None:
        return []
    k = tree[0]
    if k in ("TERM", "PHRASE"):
        return [tree[1]]
    if k == "NOT":
        return []
    return terms(tree[1]) + terms(tree[2])


def _word_re(t: str):
    return re.compile(r"(?<![a-z0-9])" + re.escape(t))


def match(tree, blob: str) -> bool:
    if tree is None:
        return True
    k = tree[0]
    if k in ("TERM", "PHRASE"):
        return bool(_word_re(tree[1]).search(blob))
    if k == "NOT":
        return not match(tree[1], blob)
    if k == "AND":
        return match(tree[1], blob) and match(tree[2], blob)
    if k == "OR":
        return match(tree[1], blob) or match(tree[2], blob)
    return True


def text_blob(p, with_full_text: bool = True) -> str:
    parts = [p.title, p.abstract, p.summary, p.authors, p.source]
    if with_full_text:
        parts.append(getattr(p, "full_text", None))
    return " ".join(str(x or "") for x in parts).lower()


def required_terms(tree):
    """Necessary condition of the expression as an OR-set of terms, or None
    when no positive term is required (a NOT under an OR, empty query).
    Every matching document contains at least one term of the returned set,
    so it is a safe SQL LIKE pre-filter."""
    if tree is None:
        return None
    k = tree[0]
    if k in ("TERM", "PHRASE"):
        return {tree[1]}
    if k == "NOT":
        return None
    a, b = required_terms(tree[1]), required_terms(tree[2])
    if k == "AND":
        if a is None:
            return b
        if b is None:
            return a
        return a if len(a) <= len(b) else b
    if k == "OR":
        return None if (a is None or b is None) else (a | b)
    return None
