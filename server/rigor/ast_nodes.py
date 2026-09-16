"""Expression and formula parsing for the math-rigor MCP server.

A single recursive-descent parser handles three input dialects, because a proof
assistant must accept whatever notation the mathematician already wrote:

* plain / Python-ish:  ``x**2 + 2*x``, ``forall n: n > 0 -> n + 1 > 0``
* LaTeX:               ``\\frac{x^2+1}{x-1}``, ``\\forall x \\in \\mathbb{R}``
* sympy-style calls:   ``Sum(k, (k, 1, n))``, ``Abs(x)``

It produces a small, printer-agnostic AST.  Translation to sympy and z3 lives in
``translate.py``; logic checking lives in ``logic.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any, Iterable, Sequence


class ParseError(ValueError):
    """Raised when an input expression cannot be parsed."""


# --------------------------------------------------------------------------- #
# AST
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Node:
    """Base class for every AST node."""

    def text(self) -> str:
        return format_node(self, "text")

    def latex(self) -> str:
        return format_node(self, "latex")


@dataclass(frozen=True)
class Num(Node):
    value: str  # raw literal text, converted lazily

    def number(self) -> Any:
        if re.fullmatch(r"[+-]?\d+", self.value):
            return int(self.value)
        return float(self.value)


@dataclass(frozen=True)
class Sym(Node):
    name: str


@dataclass(frozen=True)
class App(Node):
    """Function or predicate application: ``f(x, y)``."""

    name: str
    args: tuple[Node, ...] = ()


@dataclass(frozen=True)
class TupleN(Node):
    items: tuple[Node, ...] = ()


@dataclass(frozen=True)
class Neg(Node):
    operand: Node


@dataclass(frozen=True)
class Bin(Node):
    op: str  # add | sub | mul | div | mod | pow
    left: Node
    right: Node


@dataclass(frozen=True)
class Cmp(Node):
    op: str  # eq | ne | lt | le | gt | ge
    left: Node
    right: Node


@dataclass(frozen=True)
class Not(Node):
    operand: Node


@dataclass(frozen=True)
class And(Node):
    parts: tuple[Node, ...] = ()


@dataclass(frozen=True)
class Or(Node):
    parts: tuple[Node, ...] = ()


@dataclass(frozen=True)
class Imp(Node):
    left: Node
    right: Node


@dataclass(frozen=True)
class Iff(Node):
    left: Node
    right: Node


@dataclass(frozen=True)
class Quant(Node):
    kind: str  # forall | exists
    var: str
    sort: str  # int | real | rat | nat | bool | uninterpreted
    body: Node


@dataclass(frozen=True)
class Sqrt(Node):
    operand: Node


@dataclass(frozen=True)
class Abs(Node):
    operand: Node


@dataclass(frozen=True)
class BigOp(Node):
    """``Sum`` / ``Product`` over a symbolic range."""

    kind: str  # sum | prod
    expr: Node
    var: str
    lo: Node
    hi: Node


LOGIC_NODES = (Not, And, Or, Imp, Iff, Quant, Cmp)


def is_formula(node: Node) -> bool:
    """True when the node's outermost connective is propositional."""
    return isinstance(node, LOGIC_NODES)


# --------------------------------------------------------------------------- #
# Tokenizer
# --------------------------------------------------------------------------- #

_TOKEN_SPEC: list[tuple[str, str]] = [
    ("ws", r"[ \t\r\n]+"),
    ("cmd", r"\\[A-Za-z]+"),
    ("arrow", r"<->|<=>|==>|-->|->|=>|←|→"),  # matched before generic ops
    ("num", r"(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?"),
    ("id", r"[A-Za-z_][A-Za-z_0-9']*"),
    ("op", r"\|\||&&|\*\*|//|<=|>=|!=|==|\.\.\.|[+\-*/%^=<>~!&|(),.:{}\[\]]"),
    ("uni", r"[¬∧∨↔≠≤≥∈∉∣∤]"),
    ("sqrt", r"√"),
    ("bb", r"[ℤℝℕℚℂ]"),
    ("logicconst", r"[⊥⊤]"),
    ("sum", r"[∑∏]"),
    ("cdot", r"[·×⋅]"),
    ("other", r"."),
]

_TOKEN_RE = re.compile("|".join(f"(?P<{n}>{p})" for n, p in _TOKEN_SPEC))


@dataclass(frozen=True)
class Token:
    kind: str
    text: str
    pos: int


def tokenize(source: str) -> list[Token]:
    text = _normalize_source(source)
    out: list[Token] = []
    pos = 0
    while pos < len(text):
        m = _TOKEN_RE.match(text, pos)
        if m is None:  # pragma: no cover - regex has an "other" catch-all
            raise ParseError(f"cannot tokenize at position {pos}: {text[pos:pos + 12]!r}")
        kind = m.lastgroup or "other"
        raw = m.group()
        pos = m.end()
        if kind == "ws":
            continue
        if kind == "other":
            raise ParseError(f"unexpected character {raw!r} at position {m.start()}")
        out.append(Token(kind, raw, m.start()))
    return _disambiguate_pipes(out)


