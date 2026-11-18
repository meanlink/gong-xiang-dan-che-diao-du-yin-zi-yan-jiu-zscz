import hashlib, json
def checksum(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
def dataset_dict(d):
    return {'id':d.id, 'name':d.name, 'source':d.source, 'rows':len(d.records), 'checksum':d.checksum, 'created_at':d.created_at}
def job_dict(j):
    return {k:getattr(j,k) for k in ['id','name','dataset_id','params','status','result','error','approved','created_at','finished_at']}
def report_dict(r):
    return {k:getattr(r,k) for k in ['id','name','job_id','destination','interval_seconds','next_run','enabled']}
def delivery_dict(d):
    return {k:getattr(d,k) for k in ['id','report_id','created_at','checksum','filename','destination']}
def bike_request_dict(r):
    return {'id':r.id,'request_id':r.request_id,'scan_time':r.scan_time,'station':r.station,
            'result':r.result,'seq':r.seq,'source':r.source,'received_at':r.received_at,
            'settlement_id':r.settlement_id}
def settlement_run_dict(r):
    return {'id':r.id,'cutoff':r.cutoff,'window_start':r.window_start,'dataset_id':r.dataset_id,'status':r.status,
            'result':r.result,'created_by':r.created_by,'created_at':r.created_at,
            'finished_at':r.finished_at}
def settlement_item_dict(i):
    return {'id':i.id,'settlement_id':i.settlement_id,'request_id':i.request_id,
            'station':i.station,'period':i.period,'borrowed':i.borrowed,
            'borrow_station':i.borrow_station,'borrow_period':i.borrow_period,
            'status':i.status,'matched':i.matched,'reason':i.reason,'scan_count':i.scan_count}
