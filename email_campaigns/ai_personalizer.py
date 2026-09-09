"""AI Personalizer using Gemini to generate personalized company hooks and value pitches."""

import os
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field

from core.logging import logger

# In-memory cache to avoid redundant API calls for identical companies
_CACHE: Dict[str, Dict[str, str]] = {}


class EmailHookAndPitch(BaseModel):
    ai_company_hook_temp1: str = Field(
        description="A natural 2-sentence opening hook for the initial outreach email complimenting what the company specifically does based on their description/tags and expressing enthusiasm to explore AI automation, intelligent workflows, and scalable product engineering."
    )
    ai_value_pitch_temp1: str = Field(
        description="A tailored 1-sentence value pitch for the initial outreach email highlighting how SoftRefine (90+ AI/ML & Full-Stack engineers) can specifically support their technical initiatives, ending with 'as {company_name} evolves.'"
    )
    ai_company_hook_temp2: str = Field(
        description="Exactly 2 sentences for Template 2: 1) 'I really liked {company_name}’s approach to [specific deep technical/product observation from web research].' 2) 'As the product evolves, I believe there could be a good opportunity for SoftRefine to support areas like [tailored technical domains].'"
    )
    ai_value_pitch_temp2: str = Field(
        description="The second sentence of the Template 2 hook: 'As the product evolves, I believe there could be a good opportunity for SoftRefine to support areas like [tailored technical domains].'"
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
    Generate {{ai_company_hook_temp1}}, {{ai_value_pitch_temp1}},
    {{ai_company_hook_temp2}}, and {{ai_value_pitch_temp2}} using Google Gemini with Tavily web research.
    Falls back gracefully to intelligent template defaults if Gemini is unreachable.
    """
    c_name = (company_name or "").strip()
    if not c_name:
        default_hook_1 = "I recently came across your company and was really impressed by your innovation and product vision. I'd love to explore how we could support your team with AI automation, intelligent workflows, and scalable product engineering."
        default_pitch_1 = "We can support areas such as AI-powered workflow automation, intelligent data pipelines, RAG and knowledge systems, GenAI integrations, and scalable product engineering as your product evolves."
        default_hook_2 = "I really liked your approach to product innovation and user-centric architecture. As the product evolves, I believe there could be a good opportunity for SoftRefine to support areas like AI engineering, intelligent workflows, real-time analytics, and scalable product development."
        default_pitch_2 = "As the product evolves, I believe there could be a good opportunity for SoftRefine to support areas like AI engineering, intelligent workflows, real-time analytics, and scalable product development."
        return {
            "ai_company_hook_temp1": default_hook_1,
            "ai_value_pitch_temp1": default_pitch_1,
            "ai_company_hook_temp2": default_hook_2,
            "ai_value_pitch_temp2": default_pitch_2,
            "temp1_ai_company_hook": default_hook_1,
            "temp1_ai_value_pitch": default_pitch_1,
            "temp2_ai_company_hook": default_hook_2,
            "temp2_ai_value_pitch": default_pitch_2,
            "ai_company_hook": default_hook_1,
            "ai_value_pitch": default_pitch_1,
        }

    # Check cache
    cache_key = f"{c_name.lower()}_{(website or '').lower()}"
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    # Explore the internet via Tavily to get real-time intelligence about the company
    web_intel = ""
    try:
        from enrichment.tools.web_search import TavilySearchTool
        tavily = TavilySearchTool()
        if tavily.api_key:
            search_query = f"{c_name} company what does it do product technology"
            if website:
                search_query += f" {website}"
            search_results = tavily.search(search_query, max_results=2)
            if search_results:
                snippets = [r.get("content", "")[:350].strip() for r in search_results if r.get("content")]
                web_intel = "\n".join(snippets)
    except Exception as e:
        logger.debug(f"Tavily web exploration skipped for {c_name}: {e}")

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
        details.append(f"Company Overview: {description[:400].strip()}")
    if web_intel:
        details.append(f"Live Web Research (Internet Intelligence):\n{web_intel}")

    company_summary = "\n".join(details)

    prompt = f"""You are writing authentic B2B outreach email copy on behalf of Stephan Arnas from SoftRefine Technology (a skilled team of 90+ AI/ML, GenAI, and Full-Stack developers).

Company Information:
{company_summary}

Your task is to generate FOUR personalized text components across two sequence steps:

=== STEP 1: INITIAL OUTREACH (Template 1) ===
1. ai_company_hook_temp1:
- A 2-sentence opening hook.
- It MUST begin with or include: "I recently came across {c_name} and really liked how you're..."
- Mention what {c_name} actually does based on the company details above (their actual domain, product features, or unique positioning).
- Conclude the hook with: "I'd love to explore how we could support {c_name} with AI automation, intelligent workflows, and scalable product engineering."

2. ai_value_pitch_temp1:
- A single concise sentence starting with: "We can support areas such as..."
- Tailor the specific technical capabilities (e.g. AI automation, data pipelines, RAG systems, GenAI integrations, LLM routing, cloud architecture) to match {c_name}'s domain.
- Conclude the sentence with: "...as {c_name} evolves."

=== STEP 2: FOLLOW-UP / REMINDER (Template 2) ===
3. ai_company_hook_temp2:
- Exactly 2 sentences for Template 2 (Follow-up / Reminder).
- Sentence 1 MUST start with: "I really liked {c_name}’s approach to " followed by a specific deep technical/product observation exploring what they actually do based on the web research and company details (their specific technology, product architecture, hardware/software combination, or unique innovation).
- Sentence 2 MUST start with: "As the product evolves, I believe there could be a good opportunity for SoftRefine to support areas like " followed by 3-4 tailored technical domains relevant to their product (e.g. AI engineering, sensor-data processing, real-time analytics, and scalable product development).
- Pattern Reference:
  "I really liked Wayvee’s approach to combining RF sensing with AI to generate real-time insights while keeping privacy at the forefront. As the product evolves, I believe there could be a good opportunity for SoftRefine to support areas like AI engineering, sensor-data processing, real-time analytics, and scalable product development."

4. ai_value_pitch_temp2:
- Sentence 2 of Template 2: "As the product evolves, I believe there could be a good opportunity for SoftRefine to support areas like [tailored domains]."

Do not include quotes or surrounding formatting. Be natural, professional, and directly relevant to {c_name}.
"""

    try:
        from llm.providers.gemini import GeminiProvider

        provider = GeminiProvider(temperature=0.2)
        llm = provider.get_structured_llm(EmailHookAndPitch)
        result: EmailHookAndPitch = llm.invoke(prompt)

        hook_1 = result.ai_company_hook_temp1.strip()
        pitch_1 = result.ai_value_pitch_temp1.strip()
        hook_2 = result.ai_company_hook_temp2.strip()
        pitch_2 = result.ai_value_pitch_temp2.strip()

        # Ensure company name is present in hook 1
        if c_name.lower() not in hook_1.lower():
            hook_1 = f"I recently came across {c_name} and really liked your product innovation. I'd love to explore how we could support {c_name} with AI automation, intelligent workflows, and scalable product engineering."
        if c_name.lower() not in hook_2.lower():
            hook_2 = f"I really liked {c_name}’s approach to driving innovation in product development. As the product evolves, I believe there could be a good opportunity for SoftRefine to support areas like AI engineering, intelligent workflows, and scalable product development."

        res_dict = {
            "ai_company_hook_temp1": hook_1,
            "ai_value_pitch_temp1": pitch_1,
            "ai_company_hook_temp2": hook_2,
            "ai_value_pitch_temp2": pitch_2,
            "temp1_ai_company_hook": hook_1,
            "temp1_ai_value_pitch": pitch_1,
            "temp2_ai_company_hook": hook_2,
            "temp2_ai_value_pitch": pitch_2,
            "ai_company_hook": hook_1,
            "ai_value_pitch": pitch_1,
        }
        _CACHE[cache_key] = res_dict
        return res_dict

    except Exception as e:
        logger.warning(f"Failed to generate Gemini AI hook for {c_name}: {e}. Using dynamic fallback.")
        focus = category or tags or "product"
        fallback_hook_1 = (
            f"I recently came across {c_name} and really liked how you're driving innovation in {focus}. "
            f"I'd love to explore how we could support {c_name} with AI automation, intelligent workflows, and scalable product engineering."
        )
        fallback_pitch_1 = (
            f"We can support areas such as AI-powered workflow automation, intelligent data pipelines, RAG systems, GenAI integrations, and scalable product engineering as {c_name} evolves."
        )
        fallback_hook_2 = (
            f"I really liked {c_name}’s approach to driving innovation in {focus}. "
            f"As the product evolves, I believe there could be a good opportunity for SoftRefine to support areas like AI engineering, intelligent workflows, and scalable product development."
        )
        fallback_pitch_2 = (
            f"As the product evolves, I believe there could be a good opportunity for SoftRefine to support areas like AI engineering, intelligent workflows, and scalable product development."
        )
        return {
            "ai_company_hook_temp1": fallback_hook_1,
            "ai_value_pitch_temp1": fallback_pitch_1,
            "ai_company_hook_temp2": fallback_hook_2,
            "ai_value_pitch_temp2": fallback_pitch_2,
            "temp1_ai_company_hook": fallback_hook_1,
            "temp1_ai_value_pitch": fallback_pitch_1,
            "temp2_ai_company_hook": fallback_hook_2,
            "temp2_ai_value_pitch": fallback_pitch_2,
            "ai_company_hook": fallback_hook_1,
            "ai_value_pitch": fallback_pitch_1,
        }