# Tokens after which an expression may begin (so a following '|' opens |...|).
_EXPRESSION_STARTERS = {
    "(", "[", "{", ",", "=", "==", "!=", "<", "<=", ">", ">=", "->", "=>",
    "==>", "-->", "<->", "<=>", "+", "-", "*", "/", "%", "^", "**", "&", "&&",
    "∨", "∧", "~", "!", "¬", ":", ".", "|", "∣", "→", "↔", "∀", "∃",
}
_OPENERS = {"(": ")", "[": "]", "{": "}"}
_CLOSERS = {")", "]", "}"}
_NEGATIONS = {"~", "!", "¬"}


def _token_before_group(tokens: list[Token], close_index: int) -> Token | None:
    """The token naming the group ending at ``close_index``, as in ``P(x)``."""
    depth = 0
    for j in range(close_index, -1, -1):
        text = tokens[j].text
        if text in _CLOSERS:
            depth += 1
        elif text in _OPENERS:
            depth -= 1
            if depth == 0:
                return tokens[j - 1] if j > 0 else None
    return None


def _first_inside_before(tokens: list[Token], close_index: int) -> Token | None:
    """For a group ending at ``close_index``, the first token inside it."""
    depth = 0
    for j in range(close_index, -1, -1):
        text = tokens[j].text
        if text in _CLOSERS:
            depth += 1
        elif text in _OPENERS:
            depth -= 1
            if depth == 0:
                return tokens[j + 1] if j + 1 < close_index else None
    return None


def _looks_propositional(token: Token | None) -> bool:
    """Mathematical convention: an upper-case initial names a proposition.

    ``P | Q`` is a disjunction, ``6 | n`` is a divisibility statement.  The
    parser cannot know the types, so it uses the notation convention that
    propositions are written with capital letters, and documents the escape
    hatches (``||`` / ``or`` for disjunction, ``divides(a, b)`` or ``a % b = 0``
    for divisibility).
    """
    if token is None:
        return False
    if token.kind == "id":
        return token.text[:1].isupper()
    return False


def _propositional_on_the_left(tokens: list[Token], index: int) -> bool:
    if index == 0:
        return False
    previous = tokens[index - 1]
    if previous.text in _CLOSERS:
        # P(x) | ... reads as a disjunction of predicates
        if _looks_propositional(_token_before_group(tokens, index - 1)):
            return True
        return _looks_propositional(_first_inside_before(tokens, index - 1))
    return _looks_propositional(previous)


def _propositional_on_the_right(tokens: list[Token], index: int) -> bool:
    j = index + 1
    while j < len(tokens) and tokens[j].text in _NEGATIONS:
        j += 1
    if j >= len(tokens):
        return False
    token = tokens[j]
    if token.text in _OPENERS:
        return _looks_propositional(tokens[j + 1] if j + 1 < len(tokens) else None)
    return _looks_propositional(token)


def _mark_absolute_value_pipes(tokens: list[Token]) -> list[bool]:
    """Find '|' pairs that delimit an absolute value, so they stay untouched."""
    is_abs = [False] * len(tokens)
    index = 0
    while index < len(tokens):
        if tokens[index].text == "|":
            opens_expression = index == 0 or tokens[index - 1].text in _EXPRESSION_STARTERS
            if opens_expression:
                depth = 0
                for j in range(index + 1, len(tokens)):
                    text = tokens[j].text
                    if text in _OPENERS:
                        depth += 1
                    elif text in _CLOSERS:
                        depth -= 1
                    elif text == "|" and depth == 0:
                        is_abs[index] = is_abs[j] = True
                        index = j
                        break
        index += 1
    return is_abs


def _disambiguate_pipes(tokens: list[Token]) -> list[Token]:
    """Rewrite bare '|' into disjunction or divisibility.

    ``|`` carries three meanings in mathematical writing: absolute value,
    disjunction, and divisibility.  Absolute-value pairs are identified first,
    then the remaining bars are classified by the notation convention above.
    Resolving this at token level (rather than deep in the grammar) is what
    keeps ``~P | Q`` grouping as ``(~P) | Q`` instead of ``~(P | Q)``.
    """
    if not any(t.text == "|" for t in tokens):
        return tokens
    is_abs = _mark_absolute_value_pipes(tokens)
    out: list[Token] = []
    for index, token in enumerate(tokens):
        if token.text != "|" or is_abs[index]:
            out.append(token)
            continue
        if _propositional_on_the_left(tokens, index) and \
                _propositional_on_the_right(tokens, index):
            out.append(Token("uni", "∨", token.pos))
        else:
            out.append(Token("uni", "∣", token.pos))
    return out


_DROP_CMDS = {
    r"\left", r"\right", r"\,", r"\;", r"\!", r"\quad", r"\qquad", r"\,",
    r"\displaystyle", r"\limits", r"\big", r"\Big", r"\bigl", r"\bigr",
    r"\lvert", r"\rvert", r"\left.", r"\right.",
}

