"""Reading and writing text files with encoding and line-ending detection."""
from __future__ import annotations

import codecs
from dataclasses import dataclass

BOMS = [
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
]


@dataclass
class TextFile:
    text: str
    encoding: str
    eol: str  # "\r\n", "\n" or "\r"


def detect_encoding(data: bytes) -> str:
    for bom, name in BOMS:
        if data.startswith(bom):
            return name
    try:
        data.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        import chardet  # optional

        guess = chardet.detect(data[:200_000])
        enc = guess.get("encoding")
        if enc and guess.get("confidence", 0) > 0.6:
            codecs.lookup(enc)
            # Western European files are by far the most common non-UTF-8 case
            if enc.lower() in ("iso-8859-1", "ascii"):
                return "cp1252"
            return enc
    except Exception:
        pass
    return "cp1252"


def detect_eol(text: str) -> str:
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    cr = text.count("\r") - crlf
    if crlf >= lf and crlf >= cr and crlf > 0:
        return "\r\n"
    if cr > lf:
        return "\r"
    return "\n"


def is_binary(data: bytes) -> bool:
    head = data[:8000]
    if any(head.startswith(b) for b, _ in BOMS):
        return False
    return b"\x00" in head


def read_text(path: str, encoding: str | None = None) -> TextFile:
    with open(path, "rb") as fh:
        data = fh.read()
    enc = encoding or detect_encoding(data)
    text = data.decode(enc, errors="replace")
    eol = detect_eol(text)
    # normalise to \n internally
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return TextFile(text, enc, eol)


def write_text(path: str, text: str, encoding: str = "utf-8", eol: str = "\n") -> None:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if eol != "\n":
        text = text.replace("\n", eol)
    with open(path, "wb") as fh:
        fh.write(text.encode(encoding, errors="replace"))


EOL_NAMES = {"\r\n": "Windows (CR LF)", "\n": "Unix (LF)", "\r": "Mac (CR)"}
