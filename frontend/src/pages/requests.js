import {requestsApi,datasets} from '../api/entities.js';
import {workspace} from '../stores/workspace.js';
import {session} from '../stores/session.js';
import {heading} from '../components/common/shell.js';
import {table} from '../components/common/table.js';
import {esc,notify,date} from '../utils/dom.js';
import {submit} from '../hooks/forms.js';
import {parseCSV} from '../utils/csv.js';
import {RequestResult,SettlementItemStatus} from '../constants/enums.js';

const recordColumns=[
 {key:'request_id',label:'求车编号'},{key:'scan_time',label:'扫码时刻'},
 {key:'station',label:'站点'},{key:'result',label:'结果'},
 {key:'seq',label:'扫码序号'},{key:'settlement_id',label:'归属结算批次'}];

function localInputValue(epochSeconds){
 const d=new Date((epochSeconds??Date.now()/1000)*1000);
 d.setSeconds(0,0);
 const pad=n=>String(n).padStart(2,'0');
 return d.getFullYear()+'-'+pad(d.getMonth()+1)+'-'+pad(d.getDate())+'T'+pad(d.getHours())+':'+pad(d.getMinutes());
}
function toEpoch(value){return value?new Date(value).getTime()/1000:null;}
function displayRows(rows){
 return rows.slice(0,300).map(r=>({...r,
  scan_time:date(r.scan_time),
  result:RequestResult[r.result]||r.result,
  settlement_id:r.settlement_id??'—'}));
}

export async function requestPage(){
 workspace.datasets=await datasets.list();
 const [records,runs]=await Promise.all([requestsApi.list(),requestsApi.settlements()]);
 workspace.bikeRequests=records; workspace.settlements=runs;
 const readonly=session.user.role==='viewer';
 return heading('06 / REQUEST INTAKE','求车接报与对账结算',
   '只看借成数会漏掉求车。按求车编号接收每次扫码；需求归到首次扫码的站点和时段，借成只计一次，未借成但报缺车也计一单。成功单按借成站点时段与批次观测对账，对不上先不结算。')
 +'<div class="dataset-grid"><section class="panel">'
 +'<div class="section-title"><h2>接收扫码记录</h2><span>'+records.length+' 条已接报</span></div>'
 +'<form id="intake-form"><label>来源标识<input name="source" value="manual.json" maxlength="200"></label>'
 +'<label>上传 CSV 或 JSON<input type="file" id="request-file" accept=".json,.csv"></label>'
 +'<label>记录 JSON<textarea name="records" rows="7" placeholder=\'[{"request_id":"R00001","scan_time":1763000060,"station":"科技园","result":"shortage","seq":0}]\' required></textarea>'
 +'<p class="hint">字段：request_id 求车编号、scan_time 扫码时刻（秒）、station 站点、result（borrowed 借成 / shortage 缺车）、seq 可选扫码序号。同一编号自然键重复自动去重。</p>'
 +'<button type="submit" class="primary" '+(readonly?'disabled':'')+'>校验并接报</button></form>'
 +'<div class="section-title" style="margin-top:34px"><h2>按截止点对账结算</h2></div>'
 +'<form id="settle-form">'
 +'<label>截止点（同一截止点取数）<input type="datetime-local" name="cutoff" step="60" value="'+esc(localInputValue())+'"></label>'
 +'<label>观测窗口起点（折算时段锚点）<input type="datetime-local" name="window_start" step="60" value="'+esc(localInputValue(runs[0]?.window_start))+'"></label>'
 +'<label>对账观测批次<select name="dataset_id"><option value="">最新观测批次（默认）</option>'
 +workspace.datasets.map(d=>'<option value="'+d.id+'">'+esc(d.name)+' · '+d.rows+' 条</option>').join('')+'</select></label>'
 +'<button type="submit" class="primary" '+(readonly||!workspace.datasets.length?'disabled':'')+'>按此截止点结算 ↗</button>'
 +'<p class="hint">晚于截止点的记录留给下一版；同一截止点重复提交不会重复出结果。借成单对不上观测站点时段时暂挂，不进入结算。</p></form>'
 +'</section><section class="panel">'
 +'<div class="section-title"><h2>结算批次</h2><span>'+runs.length+' 版</span></div>'
 +'<div class="batch-list">'+(runs.length?runs.map(r=>'<button class="batch-item" data-run="'+r.id+'"><strong>截止 '+esc(date(r.cutoff))+' · '+(r.status==='done'?'已完成':'结算中')+'</strong>'
   +'<span>已结算 '+(r.result?.settled??0)+' 单 · 暂挂 '+(r.result?.held??0)+' 单 · 晚到 '+(r.result?.late_records??0)+' 条</span>'
   +'<small>#'+r.id+' · 观测批次 '+esc(r.result?.dataset_name||r.dataset_id||'—')+'</small></button>').join('')
   :'<p class="empty">尚未生成结算批次。</p>')+'</div>'
 +'<div class="section-title"><h2 id="run-detail-title">最近接报（前 300 条）</h2><span>点左侧批次查看对账明细</span></div>'
 +'<div id="request-preview">'+table(recordColumns,displayRows(records))+'</div>'
 +'</section></div>';
}