_CMD_REPLACE: dict[str, str] = {
    r"\land": "&&", r"\wedge": "&&", r"\lor": "||", r"\vee": "||",
    r"\lnot": "~", r"\neg": "~",
    r"\to": "->", r"\rightarrow": "->", r"\Rightarrow": "->", r"\implies": "->",
    r"\iff": "<->", r"\leftrightarrow": "<->", r"\Leftrightarrow": "<->",
    r"\neq": "!=", r"\ne": "!=", r"\leq": "<=", r"\le": "<=",
    r"\geq": ">=", r"\ge": ">=", r"\lt": "<", r"\gt": ">",
    r"\cdot": "*", r"\times": "*", r"\div": "/",
    r"\forall": "forall", r"\exists": "exists", r"\in": " in ",
    r"\pm": "±", r"\infty": "oo", r"\partial": "partial",
    r"\lfloor": "floor(", r"\rfloor": ")",
    r"\lceil": "ceil(", r"\rceil": ")",
}

_GREEK = {
    "alpha", "beta", "gamma", "delta", "epsilon", "varepsilon", "zeta", "eta",
    "theta", "vartheta", "iota", "kappa", "lambda_", "mu", "nu", "xi", "pi",
    "rho", "varrho", "sigma", "varsigma", "tau", "upsilon", "phi", "varphi",
    "chi", "psi", "omega", "Gamma", "Delta", "Theta", "Lambda", "Xi", "Pi",
    "Sigma", "Upsilon", "Phi", "Psi", "Omega",
}


def _normalize_source(source: str) -> str:
    """Strip LaTeX decoration and rewrite commands into the core dialect."""
    text = source.strip()
    # math delimiters
    text = re.sub(r"^\s*\$\$?(.*?)\$\$?\s*$", r"\1", text, flags=re.S)
    text = re.sub(r"^\s*\\\[(.*?)\\\]\s*$", r"\1", text, flags=re.S)
    text = re.sub(r"^\s*\\\((.*?)\\\)\s*$", r"\1", text, flags=re.S)
    # \left( ... \right) -> ( ... )
    for cmd in sorted(_DROP_CMDS, key=len, reverse=True):
        text = text.replace(cmd, " " if cmd.startswith("\\,") else "")
    for cmd, repl in sorted(_CMD_REPLACE.items(), key=lambda kv: -len(kv[0])):
        text = text.replace(cmd, f" {repl} ")
    # \frac{a}{b} -> (a)/(b)
    text = _rewrite_frac(text)
    # \sqrt[n]{a} / \sqrt{a}
    text = _rewrite_sqrt(text)
    # \sum_{k=a}^{b} / \prod_{k=a}^{b}
    text = _rewrite_bigop(text)
    # \mathbb{Z} -> Z  (sort markers)
    text = re.sub(r"\\mathbb\{([ZRNQC])\}", lambda m: m.group(1), text)
    text = re.sub(r"\\mathrm\{([A-Za-z]+)\}", lambda m: m.group(1), text)
    # greek letters
    def _greek(m: re.Match[str]) -> str:
        name = m.group(1)
        if name in _GREEK:
            return "lambda" if name == "lambda_" else name
        return name

    text = re.sub(r"\\([A-Za-z]+)", _greek, text)
    text = text.replace("±", "+/-")
    return text


def _read_group(text: str, start: int) -> tuple[str, int]:
    """Read a ``{...}`` group (or a single token) starting at ``start``."""
    i = start
    while i < len(text) and text[i].isspace():
        i += 1
    if i >= len(text):
        raise ParseError("unexpected end of input while reading a group")
    if text[i] != "{":
        if text[i] == "\\":  # single command
            m = re.match(r"\\[A-Za-z]+", text[i:])
            if m:
                return m.group(), i + m.end()
        return text[i], i + 1
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[i + 1:j], j + 1
    raise ParseError("unbalanced braces in input")


def _rewrite_frac(text: str) -> str:
    while True:
        idx = text.find(r"\frac")
        if idx < 0:
            return text
        num, after_num = _read_group(text, idx + len(r"\frac"))
        den, after_den = _read_group(text, after_num)
        text = f"{text[:idx]}(({num})/({den})){text[after_den:]}"


def _rewrite_sqrt(text: str) -> str:
    while True:
        idx = text.find(r"\sqrt")
        if idx < 0:
            return text
        pos = idx + len(r"\sqrt")
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if pos < len(text) and text[pos] == "[":
            end = text.find("]", pos)
            if end < 0:
                raise ParseError("unbalanced '[' in \\sqrt")
            degree = text[pos + 1:end]
            body, after = _read_group(text, end + 1)
            text = f"{text[:idx]}(({body})**(1/({degree}))){text[after:]}"
        else:
            body, after = _read_group(text, pos)
            text = f"{text[:idx]}sqrt({body}){text[after:]}"


