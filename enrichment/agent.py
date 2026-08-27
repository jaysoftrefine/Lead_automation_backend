"""LangGraph-powered Autonomous Thinking Agent for Lead Enrichment with COT & Chain of Action."""

import json
import re
from typing import List, Optional, Dict, Any, TypedDict
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, START, END
from langchain_core.messages import SystemMessage, HumanMessage

from core.logging import logger
from core.exceptions import EnrichmentException
from config.settings import settings
from db.models import RawJobPosting, EnrichedLead, ContactPerson
from llm.registry import LLMProviderRegistry
from enrichment.schemas import ExtractedLeadData
from enrichment.prompts import LEAD_ENRICHMENT_SYSTEM_PROMPT, ENRICHMENT_USER_PROMPT_TEMPLATE
from enrichment.tools.web_search import TavilySearchTool, get_tavily_search_tool
from core.email_verifier import email_verifier


class LeadState(TypedDict):
    """LangGraph state representing the complete Lead Intelligence trajectory."""
    # Input Job Metadata
    title: str
    company: str
    location: str
    site: str
    job_url: str
    job_type: str
    salary_info: str
    date_posted: str
    description: str
    target_company_size: str
    target_job_type: str

    # Chain of Thought (COT) Reasoning
    cot_reasoning: str
    extracted_tech_stack: List[str]
    is_direct_employer: bool

    # Chain of Action (COA) Web Research
    iteration: int
    max_iterations: int
    pending_queries: List[str]
    executed_queries: List[str]
    search_knowledge_base: List[Dict[str, Any]]

    # Final Output
    structured_lead: Optional[ExtractedLeadData]


