"""環境省WBGT地点マスタの取り込みCLI（#113）。

使い方:
  cd backend
  python -m app.scripts.import_wbgt_stations --dry-run   # 取得のみ・DB変更なし
  python -m app.scripts.import_wbgt_stations             # observation_stations へ同期
"""
from __future__ import annotations

import argparse
import asyncio

from app.core.db import SessionLocal
from app.services.data_collectors import wbgt_env


async def _run(dry_run: bool) -> int:
    data = await wbgt_env.fetch_point_master()
    if data.get("status") != "OK":
        print(f"ERROR: {data.get('error')}")
        return 1
    print(f"master: {data['count']} stations fetched")
    if dry_run:
        for st in data["stations"][:5]:
            print(f"  {st['station_code']} {st['name']} "
                  f"({st['latitude']},{st['longitude']})")
        print("dry-run: DBを変更しません")
        return 0
    db = SessionLocal()
    try:
        result = await wbgt_env.sync_point_master(db, data=data)
        print(f"sync: status={result.get('status')} count={result.get('count')} "
              f"upserted={result.get('upserted')} updated={result.get('updated')}")
        return 0 if result.get("status") == "OK" else 1
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="環境省WBGT地点マスタの取り込み")
    parser.add_argument("--dry-run", action="store_true",
                        help="取得のみ行いDBを変更しない")
    args = parser.parse_args()
    return asyncio.run(_run(args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