def _rewrite_bigop(text: str) -> str:
    for cmd, func in ((r"\sum", "Sum"), (r"\prod", "Product")):
        while True:
            idx = text.find(cmd)
            if idx < 0:
                break
            pos = idx + len(cmd)
            sub = sup = None
            for _ in range(2):
                while pos < len(text) and text[pos].isspace():
                    pos += 1
                if pos < len(text) and text[pos] == "_" and sub is None:
                    sub, pos = _read_group(text, pos + 1)
                elif pos < len(text) and text[pos] == "^" and sup is None:
                    sup, pos = _read_group(text, pos + 1)
                else:
                    break
            body, after = _read_group(text, pos)
            if sub is None or sup is None:
                raise ParseError(f"{cmd} requires both a lower and an upper limit")
            # sub looks like "k=1" or just "k"
            if "=" in sub:
                var, lo = sub.split("=", 1)
                var, lo = var.strip(), lo.strip()
            else:
                var, lo = sub.strip(), "1"
            text = f"{text[:idx]}{func}({body}, ({var}, {lo}, {sup})){text[after:]}"
    return text


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #

_SORT_ALIASES = {
    "Z": "int", "R": "real", "N": "nat", "Q": "rat", "C": "real",
    "int": "int", "integer": "int", "integers": "int", "zz": "int",
    "real": "real", "reals": "real", "float": "real", "double": "real",
    "nat": "nat", "natural": "nat", "naturals": "nat",
    "rat": "rat", "rational": "rat", "rationals": "rat",
    "bool": "bool", "bool_": "bool",
    "uninterpreted": "uninterpreted", "u": "uninterpreted", "any": "uninterpreted",
}

_KNOWN_FUNCS = {
    "sqrt", "abs", "exp", "ln", "log", "sin", "cos", "tan", "cot", "sec", "csc",
    "asin", "acos", "atan", "sinh", "cosh", "tanh", "floor", "ceil", "sign",
    "min", "max", "gcd", "lcm", "mod", "factorial", "binomial", "Sum",
    "Product", "sum", "prod", "re", "im", "conjugate", "gamma", "erf",
    # divisibility / parity predicates keep their call syntax on purpose
    "even", "odd", "divisible", "divides", "Abs",
}


