from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from src.config.database import get_db
from src.middlewares.auth import current_user
from src.models import Dataset, Job, Report, Audit, BikeRequest, SettlementRun, SettlementItem
from src.services.business import metadata
from src.services.requests_core import SETTLED, DONE
router=APIRouter()
@router.get('/meta')
def meta():return metadata()
@router.get('/overview')
def overview(db=Depends(get_db),user=Depends(current_user)):
    latest=db.scalar(select(SettlementRun).where(SettlementRun.status==DONE).order_by(SettlementRun.cutoff.desc()))
    held=settled=0
    if latest:
        settled=db.scalar(select(func.count()).select_from(SettlementItem).where(SettlementItem.settlement_id==latest.id,SettlementItem.status==SETTLED))
        held=db.scalar(select(func.count()).select_from(SettlementItem).where(SettlementItem.settlement_id==latest.id,SettlementItem.status=='held'))
    return {'datasets':db.scalar(select(func.count()).select_from(Dataset)), 'records':sum(len(d.records) for d in db.scalars(select(Dataset))), 'jobs':db.scalar(select(func.count()).select_from(Job)), 'approved':db.scalar(select(func.count()).select_from(Job).where(Job.approved==True)), 'reports':db.scalar(select(func.count()).select_from(Report)), 'bike_requests':db.scalar(select(func.count()).select_from(BikeRequest)), 'settlements':db.scalar(select(func.count()).select_from(SettlementRun)), 'latest_settled':settled, 'latest_held':held}
@router.get('/audits')
def audits(db=Depends(get_db),user=Depends(current_user)):
    return [{'id':a.id,'actor':a.actor,'action':a.action,'detail':a.detail,'at':a.at} for a in db.scalars(select(Audit).order_by(Audit.id.desc()).limit(50))]
