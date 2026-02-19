import json
import os
from pathlib import Path
from typing import Optional

from leads_engine.models import Lead, LeadStatus

DB_FILE = Path(os.getenv("LEADS_DB_DIR", "leads_database")) / "leads.jsonl"


def _ensure():
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)


def save_lead(lead: Lead) -> None:
    _ensure()
    with DB_FILE.open("a", encoding="utf-8") as f:
        f.write(lead.model_dump_json() + "\n")


def _all() -> list[Lead]:
    _ensure()
    if not DB_FILE.exists():
        return []
    leads = []
    for line in DB_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            leads.append(Lead.model_validate_json(line))
    return sorted(leads, key=lambda l: l.created_at, reverse=True)


def get_leads(status: Optional[LeadStatus] = None, source: Optional[str] = None) -> list[Lead]:
    leads = _all()
    if status:
        leads = [l for l in leads if l.status == status]
    if source:
        leads = [l for l in leads if l.source == source]
    return leads


def get_lead_by_id(lead_id: str) -> Optional[Lead]:
    return next((l for l in _all() if l.id == lead_id), None)


def update_status(lead_id: str, new_status: LeadStatus) -> Optional[Lead]:
    leads = _all()
    updated = None
    for lead in leads:
        if lead.id == lead_id:
            lead.status = new_status
            updated = lead
    if updated:
        DB_FILE.write_text(
            "\n".join(l.model_dump_json() for l in leads) + "\n",
            encoding="utf-8"
        )
    return updated


def get_stats() -> dict:
    leads = _all()
    counts = {s.value: 0 for s in LeadStatus}
    sources: dict[str, int] = {}
    for l in leads:
        counts[l.status.value] += 1
        sources[l.source] = sources.get(l.source, 0) + 1
    return {"total": len(leads), "by_status": counts, "by_source": sources}