class _Parser:
    def __init__(self, tokens: Sequence[Token], known_funcs: frozenset[str] = frozenset()) -> None:
        self.toks = list(tokens)
        self.i = 0
        self.known_funcs = known_funcs

    # -- token helpers ----------------------------------------------------- #
    def peek(self, offset: int = 0) -> Token | None:
        j = self.i + offset
        return self.toks[j] if 0 <= j < len(self.toks) else None

    def next(self) -> Token:
        tok = self.peek()
        if tok is None:
            raise ParseError("unexpected end of input")
        self.i += 1
        return tok

    def at(self, *texts: str) -> bool:
        tok = self.peek()
        return tok is not None and tok.text in texts

    def eat(self, text: str) -> bool:
        if self.at(text):
            self.i += 1
            return True
        return False

    def expect(self, text: str) -> None:
        if not self.eat(text):
            tok = self.peek()
            found = "end of input" if tok is None else repr(tok.text)
            raise ParseError(f"expected {text!r} but found {found}")

    # -- grammar ----------------------------------------------------------- #
    def parse_formula(self) -> Node:
        return self.parse_iff()

    def parse_iff(self) -> Node:
        left = self.parse_imp()
        while self.at("<->", "<=>", "↔"):
            self.next()
            right = self.parse_imp()
            left = Iff(left, right)
        return left

    def parse_imp(self) -> Node:
        left = self.parse_or()
        if self.at("->", "=>", "==>", "-->", "→"):
            self.next()
            right = self.parse_imp()  # right associative
            return Imp(left, right)
        return left

    def parse_or(self) -> Node:
        parts = [self.parse_and()]
        # '|' is NOT disjunction: in mathematical writing it is either absolute
        # value (handled in parse_atom) or the divisibility relation (parse_cmp).
        while self.at("||", "∨") or self._kw("or"):
            self.next()
            parts.append(self.parse_and())
        return parts[0] if len(parts) == 1 else Or(tuple(parts))

    def parse_and(self) -> Node:
        parts = [self.parse_not()]
        # NOTE: '^' is deliberately NOT a conjunction here -- in mathematical
        # notation it means exponentiation, which parse_power already claims.
        while self.at("&&", "&", "∧") or self._kw("and"):
            self.next()
            parts.append(self.parse_not())
        return parts[0] if len(parts) == 1 else And(tuple(parts))

    def parse_not(self) -> Node:
        if self.at("~", "!", "¬") or self._kw("not", "¬"):
            self.next()
            return Not(self.parse_not())
        return self.parse_cmp()

    def parse_cmp(self) -> Node:
        left = self.parse_sum()
        rels = {"=": "eq", "==": "eq", "!=": "ne", "<": "lt", "<=": "le",
                ">": "gt", ">=": "ge", "≠": "ne", "≤": "le", "≥": "ge",
                "∣": "divides", "∤": "not_divides"}
        chain: list[tuple[str, Node]] = []
        while self.peek() is not None and self.peek().text in rels:  # type: ignore[union-attr]
            op = rels[self.next().text]
            chain.append((op, self.parse_sum()))
        if not chain:
            return left
        if len(chain) == 1:
            return Cmp(chain[0][0], left, chain[0][1])
        # chained relation a < b < c  ==>  a < b && b < c
        parts: list[Node] = []
        prev = left
        for op, rhs in chain:
            parts.append(Cmp(op, prev, rhs))
            prev = rhs
        return And(tuple(parts))

    def parse_sum(self) -> Node:
        node = self.parse_term()
        while self.peek() is not None and self.peek().text in ("+", "-"):  # type: ignore[union-attr]
            op = "add" if self.next().text == "+" else "sub"
            node = Bin(op, node, self.parse_term())
        return node

    def parse_term(self) -> Node:
        node = self.parse_unary()
        while True:
            tok = self.peek()
            if tok is None:
                break
            if tok.text in ("*", "/", "%", "cdot", "·", "×", "⋅"):
                raw = self.next().text
                op = {"*": "mul", "/": "div", "%": "mod", "cdot": "mul",
                      "·": "mul", "×": "mul", "⋅": "mul"}[raw]
                node = Bin(op, node, self.parse_unary())
            elif tok.kind == "id" and self._implicit_mul_ahead():
                node = Bin("mul", node, self.parse_unary())
            elif tok.text == "(":
                # juxtaposition with a bracketed group: (a+b)(c+d)
                node = Bin("mul", node, self.parse_unary())
            else:
                break
        return node

    def _implicit_mul_ahead(self) -> bool:
        """``2x`` / ``(x+1)(x-1)`` stay multiplication instead of application."""
        tok = self.peek()
        if tok is None or tok.kind != "id":
            return False
        if tok.text in ("in", "and", "or", "not", "mod", "forall", "exists"):
            return False
        prev = self.toks[self.i - 1] if self.i else None
        if prev is None:
            return False
        return prev.text in (")", "}", "]") or prev.kind in ("num", "id") or prev.text in (
            "!", "√", "π",
        )

    def parse_unary(self) -> Node:
        if self.at("-"):
            self.next()
            return Neg(self.parse_unary())
        if self.at("+"):
            self.next()
            return self.parse_unary()
        if self.at("±"):
            raise ParseError("'±' is not supported; split the statement into two cases")
        return self.parse_power()

    def parse_power(self) -> Node:
        base = self.parse_atom()
        if self.at("^", "**"):
            self.next()
            exponent = self.parse_unary()
            return Bin("pow", base, exponent)
        return base

    def parse_atom(self) -> Node:
        tok = self.peek()
        if tok is None:
            raise ParseError("unexpected end of input")

        if tok.text in ("forall", "exists", "∀", "∃"):
            return self.parse_quantifier()
        if tok.text == "(":
            self.next()
            inner = self.parse_formula()
            self.expect(")")
            return inner
        if tok.text == "{":
            self.next()
            inner = self.parse_formula()
            self.expect("}")
            return inner
        if tok.text in ("sqrt", "√"):
            self.next()
            return Sqrt(self._parse_delimited())
        if tok.text == "|":
            return self._parse_abs()
        if tok.kind == "num":
            self.next()
            return Num(tok.text)
        if tok.kind == "bb":
            self.next()
            return Sym(_SORT_ALIASES.get(tok.text, "real"))
        if tok.kind == "logicconst":
            self.next()
            return Sym("verum" if tok.text == "⊤" else "falsum")
        if tok.kind == "id":
            self.next()
            name = tok.text
            if self.at("("):
                return self._parse_name_parens(name)
            if self.at("["):
                args = self._parse_bracket_args()
                if name in ("Sum", "sum", "Product", "prod"):
                    return self._build_bigop(name, args)
                return App(name, tuple(args))
            if name == "in" or name == "∈":
                raise ParseError("unexpected 'in' outside a quantifier")
            return Sym(name)
        raise ParseError(f"unexpected token {tok.text!r} at position {tok.pos}")

    def _parse_name_parens(self, name: str) -> Node:
        """Resolve the ``name(...)`` ambiguity between application and juxtaposition.

        ``f(x)`` and ``n(n+1)`` are structurally identical in bare mathematical
        notation.  The rule applied here is:

        * a builtin / pre-declared function name is always application;
        * otherwise ``name(...)`` is application only when every argument is a
          simple atomic term -- which covers predicates such as ``P(x)``,
          ``Q(x, y)`` and ``isPrime(n)``;
        * anything else falls back to juxtaposition-as-multiplication, so
          ``n(n+1)`` and ``a(x-1)`` mean products.

        Compound arguments to a custom function therefore need the name declared
        (``functions=[...]``) or an explicit ``*``.
        """
        if name in _KNOWN_FUNCS or name in self.known_funcs:
            args = self._parse_args()
            if name in ("Sum", "sum", "Product", "prod"):
                return self._build_bigop(name, args)
            return App(name, tuple(args))
        save = self.i
        try:
            args = self._parse_args()
        except ParseError:
            self.i = save
            return Sym(name)
        if args and all(_is_simple_term(a) for a in args):
            if name in ("Sum", "sum", "Product", "prod"):
                return self._build_bigop(name, args)
            return App(name, tuple(args))
        self.i = save
        return Sym(name)

    def _parse_abs(self) -> Node:
        """Parse ``|expr|`` by scanning to the matching bar.

        ``|`` is ambiguous in math notation (absolute value vs. divisibility), so
        the body is collected token-wise instead of through the relation level,
        which would otherwise swallow the closing bar.
        """
        self.expect("|")
        depth = 0
        collected: list[Token] = []
        while True:
            tok = self.peek()
            if tok is None:
                raise ParseError("unbalanced '|'")
            if tok.text in ("(", "{", "["):
                depth += 1
            elif tok.text in (")", "}", "]"):
                if depth == 0:
                    raise ParseError("unbalanced '|' before a closing bracket")
                depth -= 1
            elif tok.text == "|" and depth == 0:
                self.next()
                break
            collected.append(self.next())
        if not collected:
            raise ParseError("empty |...| expression")
        sub = _Parser(collected, self.known_funcs)
        node = sub.parse_formula()
        if sub.peek() is not None:
            raise ParseError(
                f"unexpected input inside |...|: {sub.peek().text!r}"  # type: ignore[union-attr]
            )
        return Abs(node)

    def _parse_delimited(self) -> Node:
        if self.at("("):
            args = self._parse_args()
            return args[0] if len(args) == 1 else TupleN(tuple(args))
        if self.at("{"):
            self.next()
            inner = self.parse_formula()
            self.expect("}")
            return inner
        return self.parse_atom()

    def _parse_args(self) -> list[Node]:
        self.expect("(")
        args: list[Node] = []
        if self.eat(")"):
            return args
        while True:
            args.append(self._parse_arg_item())
            if self.eat(","):
                continue
            self.expect(")")
            return args

    def _parse_bracket_args(self) -> list[Node]:
        self.expect("[")
        args: list[Node] = []
        if self.eat("]"):
            return args
        while True:
            args.append(self._parse_arg_item())
            if self.eat(","):
                continue
            self.expect("]")
            return args

    def _parse_arg_item(self) -> Node:
        """An argument may itself be a tuple, as in ``Sum(k, (k, 1, n))``."""
        if self.at("("):
            save = self.i
            self.next()
            items = [self._parse_arg_item()]
            saw_comma = False
            while self.eat(","):
                saw_comma = True
                items.append(self._parse_arg_item())
            if self.eat(")") and saw_comma:
                return TupleN(tuple(items))
            self.i = save
        return self.parse_formula()

    def _build_bigop(self, name: str, args: list[Node]) -> Node:
        kind = "sum" if name.lower() == "sum" else "prod"
        if len(args) != 2 or not isinstance(args[1], TupleN) or len(args[1].items) != 3:
            raise ParseError(
                f"{name} expects (expression, (index, lower, upper))"
            )
        body, spec = args
        idx, lo, hi = spec.items
        if not isinstance(idx, Sym):
            raise ParseError("the summation index must be a single symbol")
        return BigOp(kind, body, idx.name, lo, hi)

    def parse_quantifier(self) -> Node:
        tok = self.next()
        kind = "forall" if tok.text in ("forall", "∀") else "exists"
        bindings: list[tuple[str, str]] = []
        while True:
            vtok = self.peek()
            if vtok is None or vtok.kind in ("num",):
                raise ParseError("expected a bound variable after the quantifier")
            if vtok.kind == "id" or vtok.kind == "bb":
                self.next()
                name = vtok.text
                sort = "uninterpreted"
                if self.at("in", "∈"):
                    self.next()
                    sort = self._parse_sort()
                elif self.at(":"):
                    # 'x : Z' style
                    save = self.i
                    self.next()
                    nxt = self.peek()
                    if nxt is not None and (nxt.kind == "bb" or (nxt.kind == "id" and nxt.text in _SORT_ALIASES)):
                        sort = self._parse_sort()
                    else:
                        self.i = save
                bindings.append((name, sort))
                if self.at(":", ".", ","):
                    sep = self.next().text
                    if sep in (":", "."):
                        break
                    continue
                raise ParseError("expected ':' or '.' after the bound variable")
            raise ParseError(f"unexpected token {vtok.text!r} in quantifier binding")
        if not bindings:
            raise ParseError("quantifier binds no variable")
        body = self.parse_formula()
        for name, sort in reversed(bindings):
            body = Quant(kind, name, sort, body)
        return body

    def _parse_sort(self) -> str:
        tok = self.peek()
        if tok is None:
            raise ParseError("expected a set after 'in'")
        self.next()
        if tok.kind == "bb":
            return _SORT_ALIASES.get(tok.text, "real")
        if tok.kind == "id":
            key = tok.text
            if key in _SORT_ALIASES:
                return _SORT_ALIASES[key]
            return "uninterpreted"
        raise ParseError(f"unknown set {tok.text!r}")

    def _kw(self, *words: str) -> bool:
        tok = self.peek()
        return tok is not None and tok.kind == "id" and tok.text in words


