"""AI Personalizer using Gemini to generate personalized company hooks and value pitches."""

import os
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field

from core.logging import logger

# In-memory cache to avoid redundant API calls for identical companies
_CACHE: Dict[str, Dict[str, str]] = {}


class EmailHookAndPitch(BaseModel):
    ai_company_hook: str = Field(
        description="A natural 2-sentence opening hook complimenting what the company specifically does based on their description/tags and expressing enthusiasm to explore AI automation, intelligent workflows, and scalable product engineering."
    )
    ai_value_pitch: str = Field(
        description="A tailored 1-sentence value pitch highlighting how SoftRefine (90+ AI/ML & Full-Stack engineers) can specifically support their technical initiatives, ending with 'as {company_name} evolves.'"
    )


def generate_ai_hook_and_pitch(
    company_name: Optional[str] = None,
    recipient_name: Optional[str] = None,
    role: Optional[str] = None,
    website: Optional[str] = None,
    description: Optional[str] = None,
    tags: Optional[str] = None,
    category: Optional[str] = None,
) -> Dict[str, str]:
    """
    Generate {{ai_company_hook}} and {{ai_value_pitch}} using Google Gemini.
    Falls back gracefully to intelligent template defaults if Gemini is unreachable.
    """
    c_name = (company_name or "").strip()
    if not c_name:
        return {
            "ai_company_hook": "I recently came across your company and was really impressed by your innovation and product vision. I'd love to explore how we could support your team with AI automation, intelligent workflows, and scalable product engineering.",
            "ai_value_pitch": "We can support areas such as AI-powered workflow automation, intelligent data pipelines, RAG and knowledge systems, GenAI integrations, and scalable product engineering as your product evolves.",
        }

    # Check cache
    cache_key = f"{c_name.lower()}_{(website or '').lower()}"
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    # Build prompt context
    details = [f"Company Name: {c_name}"]
    if recipient_name:
        details.append(f"Recipient Name: {recipient_name}")
    if role:
        details.append(f"Role / Title: {role}")
    if website:
        details.append(f"Website: {website}")
    if category:
        details.append(f"Category / Sector: {category}")
    if tags:
        details.append(f"Tags / Focus: {tags}")
    if description:
        # Limit description to 400 chars for concise context
        details.append(f"Company Overview: {description[:400].strip()}")

    company_summary = "\n".join(details)

    prompt = f"""You are writing a warm, highly authentic B2B outreach email on behalf of Stephan Arnas from SoftRefine Technology (a skilled team of 90+ AI/ML, GenAI, and Full-Stack developers).

Company Information:
{company_summary}

Your task is to generate TWO personalized text components:

1. ai_company_hook:
- A 2-sentence opening hook.
- It MUST begin with or include: "I recently came across {c_name} and really liked how you're..."
- Mention what {c_name} actually does based on the company details above (their actual domain, product features, or unique positioning).
- Conclude the hook with: "I'd love to explore how we could support {c_name} with AI automation, intelligent workflows, and scalable product engineering."

2. ai_value_pitch:
- A single concise sentence starting with: "We can support areas such as..."
- Tailor the specific technical capabilities (e.g. AI automation, data pipelines, RAG systems, GenAI integrations, LLM routing, cloud architecture) to match {c_name}'s domain.
- Conclude the sentence with: "...as {c_name} evolves."

Do not include quotes or surrounding formatting. Be natural, professional, and directly relevant to {c_name}.
"""

    try:
        from llm.providers.gemini import GeminiProvider

        provider = GeminiProvider(temperature=0.2)
        llm = provider.get_structured_llm(EmailHookAndPitch)
        result: EmailHookAndPitch = llm.invoke(prompt)

        hook = result.ai_company_hook.strip()
        pitch = result.ai_value_pitch.strip()

        # Ensure company name is present
        if c_name.lower() not in hook.lower():
            hook = f"I recently came across {c_name} and really liked your product innovation. I'd love to explore how we could support {c_name} with AI automation, intelligent workflows, and scalable product engineering."

        res_dict = {
            "ai_company_hook": hook,
            "ai_value_pitch": pitch,
        }
        _CACHE[cache_key] = res_dict
        return res_dict

    except Exception as e:
        logger.warning(f"Failed to generate Gemini AI hook for {c_name}: {e}. Using dynamic fallback.")
        # Graceful fallback
        focus = category or tags or "product"
        fallback_hook = (
            f"I recently came across {c_name} and really liked how you're driving innovation in {focus}. "
            f"I'd love to explore how we could support {c_name} with AI automation, intelligent workflows, and scalable product engineering."
        )
        fallback_pitch = (
            f"We can support areas such as AI-powered workflow automation, intelligent data pipelines, RAG systems, GenAI integrations, and scalable product engineering as {c_name} evolves."
        )
        return {
            "ai_company_hook": fallback_hook,
            "ai_value_pitch": fallback_pitch,
        }
