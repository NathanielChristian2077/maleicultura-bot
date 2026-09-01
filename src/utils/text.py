import re


# Regex
# Remove markdowns do WhatsApp
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
_MD_GARBAGE_RE = re.compile(r"[`*_~#>]")


def clean_reply_text(text: str) -> str:
    text = _MD_LINK_RE.sub(r"\1", text or "")
    text = _MD_GARBAGE_RE.sub("", text).strip()
    return text
