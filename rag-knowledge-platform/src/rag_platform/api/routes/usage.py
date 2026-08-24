"""Per-tenant usage rollup: token counts always; cost only when every row has
one (a partial sum presented as the total would be a lie)."""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from rag_platform.api.deps import CurrentTenant, DbSession
from rag_platform.api.schemas import UsageByModelOut, UsageSummaryOut
from rag_platform.models import UsageRecord

router = APIRouter(tags=["usage"])


@router.get("/usage", response_model=UsageSummaryOut)
async def usage_summary(
    tenant: CurrentTenant,
    session: DbSession,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> UsageSummaryOut:
    since = datetime.now(UTC) - timedelta(days=days)
    rows = (
        await session.execute(
            select(
                UsageRecord.model,
                func.count().label("calls"),
                func.sum(UsageRecord.input_tokens).label("input_tokens"),
                func.sum(UsageRecord.output_tokens).label("output_tokens"),
                func.sum(UsageRecord.cost_usd).label("cost_usd"),
                # rows with NULL cost make the group's cost unknowable
                func.count().filter(UsageRecord.cost_usd.is_(None)).label("uncosted"),
            )
            .where(UsageRecord.tenant_id == tenant.tenant_id, UsageRecord.created_at >= since)
            .group_by(UsageRecord.model)
            .order_by(UsageRecord.model)
        )
    ).all()

    by_model = [
        UsageByModelOut(
            model=r.model,
            calls=r.calls,
            input_tokens=int(r.input_tokens),
            output_tokens=int(r.output_tokens),
            cost_usd=None if r.uncosted else float(r.cost_usd),
        )
        for r in rows
    ]
    any_uncosted = any(m.cost_usd is None for m in by_model)
    return UsageSummaryOut(
        days=days,
        total_calls=sum(m.calls for m in by_model),
        total_input_tokens=sum(m.input_tokens for m in by_model),
        total_output_tokens=sum(m.output_tokens for m in by_model),
        total_cost_usd=None
        if any_uncosted
        else float(sum(m.cost_usd for m in by_model if m.cost_usd is not None)),
        by_model=by_model,
    )
