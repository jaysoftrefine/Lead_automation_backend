"""
cot_search.py  –  ReAct-style Chain-of-Thought with Google Gemini (Dual-Account) + DDGS

Flow (iterative):
    LLM thinks → emits SEARCH:<query>  → DDGS fetches results
    → results fed back → next query …
    → until ANSWER:<final text>  or max_steps reached.

LLM fallback hierarchy:
    1) Gemini Account 1: gemini-3.5-flash-lite → gemini-3.1-flash-lite
    2) Gemini Account 2 (if configured): gemini-3.5-flash-lite → gemini-3.1-flash-lite
    3) NVIDIA NIM: openai/gpt-oss-20b (or configured nvidia_model)

Gemini calls are throttled to GEMINI_RPM (default 15) peak requests/minute.
"""

import os
import re
import time
from typing import List, Optional, Tuple, Dict, Any

from config.settings import settings
from google import genai
from google.genai import types
from ddgs import DDGS

MAX_STEPS = 10
MAX_SEARCH_RESULTS = 5
MIN_EMAIL_SEARCHES_BEFORE_INFER = 2

GEMINI_RPM = 15
GEMINI_MODELS = (
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
)
NVIDIA_FALLBACK_MODEL = "openai/gpt-oss-20b"

SYSTEM_PROMPT = """You are a B2B research assistant. You find company executives (CEO, CTO, Founder)
and their publicly available contact info using web search.

Protocol — every turn emit EXACTLY ONE action (never both):
1. Think inside <think>…</think>
2. Then ONE of:
   SEARCH: <short focused query>
   ANSWER: <final answer with names, roles, LinkedIn URLs, emails, and source URLs>

Rules:
- Always SEARCH first. Never ANSWER on the first turn.
- Never emit SEARCH and ANSWER in the same response.
- Write your own search queries. Refine them each round using prior results.
- Use company LinkedIn URL / location / job title if provided to disambiguate.
- Goal: named executives + real LinkedIn URLs + emails found on the public web.
- Never invent LinkedIn URLs or emails. Only use URLs/emails that appeared in search results.
- Cite source URLs in the ANSWER.

Email discovery order (MUST follow — do not skip tiers):
1) PERSONAL — search the public web for a personal inbox for that person
   (gmail.com, outlook.com, yahoo.com, hotmail.com, proton.me, icloud.com, etc.).
   Example queries:
   "<Full Name>" email (gmail.com OR outlook.com OR yahoo.com)
   "<Full Name>" "@gmail.com"
2) BUSINESS — search for any work email that literally appears on a webpage
   (company site, press, job posts, filings, LinkedIn posts, directories).
   Example queries:
   "<Full Name>" email "@companydomain.com"
   "<Full Name>" mailto OR "email:" OR contact
   site:companydomain.com "<Full Name>"
3) INFERRED — corporate pattern guess ONLY after dedicated Tier-1 and Tier-2
   searches for that person failed. Never present guesses as verified.

For each person in ANSWER use exactly one email line:
- PERSONAL: <email> (source URL)
- BUSINESS: <email> (source URL)
- INFERRED: <email> (pattern: …; unconfirmed)
- NOT_FOUND
"""

_EMAIL_SEARCH_HINTS = (
    "email",
    "mailto",
    "contact",
    "@gmail",
    "@outlook",
    "@yahoo",
    "@hotmail",
    "@proton",
    "@icloud",
    "@",
)
_INFERRED_HINTS = re.compile(
    r"\b(likely|typically follows|standard corporate|corporate pattern|"
    r"based on .{0,40}pattern|inferred|guess)\b",
    re.IGNORECASE,
)
_VERIFIED_EMAIL_LABEL = re.compile(r"\b(PERSONAL|BUSINESS)\s*:", re.IGNORECASE)
_INFERRED_LABEL = re.compile(r"\bINFERRED\s*:", re.IGNORECASE)

_RATE_LIMIT_MARKERS = (
    "429",
    "resource_exhausted",
    "rate limit",
    "ratelimit",
    "quota",
    "too many requests",
    "exceeded",
    "503",
    "unavailable",
    "overloaded",
)


