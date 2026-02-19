"""
Lead scoring logic based on funnel definitions per influencer channel.

Ignacia Rios (financial niche):
  income_high       → 30 pts
  credit_urgency    → 30 pts
  investment_interest → 20 pts
  existing_debt     → 20 pts
  premium threshold → 70+

Max Andrade (digital business niche):
  existing_business   → 30 pts
  revenue_over_threshold → 30 pts
  clear_niche         → 20 pts
  ready_to_invest     → 20 pts
  premium threshold   → 70+
"""

from leads_engine.models import (
    IgnaciaLeadInput,
    MaxLeadInput,
    LeadScore,
    LeadTier,
    IncomeRange,
    RevenueRange,
)

PREMIUM_THRESHOLD = 70


def score_ignacia(data: IgnaciaLeadInput) -> LeadScore:
    breakdown: dict[str, int] = {}

    breakdown["income_high"] = 30 if data.monthly_income_range == IncomeRange.alto else 0
    breakdown["credit_urgency"] = 30 if data.applying_for_credit_soon else 0
    breakdown["investment_interest"] = 20 if data.interested_in_investments else 0
    breakdown["existing_debt"] = 20 if data.has_existing_debt else 0

    total = sum(breakdown.values())
    tier = LeadTier.premium if total >= PREMIUM_THRESHOLD else LeadTier.standard

    return LeadScore(total=total, breakdown=breakdown, tier=tier)


def score_max(data: MaxLeadInput) -> LeadScore:
    breakdown: dict[str, int] = {}

    breakdown["existing_business"] = 30 if data.business_stage not in ("idea",) else 0
    breakdown["revenue_over_threshold"] = 30 if data.monthly_revenue_range in (RevenueRange.mid, RevenueRange.high) else 0
    breakdown["clear_niche"] = 20 if data.has_defined_niche else 0
    breakdown["ready_to_invest"] = 20 if data.ready_to_invest else 0

    total = sum(breakdown.values())
    tier = LeadTier.premium if total >= PREMIUM_THRESHOLD else LeadTier.standard

    return LeadScore(total=total, breakdown=breakdown, tier=tier)
