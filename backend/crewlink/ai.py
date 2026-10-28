"""AI draft generation for a callout.

Design points (from DESIGN.md):
- Output is always a DRAFT. It never sends on its own — a human must approve the
  announcement (draft -> approved) before it can be sent.
- The provider is called with a timeout. If it is slow, down, or returns junk, we
  fall back to a basic local draft instead of blocking the leader. The feature
  degrades; it never becomes a gate that generated text slips through.
"""
import logging

from django.conf import settings
from openai import APIStatusError, OpenAI

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You turn a union leader's messy note into a clear callout for members. "
    "Return STRICT JSON with keys: title, body, push_preview. "
    "title: a short clear subject. "
    "body: 1-3 short sentences, plain and calm but keep any urgency. "
    "push_preview: <= 120 characters, suitable for a phone lock screen. "
    "Do NOT invent facts (times, places, names) that are not in the note."
)


def generate_draft(raw_text: str) -> dict:
    raw_text = (raw_text or "").strip()
    if not raw_text:
        return {
            "title": "",
            "body": "",
            "push_preview": "",
            "status": "draft",
            "source": "empty",
        }

    if not settings.OPENAI_API_KEY:
        return _fallback(raw_text, reason="no_api_key")

    try:
        client = OpenAI(
            api_key=settings.OPENAI_API_KEY,
            timeout=settings.AI_TIMEOUT_SECONDS,
            max_retries=0,
        )
        resp = client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": raw_text},
            ],
            response_format={"type": "json_object"},
            timeout=settings.AI_TIMEOUT_SECONDS,
        )
        import json

        data = json.loads(resp.choices[0].message.content)
        title = (data.get("title") or "").strip()
        body = (data.get("body") or "").strip()
        preview = (data.get("push_preview") or "").strip()[:120]
        if not title or not body:
            return _fallback(raw_text, reason="incomplete_output")
        return {
            "title": title,
            "body": body,
            "push_preview": preview,
            "status": "draft",
            "source": "openai",
        }
    except APIStatusError as exc:
        if exc.code == "credit_balance_exhausted":
            reason = "credits_exhausted"
        elif exc.code == "insufficient_quota" or exc.type == "insufficient_quota":
            reason = "quota_exhausted"
        elif exc.status_code == 429:
            reason = "rate_limited"
        else:
            reason = "provider_unavailable"
        logger.warning(
            "AI request failed (status=%s, code=%s), using fallback: %s",
            exc.status_code,
            exc.code,
            reason,
        )
        return _fallback(raw_text, reason=reason)
    except Exception as exc:  # timeout, network, bad JSON, etc.
        logger.warning("AI provider unavailable, using fallback (%s)", type(exc).__name__)
        return _fallback(raw_text, reason="provider_unavailable")


def _fallback(raw_text: str, reason: str) -> dict:
    """Degrade gracefully: a basic draft the leader can edit, never a blocked send."""
    first_line = raw_text.splitlines()[0].strip() if raw_text else ""
    title = (first_line[:80] or "Announcement").strip()
    preview = raw_text if len(raw_text) <= 120 else raw_text[:117].rstrip() + "..."
    warning = {
        "credits_exhausted": (
            "OpenAI API credits are exhausted. Ask your administrator to add API credits."
        ),
        "quota_exhausted": (
            "OpenAI API billing or usage limit reached. "
            "Ask your administrator to check API credits and limits."
        ),
        "rate_limited": "AI request limit reached. Please try again shortly.",
        "no_api_key": "AI drafting is not configured. Ask your administrator to configure it.",
    }.get(reason, "AI provider unavailable.")
    return {
        "title": title[:1].upper() + title[1:] if title else "Announcement",
        "body": raw_text,
        "push_preview": preview[:120],
        "status": "draft",
        "source": f"fallback:{reason}",
        "warning": f"{warning} Returned a basic draft. Review and edit before approving.",
    }
