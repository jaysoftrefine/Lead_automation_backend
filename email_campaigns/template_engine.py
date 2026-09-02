"""
Template Engine — resolves {{variable}} placeholders in email subjects and bodies.

Supported variables:
  {{founder_name}}    - person's name from the people table
  {{first_name}}      - first name only
  {{role}}            - founder's role (e.g. CEO, Founder)
  {{company_name}}    - company name
  {{company_website}} - company website URL
  {{city}}            - company city
  {{country}}         - company country
  {{category}}        - startup category
  {{sender_name}}     - from_name in SMTP config (the outreach sender)
  {{email}}           - recipient email (for reference in body)
"""

import re
from typing import Dict, Optional


AVAILABLE_VARIABLES = [
    ("{{name}}",                "Recipient's name"),
    ("{{company_name}}",        "Company name"),
    ("{{ai_company_hook}}",     "AI personalized company observation"),
    ("{{ai_value_pitch}}",      "AI tailored support / value proposition"),
    ("{{role}}",                "Founder / Contact role"),
    ("{{company_website}}",     "Company website"),
    ("{{category}}",            "Industry / Category"),
    ("{{city}}",                "Company city"),
    ("{{country}}",             "Company country"),
]


def resolve_variables(subject: str, body: str, context: Dict[str, str]) -> tuple[str, str]:
    """Replace all {{variable}} placeholders with context values."""
    for key, value in context.items():
        placeholder = f"{{{{{key}}}}}"
        safe_val = str(value or "").strip()
        subject = subject.replace(placeholder, safe_val)
        body = body.replace(placeholder, safe_val)
    return subject, body


def build_context(
    person_name: Optional[str] = None,
    role: Optional[str] = None,
    company_name: Optional[str] = None,
    website: Optional[str] = None,
    city: Optional[str] = None,
    country: Optional[str] = None,
    category: Optional[str] = None,
    sender_name: Optional[str] = None,
    email: Optional[str] = None,
    company_description: Optional[str] = None,
    company_tags: Optional[str] = None,
    ai_company_hook: Optional[str] = None,
    ai_value_pitch: Optional[str] = None,
    use_ai: bool = True,
) -> Dict[str, str]:
    """Build the variable resolution context for a single recipient."""
    first_name = ""
    if person_name:
        parts = person_name.strip().split()
        first_name = parts[0] if parts else person_name

    best_name = first_name or person_name or "there"
    c_name = company_name or "your company"

    hook = ai_company_hook
    pitch = ai_value_pitch

    # If AI hook or pitch not provided, generate with Gemini
    if use_ai and company_name and (not hook or not pitch):
        try:
            from email_campaigns.ai_personalizer import generate_ai_hook_and_pitch
            ai_data = generate_ai_hook_and_pitch(
                company_name=company_name,
                recipient_name=person_name,
                role=role,
                website=website,
                description=company_description,
                tags=company_tags,
                category=category,
            )
            hook = hook or ai_data.get("ai_company_hook")
            pitch = pitch or ai_data.get("ai_value_pitch")
        except Exception:
            pass

    default_hook = (
        f"I recently came across {c_name} and really liked how you're driving innovation in your product ecosystem. "
        f"I'd love to explore how we could support {c_name} with AI automation, intelligent workflows, and scalable product engineering."
        if company_name else
        "I recently came across your company and was really impressed by your innovation and product vision. I'd love to explore how we could support your team with AI automation, intelligent workflows, and scalable product engineering."
    )

    default_pitch = (
        f"We can support areas such as AI-powered workflow automation, intelligent data pipelines, RAG and knowledge systems, GenAI integrations, and scalable product engineering as {c_name} evolves."
        if company_name else
        "We can support areas such as AI-powered workflow automation, intelligent data pipelines, RAG and knowledge systems, GenAI integrations, and scalable product engineering as your product evolves."
    )

    return {
        "name":                best_name,
        "first_name":          first_name or best_name,
        "founder_name":        person_name or best_name,
        "role":                role or "",
        "company_name":        company_name or "",
        "company_website":     website or "",
        "city":                city or "",
        "country":             country or "",
        "category":            category or "",
        "sender_name":         sender_name or "Stephan Arnas",
        "email":               email or "",
        "ai_company_hook":     hook or default_hook,
        "ai_value_pitch":      pitch or default_pitch,
    }


