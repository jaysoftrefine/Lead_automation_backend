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
    ("{{name}}",                     "Recipient's name"),
    ("{{company_name}}",             "Company name"),
    ("{{job_requirement}}",          "Job / Project requirement"),
    ("{{ai_company_hook_temp1}}",     "Template 1: AI personalized company observation"),
    ("{{ai_value_pitch_temp1}}",      "Template 1: AI tailored value proposition"),
    ("{{ai_company_hook_temp2}}",     "Template 2: AI follow-up / reminder hook"),
    ("{{ai_value_pitch_temp2}}",      "Template 2: AI follow-up value proposition"),
    ("{{role}}",                     "Founder / Contact role"),
    ("{{company_website}}",          "Company website"),
    ("{{category}}",                 "Industry / Category"),
    ("{{city}}",                     "Company city"),
    ("{{country}}",                  "Company country"),
]


def resolve_variables(subject: str, body: str, context: Dict[str, str]) -> tuple[str, str]:
    """Replace all {{variable}} and bracket placeholders with context values."""
    for key, value in context.items():
        placeholder = f"{{{{{key}}}}}"
        safe_val = str(value or "").strip()
        subject = subject.replace(placeholder, safe_val)
        body = body.replace(placeholder, safe_val)

    # Support bracket syntax from direct user templates like [First Name] and [Job Requirement]
    name_val = context.get("first_name") or context.get("name") or "there"
    job_val = context.get("job_requirement") or context.get("role") or "your technical requirements"
    comp_val = context.get("company_name") or "your company"

    for pattern, val in [
        ("[First Name]", name_val),
        ("[first name]", name_val),
        ("[Name]", name_val),
        ("[Job Requirement]", job_val),
        ("[job requirement]", job_val),
        ("[Company Name]", comp_val),
    ]:
        subject = subject.replace(pattern, val)
        body = body.replace(pattern, val)

    return subject, body


