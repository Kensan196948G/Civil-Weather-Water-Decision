"""データソース実プローブのテスト（httpx.MockTransport / ネット非依存）。"""
import httpx
import pytest

from app.services.data_collectors import source_probe as sp


@pytest.mark.asyncio
async def test_probe_one_ok():
    transport = httpx.MockTransport(lambda req: httpx.Response(200))
    async with httpx.AsyncClient(transport=transport) as c:
        status, ms, ok, err = await sp.probe_one("http://x", c)
    assert ok and status in ("OK", "Warning")


@pytest.mark.asyncio
async def test_probe_one_4xx_is_warning():
    transport = httpx.MockTransport(lambda req: httpx.Response(404))
    async with httpx.AsyncClient(transport=transport) as c:
        status, ms, ok, err = await sp.probe_one("http://x", c)
    assert status == "Warning" and ok


@pytest.mark.asyncio
async def test_probe_one_5xx_is_error():
    transport = httpx.MockTransport(lambda req: httpx.Response(503))
    async with httpx.AsyncClient(transport=transport) as c:
        status, ms, ok, err = await sp.probe_one("http://x", c)
    assert status == "Error" and not ok


@pytest.mark.asyncio
async def test_probe_one_connection_error_is_error():
    def boom(req):
        raise httpx.ConnectError("refused")
    transport = httpx.MockTransport(boom)
    async with httpx.AsyncClient(transport=transport) as c:
        status, ms, ok, err = await sp.probe_one("http://x", c)
    assert status == "Error" and not ok and err == "ConnectError"


@pytest.mark.asyncio
async def test_probe_all_updates_rows(client):
    # client fixture で DB 初期化＋シード済み
    from app.core.db import SessionLocal
    from app.models import DataSourceStatus

    transport = httpx.MockTransport(lambda req: httpx.Response(200))
    async with httpx.AsyncClient(transport=transport) as c:
        with SessionLocal() as db:
            res = await sp.probe_all(db, client=c)
            assert "DS-OPEN-METEO" in res
            row = db.get(DataSourceStatus, "DS-OPEN-METEO")
            assert row.status in ("OK", "Warning")
            assert row.fails == 0 and row.last_ok
            # プローブ対象外（水防災オープン）は更新されない
            wo = db.get(DataSourceStatus, "DS-WATER-OPEN")
            assert wo.status == "Error"
