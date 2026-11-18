"""求车接报、对账结算与研究的持久化服务。

并发口径：
- 结算用 Postgres 事务级咨询锁串行化，配合 cutoff 唯一约束，保证同一截止点
  只出一版结果；重复提交同一截止点直接返回已有批次，不重复结算。
- 结算只读取 scan_time <= cutoff 的接报（同一截止点取数）；晚于截止点的记录
  留给下一版。SQLite（本地/单测）退化为同一事务内的普通读写。
"""
import time

from fastapi import HTTPException
from sqlalchemy import select, delete
from sqlalchemy.exc import IntegrityError

from src.models import BikeRequest, SettlementRun, SettlementItem, Dataset
from src.services import requests_core as core
from src.services.audit import record

# 咨询锁固定键（两个 32 位 int 合成）
_LOCK_KEY = (0x52455100, 0x5345544C)  # 'REQ'/'SETL' 助记


def _is_postgres(db):
    return db.bind.dialect.name == 'postgresql'


def _acquire_settlement_lock(db):
    # 咨询锁串行化所有结算事务；配合 cutoff 唯一约束，保证同一截止点只出一版。
    # 不额外 SET TRANSACTION 级别：认证依赖已开启事务，READ COMMITTED 下持锁后
    # 的每条语句取最新快照，仍能读到前一个已提交结算，唯一约束再做兜底。
    if _is_postgres(db):
        from sqlalchemy import text
        db.execute(text('SELECT pg_advisory_xact_lock(:k1, :k2)'),
                   {'k1': _LOCK_KEY[0], 'k2': _LOCK_KEY[1]})


def intake(db, records, source, user):
    """接收一批扫码记录，按自然键 (request_id, scan_time, seq) 幂等去重。"""
    normalized = core.normalize_batch(records)
    existing = {
        (r[0], r[1], r[2])
        for r in db.execute(
            select(BikeRequest.request_id, BikeRequest.scan_time, BikeRequest.seq)
        ).all()
    }
    now = time.time()
    new_rows, seen_in_batch = [], set()
    duplicates = 0
    for event in normalized:
        key = (event['request_id'], event['scan_time'], event['seq'])
        if key in existing or key in seen_in_batch:
            duplicates += 1
            continue
        seen_in_batch.add(key)
        new_rows.append(BikeRequest(
            request_id=event['request_id'], scan_time=event['scan_time'],
            station=event['station'], result=event['result'], seq=event['seq'],
            source=source, received_at=now,
        ))
    if not new_rows:
        return {'received': len(normalized), 'new': 0, 'duplicates': duplicates}
    db.add_all(new_rows)
    db.flush()
    record(db, user.email, 'request.intake',
           {'received': len(normalized), 'new': len(new_rows), 'duplicates': duplicates})
    return {'received': len(normalized), 'new': len(new_rows), 'duplicates': duplicates}


def _resolve_dataset(db, dataset_id):
    if dataset_id is not None:
        dataset = db.get(Dataset, dataset_id)
        if not dataset:
            raise HTTPException(404, '对账所用观测批次不存在')
        return dataset
    dataset = db.scalar(select(Dataset).order_by(Dataset.id.desc()))
    if not dataset:
        raise HTTPException(409, '尚无站点观测批次，无法对账，请先接入观测数据')
    return dataset


