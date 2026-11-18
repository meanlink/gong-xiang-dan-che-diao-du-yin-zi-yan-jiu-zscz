import time, json
from sqlalchemy import select, func
from src.config.database import Session
from migrations.initial import migrate
from src.models import User, Dataset, Job, Report, Delivery, Audit, BikeRequest, SettlementRun
from src.services.business import metadata, seed_records, analyze
from src.services.requests_core import seed_events, DONE
from src.services.requests_service import run_settlement
from src.services.reports import deliver
from src.utils.security import hash_password
from src.utils.serialization import checksum
def seed():
    migrate()
    with Session() as db:
        for role,name in [('admin','林知 · 业务负责人'),('analyst','陈舟 · 分析员'),('viewer','周禾 · 观察员')]:
            email=role+'@demo.local'
            if not db.scalar(select(User).where(User.email==email)):
                db.add(User(email=email,name=name,role=role,password=hash_password('Demo123!')))
        db.flush()
        m=metadata()
        d=db.scalar(select(Dataset).where(Dataset.name==m['dataset_name']))
        if not d:
            rows=seed_records()
            d=Dataset(name=m['dataset_name'],source=m['short']+'-202611.csv',records=rows,checksum=checksum(rows),created_at=time.time())
            db.add(d); db.flush()
        if not db.scalar(select(Job).where(Job.name=='基线复核 · '+m['name'])):
            params={p['key']:p['default'] for p in m.get('parameters',[])}
            result=analyze(d.records,params)
            json.dumps(result,ensure_ascii=False,allow_nan=False)
            admin=db.scalar(select(User).where(User.role=='admin'))
            j=Job(name='基线复核 · '+m['name'],dataset_id=d.id,params=params,status='completed',result=result,created_by=admin.id,created_at=time.time()-3600,finished_at=time.time()-3595,approved=True)
            db.add(j);db.flush()
            r=Report(name='每日业务复核简报',job_id=j.id,destination='业务复核组 · 本地收件箱',interval_seconds=86400,next_run=time.time()+86400,enabled=True)
            db.add(r);db.flush();deliver(db,r)
            db.add(Audit(actor='seed',action='demo.baseline',detail={'dataset':d.id},at=time.time()))
        if not db.scalar(select(BikeRequest)):
            events, base_time = seed_events()
            now = time.time()
            db.add_all([BikeRequest(request_id=e['request_id'], scan_time=e['scan_time'],
                                    station=e['station'], result=e['result'], seq=e['seq'],
                                    source='req-demo-202611.json', received_at=now) for e in events])
            db.flush()
            # 截止点取最后一条时刻之前：保留少量晚到记录给下一版，
            # 同时让落在观测窗口外的借成单进入本版并被对账暂挂。
            times = sorted(e['scan_time'] for e in events)
            cutoff = times[-1] - 0.5
            run, _ = run_settlement(db, cutoff, d.id,
                                    type('Actor', (), {'email': 'seed'})(),
                                    window_start=base_time)
            db.add(Audit(actor='seed', action='demo.settlement',
                         detail={'settlement': run.id, 'result': run.result}, at=time.time()))
        db.commit()
        counts={model.__tablename__:db.scalar(select(func.count()).select_from(model)) for model in [User,Dataset,Job,Report,Delivery,Audit,BikeRequest,SettlementRun]}
        print(json.dumps(counts,ensure_ascii=False))
        return counts
if __name__=='__main__':seed()