def _is_simple_term(node: Node) -> bool:
    """Atomic enough that ``name(arg)`` reads as an application, not a product."""
    if isinstance(node, (Sym, Num, App, BigOp)):
        return True
    if isinstance(node, TupleN):
        return all(_is_simple_term(item) for item in node.items)
    return False


def parse(source: str, functions: Iterable[str] = ()) -> Node:
    """Parse a formula or expression string into the AST.

    ``functions`` pre-declares custom function/predicate names so that
    ``f(x + 1)`` is read as an application rather than a product.
    """
    if source is None or not str(source).strip():
        raise ParseError("empty expression")
    known = frozenset(str(f) for f in functions)
    parser = _Parser(tokenize(str(source)), known)
    node = parser.parse_formula()
    leftover = parser.peek()
    if leftover is not None:
        raise ParseError(
            f"unexpected trailing input {leftover.text!r} at position {leftover.pos}"
        )
    return node


# --------------------------------------------------------------------------- #
# Printers
# --------------------------------------------------------------------------- #

_BIN_TEXT = {"add": "+", "sub": "-", "mul": "*", "div": "/", "mod": "%", "pow": "**"}
_CMP_TEXT = {"eq": "=", "ne": "!=", "lt": "<", "le": "<=", "gt": ">", "ge": ">=",
             "divides": "|", "not_divides": "∤"}