def run_settlement(db, cutoff, dataset_id, user, window_start=0.0):
    """按截止点取数并结算；同 cutoff 幂等，重试不重复出结果。"""
    _acquire_settlement_lock(db)

    existing = db.scalar(select(SettlementRun).where(SettlementRun.cutoff == cutoff))
    if existing is not None:
        if existing.status == core.DONE:
            # 同一截止点已有完成版：直接返回，绝不重复结算
            return existing, False
        # 崩溃残留的 running 批次：复用同一行重算（其明细先清掉）
        run = existing
        run.window_start = window_start
        db.execute(delete(SettlementItem).where(SettlementItem.settlement_id == run.id))
    else:
        run = SettlementRun(cutoff=cutoff, window_start=window_start, status=core.RUNNING,
                            created_by=user.email if hasattr(user, 'email') else str(user),
                            created_at=time.time())
        db.add(run)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raced = db.scalar(select(SettlementRun).where(SettlementRun.cutoff == cutoff))
            if raced is not None:
                return raced, False
            raise

    dataset = _resolve_dataset(db, dataset_id)
    run.dataset_id = dataset.id

    # 同一截止点取数：只取 scan_time <= cutoff 的接报
    events = [
        {'request_id': r.request_id, 'scan_time': r.scan_time, 'station': r.station,
         'result': r.result, 'seq': r.seq}
        for r in db.scalars(select(BikeRequest).where(BikeRequest.scan_time <= cutoff))
    ]
    late_count = len(db.scalars(
        select(BikeRequest).where(BikeRequest.scan_time > cutoff)
    ).all())

    groups, _late = core.group_requests(events, cutoff, origin=window_start)
    obs_index = core.observation_index(dataset.records)
    items, summary = core.settle_groups(groups, obs_index)

    item_rows = [
        SettlementItem(
            settlement_id=run.id, request_id=g['request_id'],
            station=g['first_station'], period=g['first_period'],
            borrowed=g['borrowed'], borrow_station=g['borrow_station'],
            borrow_period=g['borrow_period'], status=g['status'],
            matched=g['matched'], reason=g['reason'], scan_count=g['scan_count'],
        ) for g in items
    ]
    if item_rows:
        db.add_all(item_rows)

    # 回写接报归属，便于按单号追溯进入了哪一版
    if groups:
        ids = [g['request_id'] for g in groups]
        for row in db.scalars(select(BikeRequest).where(BikeRequest.request_id.in_(ids))):
            row.settlement_id = run.id

    run.result = {
        'cutoff': cutoff, 'window_start': window_start,
        'dataset_id': dataset.id, 'dataset_name': dataset.name,
        'late_records': late_count, **summary,
    }
    run.status = core.DONE
    run.finished_at = time.time()
    db.flush()
    record(db, run.created_by, 'settlement.run',
           {'id': run.id, 'cutoff': cutoff, **summary})
    return run, True


def latest_done_run(db):
    return db.scalar(
        select(SettlementRun).where(SettlementRun.status == core.DONE)
        .order_by(SettlementRun.cutoff.desc())
    )


def research(db, settlement_id=None):
    """借成量与求车量的偏相关研究，依据某一已完成结算批次。"""
    if settlement_id is not None:
        run = db.get(SettlementRun, settlement_id)
        if not run:
            raise HTTPException(404, '结算批次不存在')
    else:
        run = latest_done_run(db)
    if not run:
        raise HTTPException(409, '尚无已完成结算，请先执行对账结算')
    if run.status != core.DONE:
        raise HTTPException(409, '该结算批次尚未完成')

    dataset = db.get(Dataset, run.dataset_id) if run.dataset_id else None
    if not dataset:
        raise HTTPException(409, '结算批次缺少观测数据，无法研究')

    settled = [
        {'request_id': it.request_id, 'first_station': it.station,
         'first_period': it.period, 'borrowed': it.borrowed}
        for it in db.scalars(
            select(SettlementItem).where(
                SettlementItem.settlement_id == run.id,
                SettlementItem.status == core.SETTLED,
            )
        )
    ]
    payload = core.build_research(settled, dataset.records)
    payload['basis'] = {
        'settlement_id': run.id, 'cutoff': run.cutoff, 'window_start': run.window_start,
        'dataset_id': dataset.id, 'dataset_name': dataset.name,
        'settled': run.result.get('settled'), 'held': run.result.get('held'),
        'late_records': run.result.get('late_records'),
    }
    return payload