def build_context(
    person_name: Optional[str] = None,
    role: Optional[str] = None,
    job_requirement: Optional[str] = None,
    company_name: Optional[str] = None,
    website: Optional[str] = None,
    city: Optional[str] = None,
    country: Optional[str] = None,
    category: Optional[str] = None,
    sender_name: Optional[str] = None,
    email: Optional[str] = None,
    company_description: Optional[str] = None,
    company_tags: Optional[str] = None,
    ai_company_hook_temp1: Optional[str] = None,
    ai_value_pitch_temp1: Optional[str] = None,
    ai_company_hook_temp2: Optional[str] = None,
    ai_value_pitch_temp2: Optional[str] = None,
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

    hook_1 = ai_company_hook_temp1 or ai_company_hook
    pitch_1 = ai_value_pitch_temp1 or ai_value_pitch
    hook_2 = ai_company_hook_temp2
    pitch_2 = ai_value_pitch_temp2

    # If AI hooks/pitches not fully provided, generate with Gemini
    if use_ai and company_name and (not hook_1 or not pitch_1 or not hook_2 or not pitch_2):
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
            hook_1 = hook_1 or ai_data.get("ai_company_hook_temp1") or ai_data.get("ai_company_hook")
            pitch_1 = pitch_1 or ai_data.get("ai_value_pitch_temp1") or ai_data.get("ai_value_pitch")
            hook_2 = hook_2 or ai_data.get("ai_company_hook_temp2")
            pitch_2 = pitch_2 or ai_data.get("ai_value_pitch_temp2")
        except Exception:
            pass

    default_hook_1 = (
        f"I recently came across {c_name} and really liked how you're driving innovation in your product ecosystem. "
        f"I'd love to explore how we could support {c_name} with AI automation, intelligent workflows, and scalable product engineering."
        if company_name else
        "I recently came across your company and was really impressed by your innovation and product vision. I'd love to explore how we could support your team with AI automation, intelligent workflows, and scalable product engineering."
    )

    default_pitch_1 = (
        f"We can support areas such as AI-powered workflow automation, intelligent data pipelines, RAG and knowledge systems, GenAI integrations, and scalable product engineering as {c_name} evolves."
        if company_name else
        "We can support areas such as AI-powered workflow automation, intelligent data pipelines, RAG and knowledge systems, GenAI integrations, and scalable product engineering as your product evolves."
    )

    default_hook_2 = (
        f"Following up on my previous note regarding {c_name}—I wanted to check if exploring AI automation, agentic workflows, or dedicated engineering support would be timely for your team."
        if company_name else
        "Following up on my previous note—I wanted to check if exploring AI automation, agentic workflows, or dedicated engineering support would be timely for your team."
    )

    default_pitch_2 = (
        f"We'd love to share relevant case studies or explore how our 90+ engineers can support {c_name}'s technical roadmap."
        if company_name else
        "We'd love to share relevant case studies or explore how our 90+ engineers can support your technical roadmap."
    )

    final_hook_1 = hook_1 or default_hook_1
    final_pitch_1 = pitch_1 or default_pitch_1
    final_hook_2 = hook_2 or default_hook_2
    final_pitch_2 = pitch_2 or default_pitch_2

    return {
        "name":                     best_name,
        "first_name":               first_name or best_name,
        "founder_name":             person_name or best_name,
        "role":                     role or "",
        "job_requirement":          job_requirement or role or "your technical requirements",
        "company_name":             company_name or "",
        "company_website":          website or "",
        "city":                     city or "",
        "country":                  country or "",
        "category":                 category or "",
        "sender_name":              sender_name or "Stephan Arnas",
        "email":                    email or "",
        "ai_company_hook_temp1":    final_hook_1,
        "ai_value_pitch_temp1":     final_pitch_1,
        "ai_company_hook_temp2":    final_hook_2,
        "ai_value_pitch_temp2":     final_pitch_2,
        "temp1_ai_company_hook":    final_hook_1,
        "temp1_ai_value_pitch":     final_pitch_1,
        "temp2_ai_company_hook":    final_hook_2,
        "temp2_ai_value_pitch":     final_pitch_2,
        "ai_company_hook":          final_hook_1,
        "ai_value_pitch":           final_pitch_1,
    }


def get_sample_context(sender_name: str = "Stephan Arnas") -> Dict[str, str]:
    """Returns sample context for template preview rendering."""
    sample_hook_1 = (
        "I recently came across Poetry and really liked how you're bringing AI into talent acquisition workflows, "
        "from talent intelligence and recruitment marketing to recruiter enablement. I'd love to explore how we could support "
        "Poetry with AI automation, intelligent workflows, and scalable product engineering."
    )
    sample_pitch_1 = (
        "We can support areas such as AI-powered workflow automation, talent intelligence, RAG and knowledge systems, "
        "GenAI integrations, browser/ATS workflows, and scalable product engineering as Poetry evolves."
    )
    sample_hook_2 = (
        "I really liked Poetry’s approach to combining AI with recruiter intelligence to streamline talent acquisition while maintaining human touch. "
        "As the product evolves, I believe there could be a good opportunity for SoftRefine to support areas like AI engineering, workflow automation, real-time analytics, and scalable product development."
    )
    sample_pitch_2 = (
        "As the product evolves, I believe there could be a good opportunity for SoftRefine to support areas like AI engineering, workflow automation, real-time analytics, and scalable product development."
    )
    return {
        "name":                     "Adam",
        "first_name":               "Adam",
        "founder_name":             "Adam Smith",
        "role":                     "Founder & CEO",
        "job_requirement":          "Senior AI & Full Stack Engineer",
        "company_name":             "Poetry",
        "company_website":          "https://poetry.hr",
        "city":                     "London",
        "country":                  "United Kingdom",
        "category":                 "HR Tech & AI",
        "sender_name":              sender_name,
        "email":                    "adam@poetry.hr",
        "ai_company_hook_temp1":    sample_hook_1,
        "ai_value_pitch_temp1":     sample_pitch_1,
        "ai_company_hook_temp2":    sample_hook_2,
        "ai_value_pitch_temp2":     sample_pitch_2,
        "temp1_ai_company_hook":    sample_hook_1,
        "temp1_ai_value_pitch":     sample_pitch_1,
        "temp2_ai_company_hook":    sample_hook_2,
        "temp2_ai_value_pitch":     sample_pitch_2,
        "ai_company_hook":          sample_hook_1,
        "ai_value_pitch":           sample_pitch_1,
    }


def text_to_html_email(text: str) -> str:
    """Convert a plain text or markdown email body into styled HTML compatible with email clients and preview."""
    if not text:
        return ""
    if any(tag in text.lower() for tag in ["<html", "<body", "<table", "<div", "<p ", "<p>", "<br", "<ul", "<ol", "<li"]):
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