class LeadEnrichmentAgent:
    """
    Autonomous LangGraph Thinking Agent.
    Implements a multi-step Chain-of-Thought (COT) & Chain-of-Action (COA)
    graph to research company domains, discover decision-maker contacts,
    and output strictly validated structured lead intelligence.
    """

    def __init__(
        self,
        provider_name: Optional[str] = None,
        model_name: Optional[str] = None,
        max_tool_iterations: int = 2,
    ):
        self.provider = LLMProviderRegistry.get_provider(
            provider_name=provider_name or settings.default_llm_provider,
            model_name=model_name,
        )
        self.max_tool_iterations = max_tool_iterations
        self.tavily_tool = TavilySearchTool()
        self.graph = self._build_graph()

    def _build_graph(self):
        """Constructs the LangGraph state machine with COT analysis & COA research loop."""
        workflow = StateGraph(LeadState)

        # 1. Register Nodes
        workflow.add_node("cot_analyze", self._node_cot_analyze)
        workflow.add_node("coa_plan_searches", self._node_coa_plan_searches)
        workflow.add_node("coa_execute_searches", self._node_coa_execute_searches)
        workflow.add_node("synthesize_lead", self._node_synthesize_lead)

        # 2. Define Flow & Transitions
        workflow.add_edge(START, "cot_analyze")
        workflow.add_edge("cot_analyze", "coa_plan_searches")
        workflow.add_edge("coa_plan_searches", "coa_execute_searches")
        
        # 3. Conditional Quality Gate / Refinement Loop
        workflow.add_conditional_edges(
            "coa_execute_searches",
            self._route_after_search,
            {
                "continue_search": "coa_plan_searches",
                "synthesize": "synthesize_lead",
            }
        )
        workflow.add_edge("synthesize_lead", END)

        return workflow.compile()

    # --- LangGraph Nodes ---

    def _node_cot_analyze(self, state: LeadState) -> Dict[str, Any]:
        """Chain-of-Thought (COT) Node: Deeply analyzes job posting and plans research strategy."""
        logger.debug(f"[COT Node] Analyzing job requirements for '{state['title']}' at '{state['company']}'")

        chat_model = self.provider.get_chat_model()
        analysis_prompt = f"""You are an elite B2B Lead Intelligence Analyst.
Conduct an in-depth Chain-of-Thought (COT) analysis of the following job posting:

- Role: {state['title']}
- Company: {state['company']}
- Location: {state['location']}
- Job Type: {state['job_type']}
- Source: {state['site']}
- Target Company Size Focus: {state.get('target_company_size', 'small')} (MAX 50 employees for small)
- Target Job Type Focus: {state.get('target_job_type', 'all')}
- Description: {state['description'][:2000]}

### REQUIRED CHAIN-OF-THOUGHT (COT) BREAKDOWN:
1. Entity & Company Size Assessment: Is this a direct employer? What is the estimated company size (e.g. startup/small 1-10 or 11-50 [max 50], medium 51-500, enterprise 500+)? Does it match the target size focus ({state.get('target_company_size', 'small')})?
2. Executive Target Personas: Who is the Founder, Co-Founder, Owner, CEO, CTO, COO, Director, or Product Manager (PM) leading this team?
3. Job Type / Freelance Suitability: Is this a genuine contract/freelance role or standard full-time corporate position? Does it align with target job type ({state.get('target_job_type', 'all')})?
4. Technical Stack: What core technologies, tools, and platforms are needed?
5. Hiring Signal: What indicates urgency, growth, or direct outreach opportunity to leadership?
6. Leadership Discovery Strategy: What targeted search queries will find the Founder, CEO, CTO, Director, or PM on LinkedIn with their direct email/contact?

Output your structured reasoning clearly."""

        try:
            res = chat_model.invoke([
                SystemMessage(content=LEAD_ENRICHMENT_SYSTEM_PROMPT),
                HumanMessage(content=analysis_prompt),
            ])
            cot_text = str(res.content) if res.content else "Direct employer hiring opportunity."
        except Exception as e:
            logger.warning(f"COT analysis fallback: {e}")
            cot_text = f"Analyzed {state['title']} at {state['company']}."

        return {"cot_reasoning": cot_text}

    def _node_coa_plan_searches(self, state: LeadState) -> Dict[str, Any]:
        """Chain-of-Action (COA) Planner: Formulates laser-targeted executive search queries based on COT."""
        iteration = state.get("iteration", 0) + 1
        executed = set(state.get("executed_queries", []))
        company = state["company"].strip()
        location = state["location"].strip()

        # Deterministic strategic query formulations focusing on C-suite & leadership
        planned_queries = []
        
        if iteration == 1:
            # Phase 1: Founders, CEO, CTO, Directors, PM, Company Domain & Headcount
            q1 = f'"{company}" ("Founder" OR "Co-Founder" OR "CEO" OR "Owner") LinkedIn'
            q2 = f'"{company}" ("CTO" OR "Chief Technology Officer" OR "Director" OR "VP") LinkedIn'
            q3 = f'"{company}" ("COO" OR "Product Manager" OR "Head of Engineering") LinkedIn'
            q4 = f'"{company}" official website company domain headquarters headcount'
            q5 = f'"{company}" ("Founder" OR "CEO" OR "CTO" OR "Director") email OR contact'
            
            for q in [q1, q2, q3, q4, q5]:
                if q not in executed:
                    planned_queries.append(q)
        else:
            # Phase 2: Executive contact details, email patterns & direct phone
            q_refine_1 = f'"{company}" ("CEO" OR "Founder" OR "CTO" OR "Director") email contact "@"'
            q_refine_2 = f'"{company}" executive contact phone number corporate office'
            for q in [q_refine_1, q_refine_2]:
                if q not in executed:
                    planned_queries.append(q)

        logger.debug(f"[COA Planner] Planned {len(planned_queries)} queries for iteration {iteration}")
        return {
            "iteration": iteration,
            "pending_queries": planned_queries[:3],  # limit batch size
        }

    def _node_coa_execute_searches(self, state: LeadState) -> Dict[str, Any]:
        """Chain-of-Action (COA) Execution Node: Runs web searches and collates raw intelligence."""
        pending = state.get("pending_queries", [])
        executed = list(state.get("executed_queries", []))
        knowledge_base = list(state.get("search_knowledge_base", []))

        for query in pending:
            if not query or query in executed:
                continue
            
            logger.info(f"🔎 [COA Action] Executing web search: '{query}'")
            executed.append(query)
            
            try:
                results = self.tavily_tool.search(query=query, max_results=4, search_depth="advanced")
                knowledge_base.append({
                    "query": query,
                    "results_count": len(results),
                    "snippets": [
                        {"title": r.get("title"), "url": r.get("url"), "content": r.get("content")}
                        for r in results
                    ]
                })
            except Exception as e:
                logger.warning(f"Search execution error for '{query}': {e}")
                knowledge_base.append({
                    "query": query,
                    "results_count": 0,
                    "snippets": [{"content": f"Search failed: {str(e)}"}]
                })

        return {
            "pending_queries": [],
            "executed_queries": executed,
            "search_knowledge_base": knowledge_base,
        }

    def _route_after_search(self, state: LeadState) -> str:
        """Determines whether additional web research refinement is required."""
        iteration = state.get("iteration", 1)
        max_iters = state.get("max_iterations", self.max_tool_iterations)

        if iteration < max_iters:
            # Check if we have at least some search knowledge
            kb = state.get("search_knowledge_base", [])
            total_snippets = sum(len(entry.get("snippets", [])) for entry in kb)
            if total_snippets < 3:
                return "continue_search"

        return "synthesize"

    def _node_synthesize_lead(self, state: LeadState) -> Dict[str, Any]:
        """Chain-of-Thought Synthesis Node: Synthesizes findings into strictly validated ExtractedLeadData."""
        logger.debug(f"[Synthesis Node] Generating structured intelligence for '{state['company']}'")

        # Format collected research snippets
        kb_text_parts = []
        for entry in state.get("search_knowledge_base", []):
            q = entry.get("query", "")
            snippets = "\n".join([f"- {s.get('title', '')}: {s.get('content', '')} (Source: {s.get('url', '')})" for s in entry.get("snippets", [])])
            kb_text_parts.append(f"### Query: '{q}'\n{snippets}")

        research_summary = "\n\n".join(kb_text_parts) if kb_text_parts else "No external web findings."

        synthesis_prompt = f"""--- JOB PROFILE ---
Title: {state['title']}
Company: {state['company']}
Location: {state['location']}
Job Type: {state['job_type']}
Salary: {state['salary_info']}
Job URL: {state['job_url']}
Target Company Size Focus: {state.get('target_company_size', 'small')} (MAX 50 employees for small: 1-10, 11-50)
Target Job Type Focus: {state.get('target_job_type', 'all')}
Description: {state['description'][:2500]}

--- CHAIN OF THOUGHT (COT) ANALYSIS ---
{state.get('cot_reasoning', 'No initial COT recorded.')}

--- CHAIN OF ACTION (COA) WEB RESEARCH FINDINGS ---
{research_summary}

--- DIRECTIVE ---
Synthesize all discoveries into the exact structured schema.
Extract:
1. Official company domain (e.g. stripe.com)
2. Accurate company size classification (e.g. '1-10 employees', '11-50 employees' for small; '51-200 employees', '201-500 employees' for medium; '500+ employees' for enterprise)
3. Verified TOP DECISION-MAKER contacts prioritizing: Founder, Co-Founder, CEO, CTO, COO, Director, Product Manager (PM), Owner (with full name, exact executive title/role, LinkedIn URL, email, phone, and confidence score 0-100)
4. Key technical stack & skills
5. Relevance score (0-100) and hiring urgency
6. Comprehensive step-by-step thinking process detailing the evidence found."""

        # Multi-Tier Robust Structured Extraction
        structured_lead = self._robust_structured_extraction(synthesis_prompt, state)
        return {"structured_lead": structured_lead}

    # --- Double-Shield Robust Structured Extraction ---

    def _robust_structured_extraction(self, prompt: str, state: LeadState) -> ExtractedLeadData:
        """
        Guarantees non-None, perfectly formed ExtractedLeadData under all circumstances:
        Tier 1: with_structured_output schema binding
        Tier 2: Direct JSON prompt with regex extraction and Pydantic parsing
        Tier 3: Safe self-healing default constructor
        """
        queries = state.get("executed_queries", [])
        company = state["company"]
        title = state["title"]

        # Tier 1: Pydantic structured LLM
        try:
            structured_model = self.provider.get_structured_llm(ExtractedLeadData)
            result = structured_model.invoke([
                SystemMessage(content=LEAD_ENRICHMENT_SYSTEM_PROMPT),
                HumanMessage(content=prompt),
            ])
            if isinstance(result, ExtractedLeadData):
                if not result.search_queries_used and queries:
                    result.search_queries_used = queries
                return result
        except Exception as tier1_err:
            logger.warning(f"Tier 1 structured output fallback triggered for '{company}': {tier1_err}")

        # Tier 2: Direct JSON Prompting
        try:
            chat_model = self.provider.get_chat_model()
            json_prompt = f"""{prompt}

Respond ONLY with a valid JSON object matching this schema:
{{
  "is_valid_lead": true,
  "relevance_score": 85,
  "company_domain": "company.com",
  "company_summary": "Summary of company...",
  "company_size": "51-200",
  "contacts": [
    {{
      "name": "Jane Doe",
      "role": "Technical Recruiter",
      "email": "jane@company.com",
      "phone": "+1 555-0199",
      "linkedin_url": null,
      "confidence_score": 80,
      "source_url": null
    }}
  ],
  "key_technologies": ["Python", "React"],
  "hiring_urgency": "High",
  "lead_summary": "Strategic overview...",
  "thinking_process": "Detailed reasoning steps...",
  "search_queries_used": {json.dumps(queries)}
}}"""
            res = chat_model.invoke([
                SystemMessage(content="You are a strict JSON data extraction engine. Output valid JSON only."),
                HumanMessage(content=json_prompt),
            ])
            raw_text = str(res.content).strip()
            
            # Extract JSON block
            json_match = re.search(r"\{.*\}", raw_text, re.DOTALL)
            if json_match:
                parsed_json = json.loads(json_match.group(0))
                parsed_lead = ExtractedLeadData.model_validate(parsed_json)
                if not parsed_lead.search_queries_used and queries:
                    parsed_lead.search_queries_used = queries
                return parsed_lead
        except Exception as tier2_err:
            logger.warning(f"Tier 2 direct JSON fallback triggered for '{company}': {tier2_err}")

        # Tier 3: Guaranteed Safe Self-Healing Construction
        logger.info(f"Tier 3 self-healing lead constructed for '{company}'")
        return ExtractedLeadData(
            is_valid_lead=True,
            relevance_score=70,
            company_domain=None,
            company_summary=f"Hiring employer for {title}.",
            company_size="Unspecified",
            contacts=[],
            key_technologies=[],
            hiring_urgency="Normal",
            lead_summary=f"Hiring opportunity for '{title}' at '{company}'.",
            thinking_process=state.get("cot_reasoning", f"Analyzed job posting for {company}."),
            search_queries_used=queries,
        )

    # --- Public API ---

    def _verify_lead_contacts(self, contacts: List[ContactPerson]) -> List[ContactPerson]:
        """Runs real-time SMTP and DNS MX verification on every discovered email."""
        verified_contacts: List[ContactPerson] = []
        for contact in contacts:
            if contact.email:
                ver_res = email_verifier.verify_email(contact.email)
                logger.info(f"📬 [Email Verifier] {contact.email} -> Status: {ver_res['status']} | Reason: {ver_res['reason']}")
                if ver_res["is_valid"]:
                    contact.is_verified = True
                    contact.verification_status = "valid"
                    contact.verification_details = ver_res["reason"]
                    contact.confidence_score = min(100, max(60, contact.confidence_score + ver_res.get("confidence_boost", 10)))
                    verified_contacts.append(contact)
                else:
                    logger.warning(f"❌ [Email Verifier] Filtered non-deliverable email: {contact.email} ({ver_res['reason']})")
                    # If contact has a person name or LinkedIn profile, keep the person but remove the invalid email
                    if contact.name or contact.linkedin_url or contact.phone:
                        contact.email = None
                        contact.is_verified = False
                        contact.verification_status = "invalid"
                        contact.verification_details = f"Email rejected: {ver_res['reason']}"
                        contact.confidence_score = max(30, contact.confidence_score - 30)
                        verified_contacts.append(contact)
            else:
                verified_contacts.append(contact)

        return verified_contacts

    def enrich_job(self, job: RawJobPosting, target_company_size: str = "small", target_job_type: str = "all") -> EnrichedLead:
        """
        Executes the full LangGraph COT & COA research workflow for a given job posting.
        """
        logger.info(f"🚀 [LangGraph Agent] Initiating COT & COA enrichment: '{job.title}' at '{job.company}' (Target Size: {target_company_size}, Job Type: {target_job_type})")
        
        initial_state: LeadState = {
            "title": job.title,
            "company": job.company,
            "location": job.location or "Remote",
            "site": job.site,
            "job_url": job.job_url,
            "job_type": job.job_type or ("Contract" if "contract" in target_job_type.lower() or "freelance" in str(job.title).lower() else "Full-time"),
            "salary_info": f"{job.salary_min or ''} - {job.salary_max or ''} {job.salary_currency or ''}".strip(),
            "date_posted": str(job.date_posted or "Recently"),
            "description": job.description or "No description provided",
            "target_company_size": target_company_size,
            "target_job_type": target_job_type,
            "cot_reasoning": "",
            "extracted_tech_stack": [],
            "is_direct_employer": True,
            "iteration": 0,
            "max_iterations": self.max_tool_iterations,
            "pending_queries": [],
            "executed_queries": [],
            "search_knowledge_base": [],
            "structured_lead": None,
        }

        try:
            # Execute LangGraph workflow
            final_state = self.graph.invoke(initial_state)
            structured: ExtractedLeadData = final_state.get("structured_lead") or self._robust_structured_extraction("", initial_state)

            # Real-time SMTP and DNS MX Verification of all contacts
            validated_contacts = self._verify_lead_contacts(structured.contacts)

            # Construct EnrichedLead database entity
            enriched = EnrichedLead(
                job_url=job.job_url,
                title=job.title,
                company=job.company,
                site=job.site,
                location=job.location,
                job_type=job.job_type,
                job_description=job.description,
                is_valid_lead=structured.is_valid_lead,
                relevance_score=structured.relevance_score,
                company_domain=structured.company_domain,
                company_summary=structured.company_summary,
                company_size=structured.company_size,
                contacts=validated_contacts,
                key_technologies=structured.key_technologies,
                hiring_urgency=structured.hiring_urgency,
                lead_summary=structured.lead_summary,
                agent_thinking_process=structured.thinking_process,
                search_queries_used=structured.search_queries_used or final_state.get("executed_queries", []),
                status="new",
            )

            logger.info(
                f"✅ [LangGraph Agent] Finished '{job.company}' - Contacts: {len(enriched.contacts)}, Domain: {enriched.company_domain}, Score: {enriched.relevance_score}/100"
            )
            return enriched

        except Exception as e:
            logger.error(f"Critical agent error for '{job.title}' at '{job.company}': {e}")
            return EnrichedLead(
                job_url=job.job_url,
                title=job.title,
                company=job.company,
                site=job.site,
                location=job.location,
                job_type=job.job_type,
                job_description=job.description,
                is_valid_lead=True,
                relevance_score=50,
                lead_summary=f"Processed with fallback: {str(e)}",
                agent_thinking_process=f"Fallback execution due to error: {str(e)}",
                search_queries_used=[],
                status="new",
            )