class RpmLimiter:
    """Sliding-window limiter so we stay under peak Gemini RPM."""

    def __init__(self, rpm: int = GEMINI_RPM):
        self.rpm = max(1, rpm)
        self._ts: List[float] = []

    def wait(self, verbose: bool = True) -> None:
        now = time.monotonic()
        self._ts = [t for t in self._ts if now - t < 60.0]
        if len(self._ts) >= self.rpm:
            sleep_for = 60.0 - (now - self._ts[0]) + 0.1
            if sleep_for > 0:
                if verbose:
                    print(
                        f"│  ⏳ Gemini RPM limit ({self.rpm}/min) — "
                        f"waiting {sleep_for:.1f}s..."
                    )
                time.sleep(sleep_for)
            now = time.monotonic()
            self._ts = [t for t in self._ts if now - t < 60.0]
        self._ts.append(time.monotonic())


def _is_rate_or_capacity_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(m in msg for m in _RATE_LIMIT_MARKERS)


def _extract_action(text: str):
    """Prefer SEARCH over ANSWER so LLM cannot skip the DDGS loop."""
    m_search = re.search(r"SEARCH:\s*(.+)", text, re.IGNORECASE)
    m_answer = re.search(r"ANSWER:\s*(.+)", text, re.IGNORECASE | re.DOTALL)

    if m_search and m_answer:
        return "search", m_search.group(1).strip().split("\n")[0].strip()
    if m_search:
        return "search", m_search.group(1).strip().split("\n")[0].strip()
    if m_answer:
        return "answer", m_answer.group(1).strip()
    return "unknown", text.strip()


def _is_email_search(query: str) -> bool:
    q = query.lower()
    return any(h in q for h in _EMAIL_SEARCH_HINTS)


def _premature_inferred_answer(answer: str, email_searches: int) -> bool:
    """True if answer guesses emails without enough exact-email searches."""
    if email_searches >= MIN_EMAIL_SEARCHES_BEFORE_INFER:
        return False
    if _VERIFIED_EMAIL_LABEL.search(answer):
        return False
    if _INFERRED_LABEL.search(answer) or _INFERRED_HINTS.search(answer):
        return True
    return False


def _ddgs_search(query: str) -> str:
    try:
        results = DDGS().text(query=query, max_results=MAX_SEARCH_RESULTS)
    except Exception:
        results = []
    if not results:
        return "No results found."
    lines = []
    for r in results:
        lines.append(f"Title : {r.get('title', '')}")
        lines.append(f"URL   : {r.get('href', '')}")
        lines.append(f"Body  : {str(r.get('body', ''))[:300]}")
        lines.append("---")
    return "\n".join(lines)