_CMP_LATEX = {"eq": "=", "ne": r"\neq", "lt": "<", "le": r"\leq", "gt": ">",
              "ge": r"\geq", "divides": r"\mid", "not_divides": r"\nmid"}
_PREC = {"iff": 1, "imp": 2, "or": 3, "and": 4, "not": 5, "cmp": 6,
         "add": 7, "sub": 7, "mul": 8, "div": 8, "mod": 8, "pow": 9, "atom": 10}


def _node_prec(node: Node) -> int:
    if isinstance(node, Iff):
        return _PREC["iff"]
    if isinstance(node, Imp):
        return _PREC["imp"]
    if isinstance(node, Or):
        return _PREC["or"]
    if isinstance(node, And):
        return _PREC["and"]
    if isinstance(node, (Not, Quant)):
        return _PREC["not"]
    if isinstance(node, Cmp):
        return _PREC["cmp"]
    if isinstance(node, Bin):
        return _PREC[node.op]
    if isinstance(node, Neg):
        return _PREC["add"]
    return _PREC["atom"]


def format_node(node: Node, style: str = "text") -> str:
    """Render an AST node back to text or LaTeX (fully parenthesised)."""
    latex = style == "latex"
    if isinstance(node, Num):
        return node.value
    if isinstance(node, Sym):
        return node.name
    if isinstance(node, App):
        inner = ", ".join(format_node(a, style) for a in node.args)
        return f"{node.name}({inner})"
    if isinstance(node, TupleN):
        return "(" + ", ".join(format_node(a, style) for a in node.items) + ")"
    if isinstance(node, Neg):
        return f"-{_wrap(node.operand, _PREC['mul'], style)}"
    if isinstance(node, Sqrt):
        return (r"\sqrt{" + format_node(node.operand, style) + "}" if latex
                else f"sqrt({format_node(node.operand, style)})")
    if isinstance(node, Abs):
        inner = format_node(node.operand, style)
        return (r"\left|" + inner + r"\right|" if latex else f"|{inner}|")
    if isinstance(node, Bin):
        if node.op == "div" and latex:
            return (r"\frac{" + format_node(node.left, style) + "}{"
                    + format_node(node.right, style) + "}")
        if node.op == "pow" and latex:
            return (f"{_wrap(node.left, _PREC['atom'], style)}^"
                    f"{{{format_node(node.right, style)}}}")
        left = _wrap(node.left, _PREC[node.op], style)
        right = _wrap(node.right, _PREC[node.op] + (0 if node.op == "pow" else 1), style)
        sep = _BIN_TEXT[node.op]
        if latex:
            sep = {"mul": r" \cdot ", "mod": r" \bmod ", "pow": "^"}[node.op] \
                if node.op in ("mul", "mod") else sep
        return f"{left}{sep}{right}"
    if isinstance(node, Cmp):
        table = _CMP_LATEX if latex else _CMP_TEXT
        return (f"{_wrap(node.left, _PREC['cmp'], style)} {table[node.op]} "
                f"{_wrap(node.right, _PREC['cmp'] + 1, style)}")
    if isinstance(node, Not):
        operand = node.operand
        inner = format_node(operand, style)
        if not isinstance(operand, (Sym, Num, App, Not, Sqrt, Abs, BigOp)):
            inner = f"({inner})"
        return (r"\lnot " + inner) if latex else ("~" + inner)
    if isinstance(node, And):
        sep = r" \land " if latex else " & "
        return sep.join(_wrap(p, _PREC["and"], style) for p in node.parts)
    if isinstance(node, Or):
        sep = r" \lor " if latex else " | "
        return sep.join(_wrap(p, _PREC["or"], style) for p in node.parts)
    if isinstance(node, Imp):
        arrow = r"\to" if latex else "->"
        return (f"{_wrap(node.left, _PREC['imp'] + 1, style)} {arrow} "
                f"{_wrap(node.right, _PREC['imp'], style)}")
    if isinstance(node, Iff):
        arrow = r"\leftrightarrow" if latex else "<->"
        return (f"{_wrap(node.left, _PREC['iff'] + 1, style)} {arrow} "
                f"{_wrap(node.right, _PREC['iff'] + 1, style)}")
    if isinstance(node, Quant):
        sym = {"forall": (r"\forall", "forall"), "exists": (r"\exists", "exists")}[node.kind]
        setname = {"int": r"\mathbb{Z}", "nat": r"\mathbb{N}", "real": r"\mathbb{R}",
                   "rat": r"\mathbb{Q}", "bool": r"\mathrm{Bool}",
                   "uninterpreted": r"\mathcal{U}"}[node.sort]
        body = format_node(node.body, style)
        if latex:
            return f"{sym[0]} {node.var} \\in {setname} : {body}"
        return f"{sym[1]} {node.var}:{node.sort}. {body}"
    if isinstance(node, BigOp):
        sym = (r"\sum", "Sum") if node.kind == "sum" else (r"\prod", "Product")
        if latex:
            return (f"{sym[0]}_{{{node.var}={format_node(node.lo, style)}}}"
                    f"^{{{format_node(node.hi, style)}}} "
                    f"{_wrap(node.expr, _PREC['mul'], style)}")
        return (f"{sym[1]}({format_node(node.expr, style)}, "
                f"({node.var}, {format_node(node.lo, style)}, "
                f"{format_node(node.hi, style)}))")
    raise TypeError(f"cannot format node of type {type(node).__name__}")


