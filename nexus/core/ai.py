"""Change summaries.

* summarize_offline(): rule-based, always available, nothing leaves the machine.
* summarize_with_claude(): OPTIONAL. Only runs when the user explicitly asks for
  it, the `anthropic` package is installed and an API key/credential is set.
  It sends the difference text (not whole files) to the Anthropic API.
"""
from __future__ import annotations

from collections import Counter

MODEL = "claude-opus-5-5"


def summarize_offline(kind: str, stats: dict, details: list[str] | None = None) -> str:
    lines = []
    if kind == "text":
        if not stats.get("hunks"):
            return "The files are identical (with the current ignore options)."
        lines.append(f"{stats['hunks']} change block(s): {stats.get('changed', 0)} line(s) modified, "
                     f"{stats.get('left', 0)} removed, {stats.get('right', 0)} added.")
    elif kind == "table":
        lines.append(f"{stats.get('changed', 0)} row(s) modified ({stats.get('cells', 0)} cell(s)), "
                     f"{stats.get('left', 0)} row(s) removed, {stats.get('right', 0)} added.")
    elif kind == "vdi":
        lines.append(f"{stats.get('records added', 0)} record(s) added, {stats.get('records removed', 0)} removed, "
                     f"{stats.get('values changed', 0)} value change(s); {stats.get('connection changes', 0)} affect "
                     f"connection designations and {stats.get('geometry changes', 0)} geometry.")
        if stats.get("format only"):
            lines.append(f"{stats['format only']} difference(s) are notation only (e.g. 400 vs 400,0) and do not change data.")
    elif kind == "xml":
        lines.append(", ".join(f"{v} {k}" for k, v in stats.items()) or "No structural differences.")
    else:
        lines.append(", ".join(f"{v} {k}" for k, v in stats.items()))
    if details:
        common = Counter(details).most_common(5)
        lines.append("Most affected: " + ", ".join(f"{k} ({v})" for k, v in common))
    return " ".join(lines)


def claude_available() -> bool:
    try:
        import anthropic  # noqa: F401
        return True
    except ImportError:
        return False


def summarize_with_claude(diff_text: str, context: str = "", max_chars: int = 400_000) -> str:
    """Ask Claude for a plain-language change summary and impact assessment.
    Raises RuntimeError with a readable message on any problem."""
    try:
        import anthropic
    except ImportError as ex:
        raise RuntimeError("The optional 'anthropic' package is not installed (pip install anthropic).") from ex
    if len(diff_text) > max_chars:
        raise RuntimeError(f"The difference text is {len(diff_text):,} characters, more than the {max_chars:,} "
                           "allowed for one request. Narrow the comparison (e.g. only differences) and try again.")
    client = anthropic.Anthropic()
    prompt = ("You review differences between two versions of an engineering or office file. "
              "Summarise what changed in plain language for an engineer: group related changes, call out changes "
              "to values with units, connections, geometry references, formulas or structure, and say which changes "
              "look like notation only. Finish with a short 'Possible impact' list. Be concise.\n\n"
              f"Context: {context}\n\n<differences>\n{diff_text}\n</differences>")
    try:
        resp = client.messages.create(model=MODEL, max_tokens=4000, output_config={"effort": "medium"},
                                      messages=[{"role": "user", "content": prompt}])
    except anthropic.AuthenticationError as ex:
        raise RuntimeError("No valid Anthropic API key. Set ANTHROPIC_API_KEY or add a key in Settings.") from ex
    except anthropic.RateLimitError as ex:
        raise RuntimeError("Rate limited by the Anthropic API. Try again shortly.") from ex
    except anthropic.APIConnectionError as ex:
        raise RuntimeError("No connection to the Anthropic API (offline?).") from ex
    except anthropic.APIStatusError as ex:
        raise RuntimeError(f"Anthropic API error {ex.status_code}: {ex.message}") from ex
    if resp.stop_reason == "refusal":
        raise RuntimeError("The request was declined by the model.")
    return "".join(b.text for b in resp.content if b.type == "text").strip()
