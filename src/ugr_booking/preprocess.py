"""Turn raw e-mail bodies into readable text without interpreting business meaning."""
from __future__ import annotations
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
import re

_HTML_HINT = re.compile(r"<\s*/?\s*(html|body|div|p|br|span|b|i|table|tr|td|font)\b", re.IGNORECASE)
_BLOCK_TAGS = {"br", "div", "p", "tr", "li", "table", "body", "html", "h1", "h2", "h3"}
_SKIP_TAGS = {"script", "style", "head"}
_LEADING_BREAK = re.compile(r"^[ \t]*\r?\n")

# Quoted history markers seen in operational mail clients. Only explicit
# separators are recognised; ordinary sentences such as "Original booking was"
# must stay in the current message.
_QUOTE_START = re.compile(
    r"(?im)^[ \t]*-{2,}[ \t]*(previous message|original message|forwarded message)[ \t]*-{2,}[ \t]*$"
)
_QUOTE_END = re.compile(
    r"(?im)^[ \t]*-{2,}[ \t]*end (previous message|original message|forwarded message)[ \t]*-{2,}[ \t]*$"
)

# Replacement characters and typical UTF-8-read-as-Latin-1 artefacts.
SUSPECT_ENCODING_CHARS = ("�", "Ã", "Â")


class _TextExtractor(HTMLParser):
    """Lenient HTML-to-text; unclosed tags must not raise."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0
        self._after_break = False

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")
            # A source newline right after <br>/<div> is the same line break.
            self._after_break = True

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")
            self._after_break = True

    def handle_data(self, data):
        if self._after_break:
            data = _LEADING_BREAK.sub("", data, count=1)
            self._after_break = False
        if not self._skip_depth:
            self.parts.append(data)


def looks_like_html(text: str) -> bool:
    return bool(_HTML_HINT.search(text))


def html_to_text(text: str) -> str:
    parser = _TextExtractor()
    parser.feed(text)
    parser.close()
    return unescape("".join(parser.parts))


def normalize_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    lines = [line.rstrip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


@dataclass(frozen=True)
class CleanBody:
    current_text: str
    quoted_text: str
    was_html: bool


def split_quoted(text: str) -> tuple[str, str]:
    """Separate explicitly quoted history from the current instruction.

    Text after an explicit end marker belongs to the current message again.
    A start marker without an end marker quotes everything after it.
    """
    current: list[str] = []
    quoted: list[str] = []
    position = 0
    while True:
        start = _QUOTE_START.search(text, position)
        if start is None:
            current.append(text[position:])
            break
        current.append(text[position:start.start()])
        end = _QUOTE_END.search(text, start.end())
        if end is None:
            quoted.append(text[start.end():])
            break
        quoted.append(text[start.end():end.start()])
        position = end.end()
    return normalize_whitespace("\n".join(current)), normalize_whitespace("\n\n".join(quoted))


def clean_body(raw_body: str) -> CleanBody:
    was_html = looks_like_html(raw_body)
    text = html_to_text(raw_body) if was_html else raw_body
    current, quoted = split_quoted(normalize_whitespace(text))
    return CleanBody(current, quoted, was_html)


def has_suspect_encoding(text: str) -> bool:
    return any(marker in text for marker in SUSPECT_ENCODING_CHARS)
