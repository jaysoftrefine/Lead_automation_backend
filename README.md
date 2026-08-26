# 🚀 Autonomous B2B Job & Freelance Lead Generation Engine

A modular, production-ready Lead Generation and Enrichment platform that automatically scrapes job postings from **LinkedIn**, **Naukri**, and other major platforms, executes multi-step research with an autonomous **LLM Thinking Agent** equipped with **Tavily Web Search**, finds verified recruiter/decision-maker emails, phone numbers, and company intelligence, and persists strictly structured leads into **MongoDB**.

---

## 🏗️ Architecture Overview

```
Lead_gen/
├── config/
│   └── settings.py          # Pydantic Settings & environment loader (.env)
├── core/
│   ├── logging.py           # Structured logging with Loguru/Rich
│   └── exceptions.py        # Centralized domain exceptions
├── db/
│   ├── models.py            # Pydantic schemas (RawJobPosting, ContactPerson, EnrichedLead)
│   └── mongo.py             # MongoDB connection manager, indexes, and upsert helpers
├── scraper/
│   ├── base.py              # BaseScraper interface
│   └── jobspy_scraper.py    # python-jobspy scraper for LinkedIn, Naukri, Indeed, Glassdoor
├── llm/
│   ├── base.py              # Abstract BaseLLMProvider interface
│   ├── registry.py          # Dynamic LLM provider registry / factory
│   └── providers/
│       ├── gemini.py        # Google Gemini provider (ChatGoogleGenerativeAI)
│       └── nvidia.py        # NVIDIA NIM provider (ChatNVIDIA)
├── enrichment/
│   ├── schemas.py           # Strict structured output Pydantic schemas (ExtractedLeadData)
│   ├── prompts.py           # Strict reasoning & thinking agent system prompts
│   ├── tools/
│   │   └── web_search.py    # Tavily Web Search Tool
│   └── agent.py             # Autonomous tool-calling thinking agent
├── pipeline/
│   └── orchestrator.py      # End-to-end pipeline: Scrape -> Deduplicate -> Enrich -> Store
├── main.py                  # Full-featured CLI interface
├── requirements.txt         # Project dependencies
└── .env.example             # Configuration template
```

---

## ⚡ Key Features

1. **Multi-Source Scraping**:
   - Uses `python-jobspy` to scrape LinkedIn and Naukri.
   - Clean normalization of titles, descriptions, companies, salaries, and posting dates.

2. **Modular LLM Architecture**:
   - `BaseLLMProvider` contract enabling easy plug-and-play LLM providers.
   - Built-in support for **Google Gemini** (`gemini-2.5-flash`, `gemini-2.0-flash`, `gemini-1.5-pro`) and **NVIDIA NIM** (`meta/llama-3.3-70b-instruct`, etc.).
   - Model switching via CLI flag (`--provider gemini` or `--provider nvidia`) or `.env`.

3. **Autonomous Thinking & Research Agent**:
   - Step-by-step reasoning prompt that analyzes the job posting and formulates search queries.
   - **Tavily Web Search Tool** to locate company domains, recruiter profiles, hiring managers, email patterns, and direct contact numbers.
   - Assigns confidence scores (0-100) to discovered contacts.
   - Records the complete internal thinking and reasoning trail in `agent_thinking_process`.

4. **Strict Structured Output & Validation**:
   - Uses Pydantic to enforce schema compliance with zero hallucinations or malformed JSON.

5. **Production MongoDB Integration**:
   - Automatic unique index on `job_url` preventing duplicate records.
   - Idempotent upsert operations.
   - Separate storage for raw scraped jobs and enriched leads.

---

## 🛠️ Setup & Installation

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure Environment Variables
Copy `.env.example` to `.env` and fill in your API keys:
```bash
cp .env.example .env
```

Edit `.env`:
```ini
MONGODB_URI=mongodb://localhost:27017
MONGODB_DB_NAME=lead_gen_db

# LLM Selection ("gemini" or "nvidia")
DEFAULT_LLM_PROVIDER=gemini

# Google Gemini
GOOGLE_API_KEY=your_google_api_key
GEMINI_MODEL=gemini-2.5-flash

# NVIDIA NIM
NVIDIA_API_KEY=your_nvidia_api_key
NVIDIA_MODEL=meta/llama-3.3-70b-instruct

# Tavily Web Search
TAVILY_API_KEY=your_tavily_api_key
```

---

## 🖥️ CLI Usage

### Run End-to-End Pipeline
Scrape LinkedIn & Naukri, enrich each lead via LLM + Tavily, and store in MongoDB:
```bash
python main.py run --search "Python Backend Developer" --location "Remote" --sites linkedin,naukri --limit 10
```

Use NVIDIA NIM instead of Gemini:
```bash
python main.py run --search "AI Engineer" --location "Remote" --provider nvidia --limit 5
```

### Start the Web Dashboard & FastAPI Server
Launch the interactive web UI and API on http://localhost:8000:
```bash
python main.py serve
# Or directly:
python app.py
```

### Test MongoDB Connection & Counts
```bash
python main.py test-db
```

### View Enriched Leads in Database
```bash
python main.py list-leads --limit 20 --min-score 50
```

### Test LLM Thinking Agent on a Sample Job (Without Scraping)
```bash
python main.py test-enrichment --company "Stripe" --title "Senior Infrastructure Engineer"
```

---

## 🌐 FastAPI REST API Endpoints

- `GET /` - Interactive LeadPulse AI Web UI Dashboard
- `GET /docs` - Interactive OpenAPI / Swagger documentation
- `GET /api/stats` - Live database and engine stats
- `GET /api/leads` - Filtered & paginated enriched leads
- `GET /api/lead?url=...` - Lead details & full thinking trail
- `POST /api/pipeline/run` - Trigger scraping & enrichment pipeline in the background
- `GET /api/pipeline/status` - Live pipeline status & streaming logs
- `POST /api/pipeline/stop` - Stop active pipeline
- `POST /api/pipeline/test-enrichment` - Instant single-job agent enrichment test
- `GET /api/export/csv` - 1-Click CSV export of discovered leads & contacts

---

## 🧩 Extensibility: Adding a New LLM Provider

To add a new LLM (e.g. Anthropic / OpenAI / Ollama), simply inherit from `BaseLLMProvider`:

```python
from llm.base import BaseLLMProvider
from llm.registry import LLMProviderRegistry

class CustomProvider(BaseLLMProvider):
    def get_chat_model(self):
        # return LangChain chat model
        pass
        
    def get_structured_llm(self, schema):
        # return model.with_structured_output(schema)
        pass

# Register it dynamically:
LLMProviderRegistry.register_provider("custom", CustomProvider)
```
