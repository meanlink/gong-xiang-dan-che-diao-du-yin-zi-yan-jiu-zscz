import {requestsApi} from '../api/entities.js';
import {workspace} from '../stores/workspace.js';
import {heading} from '../components/common/shell.js';
import {chart} from '../components/common/chart.js';
import {table} from '../components/common/table.js';
import {esc,notify,date,fmt} from '../utils/dom.js';

export async function researchPage(){
 const runs=await requestsApi.settlements();
 const done=runs.filter(r=>r.status==='done');
 const selected=workspace.selectedSettlement ?? done[0]?.id ?? null;
 let data=null,error='';
 if(selected!=null){
  try{data=await requestsApi.research(selected);}
  catch(e){error=e.message;}
 }
 return heading('07 / DEMAND RESEARCH','借成量与求车量偏相关研究',
  '缺车时只看借成数会漏掉真实求车。对照控制通勤后的偏相关，并标出每个结算版本的依据与差异时段。')
 +basisSelector(runs,selected)
 +(error?'<section class="panel empty">'+esc(error)+'</section>':'')
 +(!data?basisEmpty(done.length):researchBody(data));
}

function basisSelector(runs,selected){
 return '<section class="panel"><div class="section-title"><h2>结算依据</h2><span>研究只基于已完成结算</span></div>'
 +(runs.length?'<label>选择结算版本<select id="select-settlement">'+runs.map(r=>'<option value="'+r.id+'" '+(r.id===selected?'selected':'')+'>#'+r.id+' · 截止 '+esc(date(r.cutoff))+' · '+(r.status==='done'?'已完成':'结算中')+'</option>').join('')+'</select></label>'
 +'<p class="hint">同一截止点取数、晚到记录不计入本版；切换版本即切换全部研究口径。</p>'
 :'<p class="empty">尚无结算版本，请先到求车接报页按截止点结算。<a href="#/requests">前往接报结算 ↗</a></p>')+'</section>';
}
function basisEmpty(doneCount){
 return '<section class="panel empty">'+(doneCount?'该版本尚未完成结算。':'没有可用于研究的已完成结算。')
 +'<a href="#/requests">前往求车接报 ↗</a></section>';
}

function researchBody(data){
 const b=data.basis||{};
 const usable=(data.stations||[]).filter(s=>s.partial!=null);
 const chartData=usable.length?{type:'bar',labels:usable.map(s=>s.station),
   series:[{name:'原始相关 r',values:usable.map(s=>s.raw)},
           {name:'控制通勤后偏相关 r',values:usable.map(s=>s.partial)}]}:null;
 const stationColumns=[
  {key:'station',label:'站点'},{key:'pairs',label:'有效时段对'},
  {key:'requests',label:'求车量（单）'},{key:'borrowed',label:'借成量（单）'},
  {key:'raw',label:'原始相关 r'},{key:'partial',label:'偏相关 r'},
  {key:'p',label:'参考 p 值'},{key:'missing_observations',label:'缺观测时段'}];
 const stationRows=(data.stations||[]).map(s=>({...s,
  raw:s.raw??'—',partial:s.partial??'样本不足',p:s.p??'—'}));
 const diffColumns=[
  {key:'station',label:'站点'},{key:'period',label:'时段'},
  {key:'requests',label:'求车量'},{key:'borrowed',label:'借成量'},
  {key:'gap',label:'缺口（求车−借成）'},{key:'has_observation',label:'有观测'}];
 const diffRows=(data.diff_periods||[]).map(d=>({...d,has_observation:d.has_observation?'是':'否'}));
 const ov=data.overall||{};
 return '<section class="result-lead"><p class="eyebrow">SETTLEMENT BASIS / 结算依据</p>'
 +'<h2>结算批次 #'+esc(fmt(b.settlement_id))+' · 截止 '+esc(date(b.cutoff))+' · 观测批次 '+esc(b.dataset_name||'—')+'</h2>'
 +'<div class="metrics">'
 +[['已结算单数',b.settled,'单'],['暂挂单数',b.held,'单'],['晚到记录（不计入）',b.late_records,'条'],
   ['整体偏相关 r',ov.partial??'—',''],['整体原始 r',ov.raw??'—',''],['参考 p 值',ov.p??'—','']]
   .map(m=>'<div><span>'+esc(m[0])+'</span><strong>'+esc(fmt(m[1]))+'<small>'+esc(m[2])+'</small></div>').join('')
 +'</div></section>'
 +'<section class="panel"><div class="section-title"><h2>借成量 vs 求车量相关对照</h2><span>控制通勤指数后</span></div>'
 +(chartData?chart(chartData):'<p class="empty">有效时段不足，无法计算偏相关；不输出伪造相关。</p>')+'</section>'
 +'<div class="two-columns"><section class="panel"><div class="section-title"><h2>分站点偏相关</h2><span>'+stationRows.length+' 站</span></div>'
 +table(stationColumns,stationRows)+'</section>'
 +'<section class="method-sheet compact"><p class="eyebrow">METHOD / 差异口径</p><h3>计算口径</h3><p>'+esc(data.method)+'</p>'
 +'<h3>差异时段</h3><p class="hint">缺口 = 求车量 − 借成量（按首次扫码站点时段归组），缺口 ≥ '+esc(data.diff_threshold)+' 的时段标出，最多展示 100 行。</p></section></div>'
 +'<section class="panel"><div class="section-title"><h2>差异时段（缺车漏损）</h2><span>'+diffRows.length+' 行 · 按缺口降序</span></div>'
 +table(diffColumns,diffRows,'该结算版本下没有明显差异时段。')+'</section>';
}

export function bindResearch(refresh){
 const sel=document.querySelector('#select-settlement');
 if(sel)sel.addEventListener('change',e=>{
  workspace.selectedSettlement=Number(e.target.value);
  refresh().catch(err=>notify(err.message,true));
 });
}
