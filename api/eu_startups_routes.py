"""Backward-compatible proxy router for EU Startups API.

The routes have been modularized into feature submodules under `api.eu_startups`:
- `api.eu_startups.directory`: Directory search, stats, options, and manual addition
- `api.eu_startups.enrichment`: Batch and single startup enrichment
- `api.eu_startups.discovery`: Web discovery
- `api.eu_startups.agent_leads`: Agent leads ingestion & presence checking

This file re-exports `router` from `api.eu_startups` for 100% backward compatibility.
"""

from api.eu_startups import router

__all__ = ["router"]