class FallbackChatLLM:
    """Multi-turn chat with multi-account Google Gemini fallbacks, then NVIDIA."""

    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        # Support dual Google/Gmail accounts via settings.get_google_api_keys()
        self._google_keys: List[str] = settings.get_google_api_keys()
        if not self._google_keys and os.environ.get("GOOGLE_API_KEY"):
            self._google_keys = [os.environ["GOOGLE_API_KEY"]]

        self._gemini_key_idx = 0
        self._gemini_model_idx = 0
        self._rpm = RpmLimiter(GEMINI_RPM)
        self._history: List[Tuple[str, str]] = []  # (user|model, text)
        self._backend = "gemini" if self._google_keys else "nvidia"
        self._gemini: Optional[genai.Client] = None
        self._nvidia = None
        self._init_gemini_client()

    def _init_gemini_client(self) -> None:
        if self._google_keys and self._gemini_key_idx < len(self._google_keys):
            active_key = self._google_keys[self._gemini_key_idx]
            self._gemini = genai.Client(api_key=active_key)
        else:
            self._gemini = None

    @property
    def label(self) -> str:
        if self._backend == "nvidia":
            model = settings.nvidia_model or NVIDIA_FALLBACK_MODEL
            return f"NVIDIA ({model})"
        key_num = self._gemini_key_idx + 1
        total_keys = max(1, len(self._google_keys))
        return f"Gemini ({GEMINI_MODELS[self._gemini_model_idx]} | Key {key_num}/{total_keys})"

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"│  ↪️  {msg}")

    def _advance_fallback(self, reason: str) -> bool:
        """Move to next model/account/backend. Returns False if no fallbacks left."""
        if self._backend == "gemini":
            # 1. Try next Gemini model on current key
            if self._gemini_model_idx + 1 < len(GEMINI_MODELS):
                self._gemini_model_idx += 1
                self._log(
                    f"Fallback → {GEMINI_MODELS[self._gemini_model_idx]} on Key {self._gemini_key_idx + 1} "
                    f"({reason})"
                )
                return True

            # 2. Advance to next Google/Gmail API key if available
            if self._gemini_key_idx + 1 < len(self._google_keys):
                self._gemini_key_idx += 1
                self._gemini_model_idx = 0
                self._init_gemini_client()
                self._log(
                    f"Fallback → Switched to Google Account Key {self._gemini_key_idx + 1}/{len(self._google_keys)} "
                    f"({reason})"
                )
                return True

            # 3. All Gemini keys & models exhausted, advance to NVIDIA
            nvidia_key = settings.nvidia_api_key or os.environ.get("NVIDIA_API_KEY")
            if not nvidia_key:
                self._log("NVIDIA_API_KEY missing — cannot fall back further")
                return False
            self._backend = "nvidia"
            self._nvidia = None
            nvidia_mod = settings.nvidia_model or NVIDIA_FALLBACK_MODEL
            self._log(f"Fallback → NVIDIA ({nvidia_mod}) ({reason})")
            return True
        return False

    def _gemini_history(self) -> List[types.Content]:
        contents: List[types.Content] = []
        for role, text in self._history:
            gemini_role = "user" if role == "user" else "model"
            contents.append(
                types.Content(
                    role=gemini_role,
                    parts=[types.Part(text=text)],
                )
            )
        return contents

    def _send_gemini(self, text: str) -> str:
        if not self._gemini:
            raise RuntimeError("Gemini client not initialized")
        self._rpm.wait(verbose=self.verbose)
        model = GEMINI_MODELS[self._gemini_model_idx]
        chat = self._gemini.chats.create(
            model=model,
            history=self._gemini_history(),
        )
        reply = chat.send_message(text).text
        if not reply:
            raise RuntimeError("Empty Gemini response")
        return reply

    def _get_nvidia(self):
        if self._nvidia is not None:
            return self._nvidia
        from langchain_nvidia_ai_endpoints import ChatNVIDIA

        api_key = settings.nvidia_api_key or os.environ.get("NVIDIA_API_KEY")
        model = settings.nvidia_model or NVIDIA_FALLBACK_MODEL
        self._nvidia = ChatNVIDIA(
            model=model,
            api_key=api_key,
            temperature=0.7,
            top_p=1,
            max_tokens=4096,
        )
        return self._nvidia

    def _send_nvidia(self, text: str) -> str:
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

        messages = [SystemMessage(content=SYSTEM_PROMPT)]
        for role, content in self._history:
            if role == "user":
                messages.append(HumanMessage(content=content))
            else:
                messages.append(AIMessage(content=content))
        messages.append(HumanMessage(content=text))

        result = self._get_nvidia().invoke(messages)
        reply = getattr(result, "content", None) or str(result)
        if isinstance(reply, list):
            reply = "".join(
                (p.get("text") if isinstance(p, dict) else str(p)) for p in reply
            )
        reply = (reply or "").strip()
        if not reply:
            raise RuntimeError("Empty NVIDIA response")
        return reply

    def send(self, text: str) -> str:
        last_err: Optional[BaseException] = None
        while True:
            try:
                if self._backend == "gemini":
                    reply = self._send_gemini(text)
                else:
                    reply = self._send_nvidia(text)
                self._history.append(("user", text))
                self._history.append(("model", reply))
                return reply
            except Exception as e:
                last_err = e
                # If rate/quota error and we have another Google key, switch immediately
                if self._backend == "gemini" and _is_rate_or_capacity_error(e):
                    if self._gemini_key_idx + 1 < len(self._google_keys):
                        self._gemini_key_idx += 1
                        self._gemini_model_idx = 0
                        self._init_gemini_client()
                        self._log(
                            f"{self.label} capacity/quota error — switched to Google Key "
                            f"{self._gemini_key_idx + 1}/{len(self._google_keys)}"
                        )
                        continue

                    # Otherwise retry same Gemini model once after short wait
                    wait = 4
                    self._log(f"{self.label} capacity/rate error — retry in {wait}s...")
                    time.sleep(wait)
                    try:
                        reply = self._send_gemini(text)
                        self._history.append(("user", text))
                        self._history.append(("model", reply))
                        return reply
                    except Exception as e2:
                        last_err = e2
                        e = e2

                if not self._advance_fallback(str(e)[:120]):
                    raise last_err from e