def get_sample_context(sender_name: str = "Stephan Arnas") -> Dict[str, str]:
    """Returns sample context for template preview rendering."""
    return {
        "name":                "Adam",
        "first_name":          "Adam",
        "founder_name":        "Adam Smith",
        "role":                "Founder & CEO",
        "company_name":        "Poetry",
        "company_website":     "https://poetry.hr",
        "city":                "London",
        "country":             "United Kingdom",
        "category":            "HR Tech & AI",
        "sender_name":         sender_name,
        "email":               "adam@poetry.hr",
        "ai_company_hook":     "I recently came across Poetry and really liked how you're bringing AI into talent acquisition workflows, from talent intelligence and recruitment marketing to recruiter enablement. I'd love to explore how we could support Poetry with AI automation, intelligent workflows, and scalable product engineering.",
        "ai_value_pitch":      "We can support areas such as AI-powered workflow automation, talent intelligence, RAG and knowledge systems, GenAI integrations, browser/ATS workflows, and scalable product engineering as Poetry evolves.",
    }


def text_to_html_email(text: str) -> str:
    """Convert a plain text or markdown email body into styled HTML compatible with email clients and preview."""
    if not text:
        return ""
    if any(tag in text.lower() for tag in ["<html", "<body", "<table", "<div", "<p "]):
        return text

    blocks = re.split(r"\n\s*\n", text.strip())
    html_blocks = []

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        raw_lines = [l.strip() for l in block.split("\n") if l.strip()]

        # Sub-group into contiguous non-list and list lines
        groups = []
        curr_type = None  # 'p' or 'list'
        curr_items = []

        for line in raw_lines:
            is_bullet = bool(re.match(r"^(•|\-|\+|\*\s)", line))
            item_type = "list" if is_bullet else "p"
            if item_type != curr_type:
                if curr_items:
                    groups.append((curr_type, curr_items))
                curr_type = item_type
                curr_items = [line]
            else:
                curr_items.append(line)
        if curr_items:
            groups.append((curr_type, curr_items))

        for g_type, lines in groups:
            if g_type == "list":
                items_html = []
                for line in lines:
                    content = re.sub(r"^(•|\-|\+|\*\s)\s*", "", line)
                    content = re.sub(r"\*\*(.*?)\*\*", r'<strong style="color:#0f172a;">\1</strong>', content)
                    content = re.sub(r"\[([^\]]+)\]\((https?://[^\)]+)\)", r'<a href="\2" style="color:#1d4ed8; text-decoration:underline; font-weight:500;" target="_blank">\1</a>', content)
                    content = re.sub(r"(?<!href=\")(?<!\">)(https?://[^\s<]+)", r'<a href="\1" style="color:#1d4ed8; text-decoration:underline;" target="_blank">\1</a>', content)
                    items_html.append(f'<li style="margin-bottom: 6px; line-height: 1.6; color:#1e293b;"><span style="color:#1e293b;">{content}</span></li>')
                html_blocks.append('<ul style="margin: 6px 0 16px 22px; padding: 0; list-style-type: disc; color: #0284c7;">\n' + "\n".join(items_html) + "\n</ul>")
            else:
                processed_lines = []
                for line in lines:
                    line_str = line
                    line_str = re.sub(r"\*\*(.*?)\*\*", r'<strong style="color:#0f172a;">\1</strong>', line_str)
                    line_str = re.sub(r"\[([^\]]+)\]\((https?://[^\)]+)\)", r'<a href="\2" style="color:#1d4ed8; text-decoration:underline; font-weight:500;" target="_blank">\1</a>', line_str)
                    line_str = re.sub(r"(?<!href=\")(?<!\">)(https?://[^\s<]+)", r'<a href="\1" style="color:#1d4ed8; text-decoration:underline;" target="_blank">\1</a>', line_str)
                    processed_lines.append(line_str)
                p_content = "<br/>\n".join(processed_lines)
                html_blocks.append(f'<p style="margin: 0 0 14px 0; font-size: 14px; line-height: 1.6; color: #1e293b;">\n{p_content}\n</p>')

    content_html = "\n\n".join(html_blocks)
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; font-size: 14px; line-height: 1.6; color: #1e293b; background-color: #ffffff; margin: 0; padding: 12px 0;">
<div style="max-width: 680px; margin: 0; font-size: 14px; line-height: 1.6; color: #1e293b;">
{content_html}
</div>
</body>
</html>"""
