import {api} from './client.js';
export const datasets={list:()=>api('/datasets'),get:id=>api('/datasets/'+id),create:data=>api('/datasets',{method:'POST',body:JSON.stringify(data)})};
export const jobs={list:()=>api('/jobs'),get:id=>api('/jobs/'+id),create:data=>api('/jobs',{method:'POST',body:JSON.stringify(data)}),approve:id=>api('/jobs/'+id+'/approve',{method:'POST'})};
export const reports={list:()=>api('/reports'),create:data=>api('/reports',{method:'POST',body:JSON.stringify(data)}),toggle:id=>api('/reports/'+id+'/toggle',{method:'POST'}),deliveries:()=>api('/deliveries')};
export const requestsApi={
 list:()=>api('/requests'),
 intake:data=>api('/requests/intake',{method:'POST',body:JSON.stringify(data)}),
 settlements:()=>api('/requests/settlements'),
 settle:data=>api('/requests/settlements',{method:'POST',body:JSON.stringify(data)}),
 settlement:id=>api('/requests/settlements/'+id),
 research:(settlementId)=>api('/requests/research'+(settlementId?('?settlement_id='+settlementId):''))
};
