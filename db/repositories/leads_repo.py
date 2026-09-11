"""Leads Repository for SQLite enriched leads operations."""

import json
import re
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional
import sqlite3

from core.company_filter import classify_company_size
from core.exceptions import DatabaseException
from core.logging import logger
from db.models import EnrichedLead


class LeadsRepository:
    def __init__(self, get_connection: Callable[[], sqlite3.Connection]):
        self.get_connection = get_connection

    def _format_lead_row(self, r: sqlite3.Row) -> Dict[str, Any]:
        """Convert a SQLite Row into a dictionary compatible with EnrichedLead JSON structure."""
        doc = dict(r)
        # Parse JSON fields
        for json_col in ("contacts", "key_technologies", "search_queries_used"):
            val = doc.get(json_col)
            if isinstance(val, str):
                try:
                    doc[json_col] = json.loads(val)
                except Exception:
                    doc[json_col] = []
            elif val is None:
                doc[json_col] = []

        # Boolean conversion
        doc["is_valid_lead"] = bool(doc.get("is_valid_lead", 1))
        doc["_id"] = str(doc.get("id", ""))
        doc["date_posted"] = doc.get("date_posted")
        doc["scraped_at"] = doc.get("scraped_at") or doc.get("created_at")
        return doc

    def upsert_enriched_lead(self, lead: EnrichedLead) -> bool:
        """Save or update enriched lead in SQLite."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            contacts_json = json.dumps([c.model_dump() for c in lead.contacts], default=str)
            tech_json = json.dumps(lead.key_technologies or [], default=str)
            queries_json = json.dumps(lead.search_queries_used or [], default=str)
            now_iso = datetime.utcnow().isoformat()
            created_at_iso = lead.created_at.isoformat() if isinstance(lead.created_at, datetime) else now_iso

            date_posted_str = str(lead.date_posted) if lead.date_posted else None
            scraped_at_str = lead.scraped_at.isoformat() if isinstance(lead.scraped_at, datetime) else (str(lead.scraped_at) if lead.scraped_at else now_iso)

            scheduled_job_id_val = getattr(lead, "scheduled_job_id", None)

            scraped_day = (scraped_at_str or now_iso)[:10]
            try:
                from datetime import date as date_cls, timedelta
                from config.settings import settings
                delay = int(settings.outreach_first_send_delay_days)
                mode = (settings.outreach_default_mode or "auto").strip().lower()
                state = (settings.outreach_default_state or "open").strip().lower()
                if mode not in ("auto", "manual"):
                    mode = "auto"
                if state not in ("open", "closed"):
                    state = "open"
                # Only schedule drip when a verified email exists
                has_verified = any(
                    bool(getattr(c, "is_verified", False))
                    and (getattr(c, "email", None) or "")
                    and "@" in (c.email or "")
                    for c in (lead.contacts or [])
                )
                next_send_default = (
                    (date_cls.fromisoformat(scraped_day) + timedelta(days=delay)).isoformat()
                    if has_verified
                    else None
                )
            except Exception:
                mode, state = "auto", "open"
                next_send_default = None

            cur.execute("""
                INSERT INTO enriched_leads (
                    job_url, title, company, site, location, job_type, job_description,
                    is_valid_lead, relevance_score, company_domain, company_summary,
                    company_size, contacts, key_technologies, hiring_urgency,
                    lead_summary, agent_thinking_process, search_queries_used,
                    status, lead_type, date_posted, scraped_at, created_at, updated_at,
                    scheduled_job_id,
                    outreach_mode, outreach_state, outreach_stage, next_send_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(job_url) DO UPDATE SET
                    title=excluded.title,
                    company=excluded.company,
                    site=excluded.site,
                    location=excluded.location,
                    job_type=excluded.job_type,
                    job_description=excluded.job_description,
                    is_valid_lead=excluded.is_valid_lead,
                    relevance_score=excluded.relevance_score,
                    company_domain=excluded.company_domain,
                    company_summary=excluded.company_summary,
                    company_size=excluded.company_size,
                    contacts=excluded.contacts,
                    key_technologies=excluded.key_technologies,
                    hiring_urgency=excluded.hiring_urgency,
                    lead_summary=excluded.lead_summary,
                    agent_thinking_process=excluded.agent_thinking_process,
                    search_queries_used=excluded.search_queries_used,
                    status=excluded.status,
                    lead_type=excluded.lead_type,
                    scheduled_job_id=COALESCE(excluded.scheduled_job_id, enriched_leads.scheduled_job_id),
                    date_posted=COALESCE(excluded.date_posted, enriched_leads.date_posted),
                    scraped_at=COALESCE(excluded.scraped_at, enriched_leads.scraped_at),
                    updated_at=?
            """, (
                lead.job_url,
                lead.title,
                lead.company,
                lead.site,
                lead.location,
                lead.job_type,
                lead.job_description,
                1 if lead.is_valid_lead else 0,
                lead.relevance_score,
                lead.company_domain,
                lead.company_summary,
                lead.company_size,
                contacts_json,
                tech_json,
                lead.hiring_urgency,
                lead.lead_summary,
                lead.agent_thinking_process,
                queries_json,
                lead.status,
                lead.lead_type or "others",
                date_posted_str,
                scraped_at_str,
                created_at_iso,
                now_iso,
                scheduled_job_id_val,
                mode,
                state,
                next_send_default,
                now_iso,
            ))
            conn.commit()
            logger.info(f"Saved enriched lead in SQLite: '{lead.title}' at {lead.company}")
            return True
        except Exception as e:
            logger.error(f"Error upserting lead {lead.job_url} to SQLite: {e}")
            raise DatabaseException(f"Failed to upsert lead to SQLite: {e}") from e
        finally:
            conn.close()

    def get_leads(
        self,
        search: Optional[str] = None,
        site: Optional[str] = None,
        status: Optional[str] = None,
        lead_type: Optional[str] = None,
        company_size: Optional[str] = None,
        job_type: Optional[str] = None,
        hours_old: Optional[int] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        date_field: Optional[str] = "any",
        min_score: int = 0,
        has_contacts: Optional[bool] = None,
        scheduled_job_id: Optional[str] = None,
        limit: int = 50,
        page: int = 1,
    ) -> Dict[str, Any]:
        """Retrieve filtered, paginated list of enriched leads from SQLite."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            conditions = ["enriched_leads.is_valid_lead = 1"]
            params = []

            if scheduled_job_id and scheduled_job_id.lower() != "all":
                conditions.append("enriched_leads.scheduled_job_id = ?")
                params.append(scheduled_job_id.strip())

            if min_score > 0:
                conditions.append("enriched_leads.relevance_score >= ?")
                params.append(min_score)

            if site and site.lower() != "all":
                conditions.append("LOWER(enriched_leads.site) = ?")
                params.append(site.lower().strip())

            if status and status.lower() != "all":
                conditions.append("LOWER(enriched_leads.status) = ?")
                params.append(status.lower().strip())

            if lead_type and lead_type.lower() != "all":
                conditions.append("COALESCE(LOWER(enriched_leads.lead_type), 'others') = ?")
                params.append(lead_type.lower().strip())

            if has_contacts is True:
                conditions.append("(enriched_leads.contacts IS NOT NULL AND enriched_leads.contacts != '[]' AND enriched_leads.contacts != '')")

            if hours_old and hours_old > 0:
                cutoff = (datetime.utcnow() - timedelta(hours=hours_old)).isoformat()
                conditions.append("enriched_leads.created_at >= ?")
                params.append(cutoff)

            if date_from or date_to:
                df = (date_field or "any").lower().strip()
                if df == "posted":
                    if date_from:
                        conditions.append("enriched_leads.date_posted >= ?")
                        params.append(date_from)
                    if date_to:
                        conditions.append("enriched_leads.date_posted <= ?")
                        params.append(date_to)
                elif df in ("scraped", "added"):
                    if date_from:
                        conditions.append("substr(COALESCE(enriched_leads.scraped_at, enriched_leads.created_at), 1, 10) >= ?")
                        params.append(date_from)
                    if date_to:
                        conditions.append("substr(COALESCE(enriched_leads.scraped_at, enriched_leads.created_at), 1, 10) <= ?")
                        params.append(date_to)
                else:  # any
                    sub = []
                    sub_p = []
                    if date_from and date_to:
                        sub.append("(enriched_leads.date_posted IS NOT NULL AND enriched_leads.date_posted != '' AND enriched_leads.date_posted >= ? AND enriched_leads.date_posted <= ?)")
                        sub_p.extend([date_from, date_to])
                    elif date_from:
                        sub.append("(enriched_leads.date_posted IS NOT NULL AND enriched_leads.date_posted != '' AND enriched_leads.date_posted >= ?)")
                        sub_p.append(date_from)
                    elif date_to:
                        sub.append("(enriched_leads.date_posted IS NOT NULL AND enriched_leads.date_posted != '' AND enriched_leads.date_posted <= ?)")
                        sub_p.append(date_to)

                    if date_from and date_to:
                        sub.append("(substr(COALESCE(enriched_leads.scraped_at, enriched_leads.created_at), 1, 10) >= ? AND substr(COALESCE(enriched_leads.scraped_at, enriched_leads.created_at), 1, 10) <= ?)")
                        sub_p.extend([date_from, date_to])
                    elif date_from:
                        sub.append("(substr(COALESCE(enriched_leads.scraped_at, enriched_leads.created_at), 1, 10) >= ?)")
                        sub_p.append(date_from)
                    elif date_to:
                        sub.append("(substr(COALESCE(enriched_leads.scraped_at, enriched_leads.created_at), 1, 10) <= ?)")
                        sub_p.append(date_to)

                    if sub:
                        conditions.append(f"({' OR '.join(sub)})")
                        params.extend(sub_p)

            if company_size and company_size.lower() not in ("all", "any"):
                c_category = classify_company_size(company_size)
                c_size = c_category if c_category != "unknown" else company_size.lower().strip()
                if c_size == "small":
                    conditions.append("""(
                        (
                            enriched_leads.company_size LIKE '%1-10%' OR
                            enriched_leads.company_size LIKE '%2-10%' OR
                            enriched_leads.company_size LIKE '%11-50%' OR
                            enriched_leads.company_size LIKE '%1-50%' OR
                            enriched_leads.company_size LIKE '%1-20%' OR
                            LOWER(enriched_leads.company_size) LIKE '%startup%' OR
                            LOWER(enriched_leads.company_size) LIKE '%seed%' OR
                            LOWER(enriched_leads.company_size) LIKE '%micro%' OR
                            LOWER(enriched_leads.company_size) LIKE '%boutique%'
                        ) AND NOT (
                            enriched_leads.company_size LIKE '%51-200%' OR
                            enriched_leads.company_size LIKE '%201-500%' OR
                            enriched_leads.company_size LIKE '%501-1000%' OR
                            enriched_leads.company_size LIKE '%500+%' OR
                            enriched_leads.company_size LIKE '%1000+%' OR
                            LOWER(enriched_leads.company_size) LIKE '%enterprise%'
                        )
                    )""")
                elif c_size == "medium":
                    conditions.append("""(
                        enriched_leads.company_size LIKE '%51-200%' OR
                        enriched_leads.company_size LIKE '%201-500%' OR
                        enriched_leads.company_size LIKE '%501-1000%' OR
                        enriched_leads.company_size LIKE '%201-1000%' OR
                        enriched_leads.company_size LIKE '%200-500%' OR
                        LOWER(enriched_leads.company_size) LIKE '%medium%' OR
                        LOWER(enriched_leads.company_size) LIKE '%mid%'
                    )""")
                elif c_size == "large":
                    conditions.append("""(
                        enriched_leads.company_size LIKE '%500+%' OR
                        enriched_leads.company_size LIKE '%1000+%' OR
                        enriched_leads.company_size LIKE '%5000+%' OR
                        enriched_leads.company_size LIKE '%10000+%' OR
                        LOWER(enriched_leads.company_size) LIKE '%enterprise%' OR
                        LOWER(enriched_leads.company_size) LIKE '%corporation%' OR
                        LOWER(enriched_leads.company_size) LIKE '%corporate%' OR
                        LOWER(enriched_leads.company_size) LIKE '%fortune%'
                    )""")

            if job_type and job_type.lower() != "all":
                jt = job_type.lower().strip()
                if jt in ("contract", "freelance"):
                    conditions.append("""(
                        LOWER(COALESCE(enriched_leads.job_type, '')) LIKE '%contract%' OR
                        LOWER(COALESCE(enriched_leads.job_type, '')) LIKE '%freelance%' OR
                        LOWER(COALESCE(enriched_leads.job_type, '')) LIKE '%c2c%' OR
                        LOWER(COALESCE(enriched_leads.job_type, '')) LIKE '%corp%' OR
                        LOWER(COALESCE(enriched_leads.job_type, '')) LIKE '%gig%' OR
                        LOWER(COALESCE(enriched_leads.job_type, '')) LIKE '%part-time%' OR
                        LOWER(COALESCE(enriched_leads.job_type, '')) LIKE '%outside ir35%' OR
                        LOWER(enriched_leads.title) LIKE '%contract%' OR
                        LOWER(enriched_leads.title) LIKE '%freelance%' OR
                        LOWER(enriched_leads.title) LIKE '%c2c%' OR
                        LOWER(enriched_leads.title) LIKE '%gig%' OR
                        LOWER(enriched_leads.title) LIKE '%outside ir35%'
                    )""")
                else:
                    conditions.append("LOWER(COALESCE(enriched_leads.job_type, '')) LIKE ?")
                    params.append(f"%{jt}%")

            if search and search.strip():
                term = f"%{search.strip().lower()}%"
                conditions.append("""(
                    LOWER(enriched_leads.title) LIKE ? OR
                    LOWER(enriched_leads.company) LIKE ? OR
                    LOWER(COALESCE(enriched_leads.key_technologies, '')) LIKE ? OR
                    LOWER(COALESCE(enriched_leads.contacts, '')) LIKE ?
                )""")
                params.extend([term, term, term, term])

            where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

            # Count total matching
            count_sql = f"SELECT COUNT(*) FROM enriched_leads {where_clause}"
            cur.execute(count_sql, params)
            total = cur.fetchone()[0]

            # Fetch page items
            offset = (page - 1) * limit
            data_sql = f"""
                SELECT 
                    enriched_leads.*,
                    COALESCE(enriched_leads.date_posted, rj.date_posted, jl.date_posted) AS resolved_date_posted,
                    COALESCE(enriched_leads.scraped_at, rj.scraped_at, jl.created_at, enriched_leads.created_at) AS resolved_scraped_at
                FROM enriched_leads 
                LEFT JOIN raw_jobs rj ON enriched_leads.job_url = rj.job_url
                LEFT JOIN job_leads jl ON enriched_leads.job_url = jl.job_url
                {where_clause}
                ORDER BY enriched_leads.created_at DESC 
                LIMIT ? OFFSET ?
            """
            cur.execute(data_sql, params + [limit, offset])
            rows = cur.fetchall()

            leads = []
            for r in rows:
                leads.append(self._format_lead_row(r))

            # Compute breakdown counts for company, personal, others and total
            type_counts = {"all": 0, "company": 0, "personal": 0, "others": 0}
            try:
                cur.execute("""
                    SELECT COALESCE(LOWER(lead_type), 'others') as lt, COUNT(*) 
                    FROM enriched_leads 
                    GROUP BY COALESCE(LOWER(lead_type), 'others')
                """)
                for crow in cur.fetchall():
                    ctype = (crow[0] or "others").lower()
                    if ctype in type_counts:
                        type_counts[ctype] = crow[1]
                cur.execute("SELECT COUNT(*) FROM enriched_leads")
                all_count_row = cur.fetchone()
                type_counts["all"] = all_count_row[0] if all_count_row else 0
            except Exception as te:
                logger.warning(f"Could not calculate lead type counts: {te}")

            return {
                "total": total,
                "page": page,
                "limit": limit,
                "total_pages": (total + limit - 1) // limit if limit else 1,
                "leads": leads,
                "type_counts": type_counts,
            }
        except Exception as e:
            logger.error(f"Error fetching leads from SQLite: {e}")
            raise DatabaseException(f"Failed to query leads: {e}") from e
        finally:
            conn.close()

    def get_lead_by_url(self, job_url: str) -> Optional[Dict[str, Any]]:
        """Retrieve single enriched lead by job_url."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT 
                    enriched_leads.*,
                    COALESCE(enriched_leads.date_posted, rj.date_posted, jl.date_posted) AS resolved_date_posted,
                    COALESCE(enriched_leads.scraped_at, rj.scraped_at, jl.created_at, enriched_leads.created_at) AS resolved_scraped_at
                FROM enriched_leads 
                LEFT JOIN raw_jobs rj ON enriched_leads.job_url = rj.job_url
                LEFT JOIN job_leads jl ON enriched_leads.job_url = jl.job_url
                WHERE enriched_leads.job_url = ? LIMIT 1
            """, (job_url,))
            row = cur.fetchone()
            if not row:
                return None
            return self._format_lead_row(row)
        finally:
            conn.close()

    def update_lead_status(self, job_url: str, status: str) -> bool:
        """Update lead status in SQLite."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            now_iso = datetime.utcnow().isoformat()
            cur.execute("""
                UPDATE enriched_leads 
                SET status = ?, updated_at = ? 
                WHERE job_url = ?
            """, (status, now_iso, job_url))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def update_lead_type(self, job_url: str, lead_type: str) -> bool:
        """Update lead type classification (company, personal, others) in SQLite."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            now_iso = datetime.utcnow().isoformat()
            cur.execute("""
                UPDATE enriched_leads 
                SET lead_type = ?, updated_at = ? 
                WHERE job_url = ?
            """, (lead_type, now_iso, job_url))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def update_lead_outreach(
        self,
        job_url: str,
        outreach_mode: Optional[str] = None,
        outreach_state: Optional[str] = None,
        outreach_stage: Optional[int] = None,
        next_send_at: Optional[str] = None,
        last_sent_at: Optional[str] = None,
        clear_next_send_at: bool = False,
    ) -> bool:
        """Update per-lead outreach automation fields. Only provided fields are changed."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            sets = ["updated_at = ?"]
            params: List[Any] = [datetime.utcnow().isoformat()]
            if outreach_mode is not None:
                sets.append("outreach_mode = ?")
                params.append(outreach_mode)
            if outreach_state is not None:
                sets.append("outreach_state = ?")
                params.append(outreach_state)
                if str(outreach_state).lower() == "closed":
                    clear_next_send_at = True
            if outreach_stage is not None:
                sets.append("outreach_stage = ?")
                params.append(int(outreach_stage))
            if clear_next_send_at:
                sets.append("next_send_at = NULL")
            elif next_send_at is not None:
                sets.append("next_send_at = ?")
                params.append(next_send_at)
            if last_sent_at is not None:
                sets.append("last_sent_at = ?")
                params.append(last_sent_at)
            if len(sets) <= 1:
                return False
            params.append(job_url)
            cur.execute(
                f"UPDATE enriched_leads SET {', '.join(sets)} WHERE job_url = ?",
                params,
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def get_due_outreach_leads(self, today_date_str: str) -> List[Dict[str, Any]]:
        """Leads due for automated mail: auto + open + next_send_at <= today + company/personal."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT * FROM enriched_leads
                WHERE is_valid_lead = 1
                  AND COALESCE(LOWER(outreach_mode), 'manual') = 'auto'
                  AND COALESCE(LOWER(outreach_state), 'open') = 'open'
                  AND next_send_at IS NOT NULL
                  AND substr(next_send_at, 1, 10) <= ?
                  AND COALESCE(LOWER(lead_type), 'others') IN ('company', 'personal')
                  AND contacts IS NOT NULL AND contacts != '[]' AND contacts != ''
                ORDER BY next_send_at ASC
                """,
                (today_date_str,),
            )
            return [self._format_lead_row(r) for r in cur.fetchall()]
        finally:
            conn.close()

    def delete_lead(self, job_url: str) -> bool:
        """Delete lead from enriched_leads table."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM enriched_leads WHERE job_url = ?", (job_url,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def get_recipients(self, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Fetch email campaign recipients from enriched_leads and job_leads in SQLite."""
        conn = self.get_connection()
        filters = filters or {}
        country_filter = (filters.get("country") or "").strip().lower()
        lead_type_filter = (filters.get("lead_type") or "").strip().lower()

        # Date filter resolution
        preset = (filters.get("date_preset") or "").strip().lower()
        date_from = (filters.get("date_from") or "").strip()
        date_to = (filters.get("date_to") or "").strip()
        date_field = (filters.get("date_field") or "any").strip().lower()

        if preset and preset not in ("custom", "all"):
            today = datetime.utcnow().date()
            if preset in ("today", "24h"):
                date_from = today.isoformat()
                date_to = today.isoformat()
            elif preset == "7d":
                date_from = (today - timedelta(days=7)).isoformat()
                date_to = today.isoformat()
            elif preset == "14d":
                date_from = (today - timedelta(days=14)).isoformat()
                date_to = today.isoformat()
            elif preset == "30d":
                date_from = (today - timedelta(days=30)).isoformat()
                date_to = today.isoformat()
            elif preset == "90d":
                date_from = (today - timedelta(days=90)).isoformat()
                date_to = today.isoformat()

        def matches_date(dp: Optional[str], sa: Optional[str], ca: Optional[str]) -> bool:
            if not date_from and not date_to:
                return True
            dp_clean = (dp or "").strip()[:10]
            sa_clean = (sa or "").strip()[:10]
            ca_clean = (ca or "").strip()[:10]

            def in_range(val: str) -> bool:
                if not val or len(val) < 10:
                    return False
                if date_from and val < date_from:
                    return False
                if date_to and val > date_to:
                    return False
                return True

            if date_field == "posted":
                return in_range(dp_clean)
            elif date_field in ("scraped", "added"):
                return in_range(sa_clean) or in_range(ca_clean)
            else:  # any
                return in_range(dp_clean) or in_range(sa_clean) or in_range(ca_clean)

        recipients = []
        seen_emails = set()

        try:
            cur = conn.cursor()
            # 1. From enriched_leads
            cur.execute("""
                SELECT company, company_domain, location, contacts, COALESCE(lead_type, 'others') as lead_type,
                       date_posted, scraped_at, created_at, updated_at
                FROM enriched_leads 
                WHERE contacts IS NOT NULL AND contacts != '[]'
            """)
            for row in cur.fetchall():
                loc = row["location"] or ""
                if country_filter and country_filter not in loc.lower():
                    continue

                row_lt = (row["lead_type"] or "others").strip("'\" ").lower()
                if lead_type_filter and lead_type_filter != "all" and row_lt != lead_type_filter:
                    continue

                if not matches_date(row["date_posted"], row["scraped_at"], row["created_at"]):
                    continue

                try:
                    contacts = json.loads(row["contacts"])
                except Exception:
                    contacts = []

                # Date when the job was scraped
                lead_date = (row["scraped_at"][:10] if row["scraped_at"] else (row["created_at"][:10] if row["created_at"] else (row["date_posted"] or "")))
                for c in contacts:
                    email = (c.get("email") or "").strip()
                    if email and email.lower() not in seen_emails:
                        seen_emails.add(email.lower())
                        domain = row["company_domain"] or ""
                        recipients.append({
                            "person_name":  c.get("name") or "Leadership / Contact",
                            "role":         c.get("role") or "Professional",
                            "email":        email,
                            "company_name": row["company"] or "",
                            "website":      f"https://{domain}" if domain and not domain.startswith("http") else domain,
                            "city":         "",
                            "country":      loc,
                            "category":     "Job Lead",
                            "source":       "job_leads",
                            "lead_type":    row_lt,
                            "date":         lead_date,
                            "scraped_at":   row["scraped_at"] or row["created_at"] or None,
                            "created_at":   row["created_at"] or None,
                            "date_posted":  row["date_posted"] or None,
                        })

            # 2. From job_leads (historical / legacy scraped data)
            if not lead_type_filter or lead_type_filter in ("all", "others"):
                cur.execute("""
                    SELECT company, company_website, location, emails, phones, recruiter_name, title,
                           date_posted, created_at, updated_at
                    FROM job_leads
                    WHERE emails IS NOT NULL AND emails != '' AND emails != 'None'
                """)
                for row in cur.fetchall():
                    loc = row["location"] or ""
                    if country_filter and country_filter not in loc.lower():
                        continue

                    if not matches_date(row["date_posted"], None, row["created_at"]):
                        continue

                    lead_date = (row["created_at"][:10] if row["created_at"] else (row["date_posted"] or ""))
                    raw_emails = row["emails"] or ""
                    for em in re.split(r"[,;\s]+", raw_emails):
                        email = em.strip()
                        if email and "@" in email and email.lower() not in seen_emails:
                            seen_emails.add(email.lower())
                            site = row["company_website"] or ""
                            recipients.append({
                                "person_name":  row["recruiter_name"] or "Hiring Manager",
                                "role":         f"Recruiter / Hiring for {row['title']}" if row['title'] else "Hiring Manager",
                                "email":        email,
                                "company_name": row["company"] or "",
                                "website":      site if site.startswith("http") else (f"https://{site}" if site else ""),
                                "city":         "",
                                "country":      loc,
                                "category":     "Job Lead",
                                "source":       "job_leads",
                                "lead_type":    "others",
                                "date":         lead_date,
                                "scraped_at":   row["created_at"] or None,
                                "created_at":   row["created_at"] or None,
                                "date_posted":  row["date_posted"] or None,
                            })

            return recipients
        except Exception as e:
            logger.warning(f"Error fetching recipients from SQLite job leads: {e}")
            return recipients
        finally:
            conn.close()
