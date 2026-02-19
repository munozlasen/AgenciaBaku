"""
File-based lead storage.
Leads are written as JSON lines to leads_database/<channel>.jsonl
One file per influencer channel for easy querying and export.
"""

import json
import os
from pathlib import Path
from typing import Optional

from leads_engine.models import Lead, InfluencerChannel, LeadTier

DB_DIR = Path(os.getenv("LEADS_DB_DIR", "leads_database"))


def _channel_file(channel: InfluencerChannel) -> Path:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    return DB_DIR / f"{channel.value}.jsonl"


def save_lead(lead: Lead) -> None:
    path = _channel_file(lead.channel)
    with path.open("a", encoding="utf-8") as f:
        f.write(lead.model_dump_json() + "\n")


def get_leads(
    channel: Optional[InfluencerChannel] = None,
    tier: Optional[LeadTier] = None,
) -> list[Lead]:
    channels = [channel] if channel else list(InfluencerChannel)
    results: list[Lead] = []

    for ch in channels:
        path = _channel_file(ch)
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            lead = Lead.model_validate_json(line)
            if tier is None or lead.score.tier == tier:
                results.append(lead)

    results.sort(key=lambda l: l.created_at, reverse=True)
    return results


def get_lead_by_id(lead_id: str) -> Optional[Lead]:
    for ch in InfluencerChannel:
        path = _channel_file(ch)
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            lead = Lead.model_validate_json(line)
            if lead.id == lead_id:
                return lead
    return None


def count_by_tier(channel: InfluencerChannel) -> dict[str, int]:
    leads = get_leads(channel=channel)
    return {
        "premium": sum(1 for l in leads if l.score.tier == LeadTier.premium),
        "standard": sum(1 for l in leads if l.score.tier == LeadTier.standard),
        "total": len(leads),
    }