def _wrap(node: Node, min_prec: int, style: str) -> str:
    rendered = format_node(node, style)
    return f"({rendered})" if _node_prec(node) < min_prec else rendered


# --------------------------------------------------------------------------- #
# Small helpers shared by the analysis modules
# --------------------------------------------------------------------------- #


def walk(node: Node) -> Iterable[Node]:
    """Depth-first traversal over every sub-node."""
    yield node
    for child in children_of(node):
        yield from walk(child)


def children_of(node: Node) -> list[Node]:
    if isinstance(node, (App,)):
        return list(node.args)
    if isinstance(node, TupleN):
        return list(node.items)
    if isinstance(node, (Neg, Not, Sqrt, Abs)):
        return [node.operand]
    if isinstance(node, (Bin, Cmp, Imp, Iff)):
        return [node.left, node.right]
    if isinstance(node, (And, Or)):
        return list(node.parts)
    if isinstance(node, Quant):
        return [node.body]
    if isinstance(node, BigOp):
        return [node.expr, node.lo, node.hi]
    return []


def collect_symbols(node: Node) -> set[str]:
    """Free symbol names appearing in the node (bound variables excluded)."""
    bound: set[str] = set()
    out: set[str] = set()

    def visit(n: Node, scope: set[str]) -> None:
        if isinstance(n, Sym):
            if n.name not in scope:
                out.add(n.name)
        elif isinstance(n, Quant):
            visit(n.body, scope | {n.var})
        elif isinstance(n, BigOp):
            visit(n.expr, scope | {n.var})
            visit(n.lo, scope)
            visit(n.hi, scope)
        else:
            for child in children_of(n):
                visit(child, scope)

    visit(node, bound)
    return out


def has_quantifier(node: Node) -> bool:
    return any(isinstance(n, Quant) for n in walk(node))


def has_arithmetic(node: Node) -> bool:
    return any(isinstance(n, (Bin, Neg, Sqrt, BigOp)) for n in walk(node))


def has_modular(node: Node) -> bool:
    return any(isinstance(n, (Bin, App)) and (
        (isinstance(n, Bin) and n.op == "mod") or (isinstance(n, App) and n.name in ("mod",))
    ) for n in walk(node))


def collect_sorts(node: Node) -> dict[str, str]:
    """Explicit ``x in Z`` annotations found in quantifiers."""
    out: dict[str, str] = {}
    for n in walk(node):
        if isinstance(n, Quant):
            out.setdefault(n.var, n.sort)
    return out


def used_sorts(node: Node) -> set[str]:
    return {n.sort for n in walk(node) if isinstance(n, Quant)}