class CotSearchAgent:
    """
    Autonomous ReAct Chain-of-Thought Search Agent.
    Bypasses Tavily by orchestrating multi-step Google Gemini LLM reasoning
    (with dual-key rotation and NVIDIA fallback) paired with DuckDuckGo Search (DDGS).
    """

    def __init__(self, verbose: bool = True, max_steps: int = MAX_STEPS, rpm: int = GEMINI_RPM):
        self.verbose = verbose
        self.max_steps = max_steps
        self.rpm = rpm

    def search_lead(
        self,
        company: str,
        company_url: Optional[str] = None,
        location: Optional[str] = None,
        title: Optional[str] = None,
        job_url: Optional[str] = None,
    ) -> str:
        """
        Executes company intelligence & executive email discovery using the exact
        high-precision prompt format demonstrated in test2.py.
        """
        query = (
            f"Find CEO / CTO / Founder names, LinkedIn profiles, and emails for this company.\n"
            f"Company: {company}\n"
            f"LinkedIn company URL: {company_url or 'N/A'}\n"
            f"Location: {location or 'N/A'}\n"
            f"Job title: {title or 'N/A'}\n"
            f"Job URL: {job_url or 'N/A'}\n"
            f"\n"
            f"Email rules (strict order):\n"
            f"1) Find PERSONAL emails publicly listed on the internet first.\n"
            f"2) Then find BUSINESS emails that literally appear on the internet.\n"
            f"3) Only if both fail, report INFERRED corporate-pattern emails as unconfirmed.\n"
            f"Never invent LinkedIn URLs or emails. Label each email PERSONAL / BUSINESS / INFERRED / NOT_FOUND.\n"
        )
        return self.search(query)

    def search(self, user_question: str) -> str:
        """Runs the ReAct LLM ↔ DDGS loop until ANSWER or max_steps reached."""
        llm = FallbackChatLLM(verbose=self.verbose)
        first_message = f"{SYSTEM_PROMPT}\n\nQuestion: {user_question}"
        searches_done = 0
        email_searches = 0
        inferred_nudge_used = False

        def _log(role: str, text: str):
            if not self.verbose:
                return
            sep = "─" * 60
            print(f"\n┌{sep}")
            print(f"│ {role}")
            print(f"├{sep}")
            for line in text.strip().split("\n"):
                print(f"│  {line}")
            print(f"└{sep}")

        def _step(message: str, depth: int = 0) -> str:
            nonlocal searches_done, email_searches, inferred_nudge_used
            if depth >= self.max_steps:
                return "Max search steps reached. Could not find a definitive answer."

            llm_reply = llm.send(message)
            _log(f"🤖 {llm.label} (step {depth + 1})", llm_reply)

            action, payload = _extract_action(llm_reply)

            if action == "answer":
                if searches_done == 0:
                    nudge = (
                        "You have not searched yet. Emit ONLY:\n"
                        "SEARCH: <query to find executives and emails>\n"
                    )
                    return _step(nudge, depth + 1)
                if not inferred_nudge_used and _premature_inferred_answer(payload, email_searches):
                    inferred_nudge_used = True
                    nudge = (
                        "Do not guess corporate email patterns yet.\n"
                        "First SEARCH for an exact PERSONAL email on the public web "
                        "(gmail/outlook/yahoo/etc.), then a BUSINESS email that appears "
                        "on a real page. Emit ONLY:\n"
                        'SEARCH: "<Full Name>" email (gmail.com OR outlook.com OR "@domain.com")\n'
                    )
                    return _step(nudge, depth + 1)
                _log("✅ FINAL ANSWER", payload)
                return payload

            if action == "search":
                _log("🔍 Searching DDGS", payload)
                search_results = _ddgs_search(payload)
                searches_done += 1
                if _is_email_search(payload):
                    email_searches += 1
                _log("📄 DDGS Results", search_results)
                follow_up = (
                    f"Search results for '{payload}':\n\n"
                    f"{search_results}\n\n"
                    "Next step rules:\n"
                    "- If you have executive names but no PERSONAL or BUSINESS email yet, "
                    "SEARCH for that person's exact email (Tier 1 personal, then Tier 2 business).\n"
                    "- Do not ANSWER with INFERRED corporate guesses until Tier-1 and Tier-2 "
                    "email searches were tried.\n"
                    "- Never invent LinkedIn URLs.\n"
                    "Emit exactly one of SEARCH: or ANSWER:."
                )
                return _step(follow_up, depth + 1)

            nudge = (
                "Respond with exactly one line starting with either:\n"
                "  SEARCH: <query>\n"
                "  ANSWER: <final answer>\n"
            )
            return _step(nudge, depth + 1)

        if self.verbose:
            nvidia_mod = settings.nvidia_model or NVIDIA_FALLBACK_MODEL
            keys_info = f"{len(llm._google_keys)} Google Key(s)"
            print(
                f"LLM: Gemini ({keys_info}, RPM≤{self.rpm}); models: "
                f"{' → '.join(GEMINI_MODELS)} → NVIDIA ({nvidia_mod})"
            )

        return _step(first_message)

    def parse_contacts(self, answer_text: str) -> List[Dict[str, Any]]:
        """
        Parses structured contact info from the final answer text.
        Extracts names, roles, LinkedIn URLs, emails, and verification status.
        """
        contacts: List[Dict[str, Any]] = []
        email_pattern = re.compile(r"[\w\.-]+@[\w\.-]+\.\w+")
        linkedin_pattern = re.compile(r"https?://(?:www\.)?linkedin\.com/in/[\w-]+/?", re.IGNORECASE)

        lines = answer_text.split("\n")
        current_name = None
        current_role = None
        current_linkedin = None

        for line in lines:
            line_str = line.strip()
            if not line_str:
                continue

            # Check for LinkedIn URL
            li_match = linkedin_pattern.search(line_str)
            if li_match:
                current_linkedin = li_match.group(0)

            # Check for email line
            if any(k in line_str.upper() for k in ["PERSONAL:", "BUSINESS:", "INFERRED:", "EMAIL:"]):
                em_match = email_pattern.search(line_str)
                email_val = em_match.group(0) if em_match else None
                is_inferred = "INFERRED:" in line_str.upper()
                is_verified = bool(email_val and not is_inferred)

                if email_val or current_name:
                    contacts.append({
                        "name": current_name or "Decision Maker",
                        "role": current_role or "Executive",
                        "email": email_val,
                        "linkedin_url": current_linkedin,
                        "confidence_score": 85 if is_verified else 50,
                        "is_verified": is_verified,
                        "verification_status": "valid" if is_verified else "unconfirmed",
                    })
                current_name = None
                current_role = None
                current_linkedin = None
            else:
                # Look for name / role heuristic e.g. "Name: ...", "CEO: ...", "Founder: ..."
                m_role = re.match(r"^(?:[-*]\s*)?(CEO|CTO|Founder|Co-Founder|COO|Director|Owner|President)[:\s]+(.*)", line_str, re.IGNORECASE)
                if m_role:
                    current_role = m_role.group(1)
                    current_name = m_role.group(2).split("-")[0].split("(")[0].strip()

        return contacts


def cot_search(user_question: str, verbose: bool = True) -> str:
    """Convenience module function matching existing import signatures."""
    agent = CotSearchAgent(verbose=verbose)
    return agent.search(user_question)


if __name__ == "__main__":
    question = input("Ask anything: ").strip()
    if not question:
        question = "Who is the CEO of OpenAI and what is their email or contact info?"

    agent = CotSearchAgent(verbose=True)
    final = agent.search(question)
    print("\n" + "=" * 60)
    print("FINAL ANSWER:\n")
    print(final)
