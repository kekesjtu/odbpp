"""Text-level readers for the two syntaxes the ODB++ spec defines.

The spec splits its files into two families (p.31-33):

Line Record Text
    One record per line, first token identifies the record type.  Used by
    ``features``, ``profile``, ``tools``, ``components``, ``netlist``,
    ``eda/data``, ``wheels/dcodes`` and the font files.

Structured Text
    Either ``KEY=VALUE`` lines or ``KEYWORD { ... }`` blocks.  Field order is
    not significant.  Used by ``matrix/matrix``, ``stephdr``, ``misc/info``,
    ``misc/attrlist``, ``sysattr.*``, ``userattr``, layer ``attrlist`` and
    ``tools``-adjacent metadata.

Everything here is deliberately mechanical: no format knowledge beyond quoting,
comments and brace nesting.  Semantic validation belongs in the parsers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator, Optional, Sequence

from .errors import ParseError

__all__ = [
    "Token",
    "tokenize",
    "split_sections",
    "iter_content_lines",
    "unquote",
    "StructuredBlock",
    "StructuredText",
    "parse_value",
]

_WS = re.compile(r"[ \t]+")

#: A line that is just an identifier, e.g. a block keyword whose brace is on the
#: following line (``BOOLEAN`` / ``{`` as used by ``misc/sysattr*``).
_BARE_TOKEN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")


# ---------------------------------------------------------------------------
# line filtering
# ---------------------------------------------------------------------------

def iter_content_lines(text: str) -> Iterator[tuple[int, str]]:
    """Yield ``(line_number, stripped_line)`` for meaningful lines only.

    Skips blanks and comment lines.  A ``#`` only starts a comment at the start
    of a line: inside a text record it is ordinary data (spec examples contain
    strings such as ``'ROT#30'``).

    ``\\r`` is stripped so that files authored on Windows parse identically.
    """
    for n, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        yield n, line


# ---------------------------------------------------------------------------
# tokenising
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Token:
    """One whitespace-delimited token.

    ``text`` has surrounding single quotes removed; ``quoted`` records whether
    they were present, which matters for fields like ``T``'s text argument where
    an unquoted value would be a syntax error.
    """

    text: str
    quoted: bool
    column: int

    def __str__(self) -> str:  # pragma: no cover - debugging aid
        return f"'{self.text}'" if self.quoted else self.text


def tokenize(text: str, *, path: Optional[str] = None, line_no: Optional[int] = None) -> list[Token]:
    """Split *text* on whitespace, honouring single-quoted spans.

    ODB++ does not define an escape sequence for a quote inside a quoted string,
    so a quote simply ends the span.
    """
    tokens: list[Token] = []
    i = 0
    n = len(text)

    while i < n:
        ch = text[i]
        if ch in " \t":
            i += 1
            continue

        start = i
        if ch == "'":
            end = text.find("'", i + 1)
            if end == -1:
                raise ParseError(
                    "unterminated quoted string",
                    path=path,
                    line_no=line_no,
                    line=text,
                )
            tokens.append(Token(text[i + 1:end], True, start))
            i = end + 1
            # A quote must be followed by whitespace or end of line.
            if i < n and text[i] not in " \t":
                raise ParseError(
                    "quoted string must be followed by whitespace",
                    path=path,
                    line_no=line_no,
                    line=text,
                )
            continue

        end = i
        while end < n and text[end] not in " \t":
            end += 1
        tokens.append(Token(text[start:end], False, start))
        i = end

    return tokens


def unquote(text: str) -> str:
    """Strip one layer of surrounding single quotes, if present."""
    if len(text) >= 2 and text[0] == "'" and text[-1] == "'":
        return text[1:-1]
    return text


def split_sections(line: str) -> list[str]:
    """Split a line-record on ``;`` while respecting quoted spans.

    The trailing part of an ODB++ record is
    ``;<atr>=<value>,...;ID=<id>`` where some of the sub-fields are optional, so
    the split must be quote-aware: ``T ... 'a;b' 1`` is a single section.
    """
    sections: list[str] = []
    buf: list[str] = []
    in_quote = False
    for ch in line:
        if ch == "'":
            in_quote = not in_quote
            buf.append(ch)
        elif ch == ";" and not in_quote:
            sections.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    sections.append("".join(buf))
    return sections


# ---------------------------------------------------------------------------
# value parsing
# ---------------------------------------------------------------------------

_INT_RE = re.compile(r"^[+-]?\d+$")
_FLOAT_RE = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)$")


def parse_value(token: str) -> object:
    """Parse a scalar the way ODB++ writes them (plain decimal, never exponent).

    Spec p.34 forbids scientific notation, so rejecting it here is a feature:
    a file using it is out of spec and we want to know.
    """
    t = token.strip()
    if not t:
        return ""
    if _INT_RE.match(t):
        return int(t)
    if _FLOAT_RE.match(t):
        return float(t)
    return t


# ---------------------------------------------------------------------------
# structured text
# ---------------------------------------------------------------------------

@dataclass
class StructuredBlock:
    """A ``KEYWORD { ... }`` block, possibly containing nested blocks.

    ``fields`` preserves the **original key case**, because attribute names in an
    ``attrlist`` are case sensitive (``.technology`` and ``.TECHNOLOGY`` are
    different attributes).  Lookups through :meth:`get` are case insensitive,
    which is what the fixed-vocabulary files (``misc/info``, ``stephdr``, the
    matrix blocks) need.

    ``keyword`` is always upper case; keywords are a fixed vocabulary, unlike
    field names.
    """

    keyword: str
    fields: dict[str, str] = field(default_factory=dict)
    blocks: list["StructuredBlock"] = field(default_factory=list)
    line_no: int = 0
    #: upper-cased key -> original key, for case-insensitive lookup
    _folded: dict[str, str] = field(default_factory=dict, repr=False)

    def set(self, key: str, value: str) -> None:
        """Set a field, remembering its case for later lookups."""
        self.fields[key] = value
        self._folded.setdefault(key.upper(), key)

    def __contains__(self, key: str) -> bool:
        return key.upper() in self._folded

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        actual = self._folded.get(key.upper())
        if actual is None:
            return default
        return self.fields.get(actual, default)

    def blocks_of(self, keyword: str) -> list["StructuredBlock"]:
        kw = keyword.upper()
        return [b for b in self.blocks if b.keyword == kw]

    # -- typed field accessors (same defaults as StructuredText) ------------

    def int_field(self, key: str, default: Optional[int] = None) -> Optional[int]:
        raw = self.get(key)
        if raw is None or raw == "":
            return default
        try:
            return int(float(raw))
        except ValueError:
            raise ParseError(f"field {key} is not an integer: {raw!r}") from None

    def float_field(self, key: str, default: Optional[float] = None) -> Optional[float]:
        raw = self.get(key)
        if raw is None or raw == "":
            return default
        try:
            return float(raw)
        except ValueError:
            raise ParseError(f"field {key} is not a number: {raw!r}") from None

    def str_field(self, key: str, default: str = "") -> str:
        value = self.get(key)
        return default if value is None else value


@dataclass
class StructuredText:
    """Parsed ``KEY=VALUE`` / ``BLOCK { }`` document, order preserving.

    The document body is the implicit root block; ``fields`` and ``blocks``
    proxy to it so that lookups obey the same case rules as any nested block.
    """

    root: StructuredBlock = field(default_factory=lambda: StructuredBlock(keyword=""))
    path: Optional[str] = None
    #: Lines that were neither a field, a block, a bare keyword, nor a brace.
    stray: list[tuple[int, str]] = field(default_factory=list)

    @property
    def fields(self) -> dict[str, str]:
        return self.root.fields

    @property
    def blocks(self) -> list[StructuredBlock]:
        return self.root.blocks

    @classmethod
    def parse(cls, text: str, *, path: Optional[str] = None) -> "StructuredText":
        doc = cls(path=path)
        stack: list[StructuredBlock] = [doc.root]

        # Some tools put the opening brace on its own line:
        #
        #     BOOLEAN
        #     {
        #       NAME = .string_mirrored
        #     }
        #
        # so a bare token line is remembered until the brace (or the next real
        # statement) decides whether it was a keyword.
        pending_keyword: Optional[tuple[str, int]] = None

        for line_no, line in iter_content_lines(text):
            if line == "{":
                if pending_keyword is None:
                    raise ParseError("block without a keyword",
                                     path=path, line_no=line_no, line=line)
                keyword, keyword_line = pending_keyword
                pending_keyword = None
                block = StructuredBlock(keyword=keyword.upper(), line_no=keyword_line)
                stack[-1].blocks.append(block)
                stack.append(block)
                continue

            if line == "}":
                if len(stack) == 1:
                    raise ParseError("unmatched '}'", path=path, line_no=line_no, line=line)
                stack.pop()
                continue

            if line.endswith("{"):
                keyword = line[:-1].strip()
                if not keyword:
                    raise ParseError("block without a keyword",
                                     path=path, line_no=line_no, line=line)
                pending_keyword = None
                block = StructuredBlock(keyword=keyword.upper(), line_no=line_no)
                stack[-1].blocks.append(block)
                stack.append(block)
                continue

            if "=" in line:
                pending_keyword = None
                key, _, value = line.partition("=")
                key = key.strip()
                if not key:
                    raise ParseError("field without a name",
                                     path=path, line_no=line_no, line=line)
                # Later duplicates win, matching how CAM tools overwrite fields.
                stack[-1].set(key, value.strip())
                continue

            if _BARE_TOKEN.match(line):
                pending_keyword = (line, line_no)
                continue

            doc.stray.append((line_no, line))

        if len(stack) != 1:
            open_kw = ", ".join(b.keyword or "<root>" for b in stack[1:])
            raise ParseError(
                f"unterminated block(s): {open_kw}",
                path=path,
                line_no=stack[-1].line_no,
            )

        return doc

    # ------------------------------------------------------------------ api

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        return self.root.get(key, default)

    def blocks_of(self, keyword: str) -> list[StructuredBlock]:
        return self.root.blocks_of(keyword)

    def require(self, key: str, *, path: Optional[str] = None) -> str:
        value = self.root.get(key)
        if value is None:
            raise ParseError(f"missing required field {key.upper()}", path=path or self.path)
        return value

    def int_field(self, key: str, default: Optional[int] = None) -> Optional[int]:
        raw = self.root.get(key)
        if raw is None or raw == "":
            return default
        try:
            return int(float(raw))
        except ValueError:
            raise ParseError(
                f"field {key} is not an integer: {raw!r}", path=self.path
            ) from None

    def float_field(self, key: str, default: Optional[float] = None) -> Optional[float]:
        raw = self.root.get(key)
        if raw is None or raw == "":
            return default
        try:
            return float(raw)
        except ValueError:
            raise ParseError(
                f"field {key} is not a number: {raw!r}", path=self.path
            ) from None

    def str_field(self, key: str, default: str = "") -> str:
        value = self.root.get(key)
        return default if value is None else value


def join_path(*parts: str) -> str:
    """Join archive-relative path fragments with ``/`` and collapse duplicates."""
    cleaned: Sequence[str] = [p.strip("/\\") for p in parts if p not in ("", None)]
    return "/".join(p.replace("\\", "/") for p in cleaned if p)
