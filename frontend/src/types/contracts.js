/** @typedef {{id:number,name:string,source:string,rows:number,checksum:string}} Dataset */
/** @typedef {{id:number,name:string,dataset_id:number,status:string,result:Object,approved:boolean}} AnalysisJob */
/** @typedef {{id:number,name:string,job_id:number,destination:string,next_run:number,enabled:boolean}} ReportSchedule */
/** @typedef {{id:number,request_id:string,scan_time:number,station:string,result:string,seq:number,settlement_id:?number}} BikeRequest */
/** @typedef {{id:number,cutoff:number,window_start:number,status:string,result:Object}} SettlementRun */
export {};
