"""Drain SDK 0.13 pages without confusing Page objects with records."""
import asyncio
from .domain import DataError

async def collect(paginator):
    rows=[]
    async for page in paginator:
        if page.limit_reached:raise DataError('官方分页深度已达上限，无法确认账户数据完整')
        rows.extend(page.items)
        if len(rows)>10000:raise DataError('账户记录超过本机核验上限')
    return rows
