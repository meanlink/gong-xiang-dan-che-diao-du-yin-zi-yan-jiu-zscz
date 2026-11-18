import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from src.config.database import get_db
from src.models import BikeRequest, SettlementRun, SettlementItem
from src.schemas.request import RequestIntakeInput, SettlementInput
from src.middlewares.auth import current_user, can_write
from src.services import requests_service as service
from src.services import requests_core as core
from src.utils.serialization import (
    bike_request_dict, settlement_run_dict, settlement_item_dict,
)

router = APIRouter(prefix='/requests')


@router.get('')
def list_requests(limit: int = 300, db=Depends(get_db), u=Depends(current_user)):
    limit = max(1, min(limit, 2000))
    rows = db.scalars(
        select(BikeRequest).order_by(BikeRequest.id.desc()).limit(limit)
    ).all()
    return [bike_request_dict(r) for r in rows]


@router.post('/intake', status_code=201)
def intake(data: RequestIntakeInput, db=Depends(get_db), u=Depends(can_write)):
    records = [r.model_dump() for r in data.records]
    try:
        outcome = service.intake(db, records, data.source, u)
    except core.ValidationError as e:
        db.rollback()
        raise HTTPException(422, str(e))
    db.commit()
    return outcome


@router.get('/settlements')
def list_settlements(db=Depends(get_db), u=Depends(current_user)):
    runs = db.scalars(
        select(SettlementRun).order_by(SettlementRun.cutoff.desc()).limit(50)
    ).all()
    return [settlement_run_dict(r) for r in runs]


@router.post('/settlements', status_code=201)
def create_settlement(data: SettlementInput, db=Depends(get_db), u=Depends(can_write)):
    cutoff = data.cutoff if data.cutoff is not None else time.time()
    try:
        run, created = service.run_settlement(db, cutoff, data.dataset_id, u,
                                              window_start=data.window_start)
    except core.ValidationError as e:
        db.rollback()
        raise HTTPException(422, str(e))
    db.commit()
    return {**settlement_run_dict(run), 'created': created}


@router.get('/settlements/{id}')
def settlement_detail(id: int, db=Depends(get_db), u=Depends(current_user)):
    run = db.get(SettlementRun, id)
    if not run:
        raise HTTPException(404, '结算批次不存在')
    items = db.scalars(
        select(SettlementItem).where(SettlementItem.settlement_id == id)
        .order_by(SettlementItem.status.asc(), SettlementItem.station.asc(),
                  SettlementItem.period.asc())
    ).all()
    return {**settlement_run_dict(run), 'items': [settlement_item_dict(i) for i in items]}


@router.get('/research')
def research(settlement_id: int | None = None, db=Depends(get_db),
             u=Depends(current_user)):
    try:
        return service.research(db, settlement_id)
    except core.ValidationError as e:
        raise HTTPException(422, str(e))