function itemsColumns(){
 return [{key:'request_id',label:'求车编号'},{key:'station',label:'首次扫码站点'},
  {key:'period',label:'首次时段'},{key:'borrowed',label:'是否借成'},
  {key:'borrow_station',label:'借成站点'},{key:'borrow_period',label:'借成时段'},
  {key:'status',label:'结算状态'},{key:'reason',label:'依据/原因'}];
}
function itemRows(items){
 return items.map(i=>({...i,
  borrowed:i.borrowed?'是':'否',
  borrow_station:i.borrow_station??'—',borrow_period:i.borrow_period??'—',
  status:SettlementItemStatus[i.status]||i.status,
  reason:i.reason||(i.status==='settled'?'对账通过，已结算':'')}));
}

export function bindRequests(refresh){
 let source='manual.json';
 document.querySelector('#request-file').addEventListener('change',async e=>{
  try{const file=e.target.files[0];if(!file)return;source=file.name;const text=await file.text();
   const records=file.name.endsWith('.csv')?parseCSV(text):JSON.parse(text);
   document.querySelector('[name=records]').value=JSON.stringify(records,null,2);
  }catch(error){notify(error.message,true);}
 });
 submit(document.querySelector('#intake-form'),async f=>{
  const outcome=await requestsApi.intake({source,records:JSON.parse(f.get('records'))});
  notify('接报完成：新增 '+outcome.new+' 条，去重 '+outcome.duplicates+' 条');await refresh();
 });
 submit(document.querySelector('#settle-form'),async f=>{
  const payload={cutoff:toEpoch(f.get('cutoff')),window_start:toEpoch(f.get('window_start'))??0};
  const ds=f.get('dataset_id');if(ds)payload.dataset_id=Number(ds);
  const run=await requestsApi.settle(payload);
  notify(run.created?'已生成结算批次 #'+run.id:'该截止点已有结果，返回现有批次（不重复结算）');
  await refresh();openRun(run.id);
 });
 document.querySelectorAll('[data-run]').forEach(b=>b.onclick=()=>openRun(b.dataset.run));
}

async function openRun(id){
 try{
  const detail=await requestsApi.settlement(id);
  document.querySelector('#run-detail-title').textContent='结算批次 #'+id+' · 对账明细';
  const held=detail.items.filter(i=>i.status==='held').length;
  const note='<p class="'+(held?'warning':'hint')+'">共 '+detail.items.length+' 单：已结算 '
   +detail.items.filter(i=>i.status==='settled').length+' 单，暂挂 '+held+' 单。暂挂单对不上观测，先不结算。</p>';
  document.querySelector('#request-preview').innerHTML=note+table(itemsColumns(),itemRows(detail.items));
 }catch(e){notify(e.message,true);}
}
