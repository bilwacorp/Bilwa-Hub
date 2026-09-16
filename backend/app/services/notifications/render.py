"""Jinja2 rendering for notification templates. Autoescape is always on for
email — template context values (a ticket subject, a submitter name, a free-
text request message) can come from user/deployment input, so this is what
stops them turning into stored XSS in a rendered HTML email.

Unlike PoultryPro-CBF's version of this module, there's no DB-editable
template override layer here (no EmailTemplate/WhatsAppTemplate tables) —
templates are file-based only. Adding a new one is a new pair of files
(templates/<key>.html, templates_whatsapp/<key>.txt), not an admin-UI edit."""

import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, TemplateNotFound, select_autoescape

from app.services.notifications.exceptions import TemplateNotFoundException

TEMPLATES_DIR = Path(__file__).parent / "templates"
WHATSAPP_TEMPLATES_DIR = Path(__file__).parent / "templates_whatsapp"

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)

# Plain text, not HTML — no autoescape (same reasoning as _subject_env
# below). trim_blocks/lstrip_blocks let each {% if %}/{% endif %} live on
# its own line in the .txt source without leaving a blank line behind when
# the condition is false.
_whatsapp_env = Environment(
    loader=FileSystemLoader(str(WHATSAPP_TEMPLATES_DIR)),
    autoescape=False,
    trim_blocks=True,
    lstrip_blocks=True,
)

# Subject lines are a plain-text mail header, not HTML — autoescaping would
# turn "Farmer & Sons" into "Farmer &amp; Sons" in the inbox.
_subject_env = Environment(autoescape=False)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")


def _html_to_text(html: str) -> str:
    """Best-effort plain-text fallback for the SMTP alternative part — not a
    full HTML parser, just enough to keep the plain-text view readable."""
    text = re.sub(r"(?i)<br\s*/?>", "\n", html)
    text = re.sub(r"(?i)</p>", "\n\n", text)
    text = _TAG_RE.sub("", text)
    text = _WS_RE.sub(" ", text)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line or True).strip()


def render_template(name: str, context: dict) -> tuple[str, str]:
    """Render `templates/{name}.html` with `context`. Returns (html, text)."""
    try:
        template = _env.get_template(f"{name}.html")
    except TemplateNotFound as e:
        raise TemplateNotFoundException(name) from e

    html = template.render(**context)
    return html, _html_to_text(html)


def render_subject(subject_template: str, context: dict) -> str:
    """Render a subject line — plain text, not HTML."""
    return _subject_env.from_string(subject_template).render(**context)


def render_whatsapp_template(name: str, context: dict) -> str:
    """Render `templates_whatsapp/{name}.txt` with `context`. Purpose-built
    short-form text, not derived from the HTML email templates."""
    try:
        template = _whatsapp_env.get_template(f"{name}.txt")
    except TemplateNotFound as e:
        raise TemplateNotFoundException(name) from e

    return template.render(**context).strip()
