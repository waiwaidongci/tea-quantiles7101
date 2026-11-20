import {pairing} from '../api/pairing.js';
import {pairingStore,loadPairing,loadReadings} from '../stores/pairing.js';
import {table} from '../components/common/table.js';
import {status} from '../components/common/status.js';
import {bindForm} from '../hooks/forms.js';
import {esc,fmt} from '../utils/format.js';

const LABELS={initial:'初测册',retest:'复测册'};

function allReadings(){
 const by={initial:[],retest:[]};pairingStore.readings.forEach(r=>by[r.booklet]?.push(r));return by;
}

/* ---------- 结果核对 ----------
 展示前在浏览器里独立复算：份数、总时差、每份“复测−初测”差值，以及两倍中位数。
 任何一项对不上就不展示上下界，只提示等待或失败原因。 */
function micro(text){
 // 十进制定点字符串 -> 整数 micro，避免 parseFloat 舍入
 const s=String(text??'').trim();const m=/^(-?)(\d+)(?:\.(\d+))?$/.exec(s);if(!m)return null;
 if(m[3]&&m[3].length>6)return null;
 const sign=m[1]==='-'?-1:1;const whole=Number(m[2]);const frac=Number((m[3]||'').padEnd(6,'0'));
 return sign*(whole*1e6+frac);
}
function auditBound(label,result){
 const k=result.cardinality;
 if(!result.verified)return `${label}：结果未标记为已核对`;
 if(!Array.isArray(result.checks)||result.checks.length<3)return `${label}：缺少核对记录`;
 const b=result[label==='下界'?'bound_lower':'bound_upper'];
 if(!b||!Array.isArray(b.pairing))return `${label}：缺配对明细`;
 if(b.pairing.length!==k)return `${label}：明细 ${b.pairing.length} 份，应为 ${k} 份`;
 const time=b.pairing.reduce((n,r)=>n+Number(r.time_diff),0);
 if(time!==result.total_time_diff)return `${label}：总时差 ${time}，应为 ${result.total_time_diff}`;
 const keys=new Set(),used=new Set();
 for(const r of b.pairing){
  const a=`i:${r.initial_key}`,c=`r:${r.retest_key}`;
  if(used.has(a)||used.has(c))return `${label}：有读数被重复配对`;
  used.add(a);used.add(c);keys.add(a);keys.add(c);
 }
 const deltas=b.pairing.map(r=>micro(r.delta)).sort((x,y)=>x-y);
 if(deltas.some(v=>v===null))return `${label}：差值无法解析`;
 const twice=k%2?2*deltas[(k-1)/2]:deltas[k/2-1]+deltas[k/2];
 if(twice!==b.twice_median_micro)return `${label}：按明细复算的两倍中位数与结果不一致`;
 return null;
}
function audit(result){
 if(!result)return ['结果尚未写入'];
 if(result.cardinality===0)return [];
 if(result.bound_lower===null||result.bound_upper===null)return ['缺少上下界'];
 return [auditBound('下界',result),auditBound('上界',result)].filter(Boolean);
}

/* ---------- 渲染 ---------- */
export async function render(id){
 const writable=localStorage.getItem('research-role')!=='viewer';
 const isAdmin=localStorage.getItem('research-role')==='admin';
 await loadPairing();
 if(pairingStore.studyId)await loadReadings();
 const run=id?await pairing.getRun(id):null;
 if(run)pairingStore.runId=run.id;
 return `<div class="eyebrow">06 / PAIRING STUDY</div><h1>杯签遗失的配对研究</h1>
 <p class="lead">两次化验的杯签遗失，只能按同批采样时间配对。先配足最多份数，再使总时差最小；同成本的配法都纳入，求“复测−初测”含水率差值中位数上下界。</p>
 <div class="method-note">每份读数最多配一次；每册最多支持 40 份。冻结的初测与复测必须同属一次更正之前或之后；更正后重跑不会改写历史结果。</div>
 ${writable?studyForm():''}
 ${pairingStore.studies.length?`<div class="toolbar">${studySelect()}<a class="button secondary" href="#pairing">刷新</a></div>${readingEditor(writable)}${launchForm(writable)}`:'<p class="method-note">先创建一项配对研究，再录入两册读数。</p>'}
 ${run?`<h2>研究结果 #${run.id}</h2>${runPanel(run,isAdmin)}`:`<h2>运行记录</h2>${runList()}`}`;
}

function studyForm(){
 return `<details><summary>新建配对研究</summary><form id="pairing-study-form" class="inline-form">
 <label>名称<input name="name" required minlength="2" placeholder="如 秋制一号 · 杯签遗失配对"></label>
 <label>说明<input name="description" placeholder="可选"></label>
 <button type="submit">保存</button></form></details>`;
}

function studySelect(){
 return `<label>配对研究<select id="pairing-study-select" name="study_id">${pairingStore.studies.map(s=>`<option value="${s.id}" ${s.id===pairingStore.studyId?'selected':''}>${esc(s.name)} · 初测 ${s.initial_count} / 复测 ${s.retest_count}</option>`).join('')}</select></label>`;
}

function readingEditor(writable){
 const by=allReadings();
 const rows=['initial','retest'].map(booklet=>`<h2>${LABELS[booklet]} <small>${by[booklet].length} / 40 份</small></h2>
 ${table(['源记录','采样时间（分钟）','含水率 (%)'],by[booklet].map(r=>[r.source_key,r.sampled_at,r.moisture]))}`).join('');
 const form=writable?`<details><summary>录入 / 更正读数（一次提交可同时改两册）</summary>
 <form id="pairing-reading-form">
  <div class="inline-form">
   <label>册别<select name="booklet"><option value="initial">初测册</option><option value="retest">复测册</option></select></label>
   <label>源记录编号<input name="source_key" required maxlength="100" placeholder="如 A-001"></label>
   <label>采样时间（分钟）<input name="sampled_at" type="number" step="1" min="0" max="10079" required></label>
   <label>含水率 (%)<input name="moisture" type="number" step="0.000001" min="0" max="100" required></label>
  </div>
  <p style="font-size:12px">提交即视为一次更正：两册改动同一事务提交并递增版本号。</p>
  <button type="submit">保存这次更正</button>
 </form></details>`:'';
 return rows+form;
}

function launchForm(writable){
 if(!writable)return '';
 return `<h2>发起配对研究</h2><form id="pairing-run-form" class="worksheet">
  <label>本次研究名称<input name="name" required minlength="2" value="${esc(pairingStore.studies.find(s=>s.id===pairingStore.studyId)?.name||'配对研究')} · 上下界"></label>
  <div class="inline-form"><label>允许时差（分钟，1–1440）<input name="tolerance_minutes" type="number" step="1" min="1" max="1440" value="30" required></label></div>
  <p>提交时冻结两册读数与允许时差，写入输入校验 SHA256，后台独立计算。上下界与配法核对齐全后才展示。</p>
  <button type="submit">冻结输入并排队 →</button></form>`;
}

export function runList(){
 const rows=pairingStore.runs.map(r=>[{html:`<a href="#pairing/${r.id}">#${r.id} ${esc(r.name)}</a>`},{html:status(r.status)},`${r.frozen_revision} 版 · 时差 ${r.parameters?.tolerance_minutes} 分钟`,r.created_at]);
 return table(['编号 / 研究','状态','冻结版本','创建时间'],rows);
}

function runPanel(run,isAdmin){
 if(run.status==='failed')return `<div class="error">计算失败：${esc(run.error)}。请到读数区核对两册采样时间与含水率，然后发起新的研究。</div>`;
 if(run.status!=='completed')return `<p class="method-note">任务${run.status==='queued'?'在队列中':'正在计算'}。此页每 2 秒刷新，核对齐全后展示上下界。</p>`;
 const result=run.result;
 if(!result)return '<p class="method-note">结果尚未写入。</p>';
 if(result.cardinality===0){
  return `<div class="method-note"><strong>没有可配对的读数</strong><p>${esc(result.reason||'允许时差内没有初测—复测可配对。')}</p></div>`;
 }
 const problems=audit(result);
 if(problems.length)return `<div class="method-note"><strong>等待核对</strong><p>${problems.map(esc).join('；')}。核对通过前不展示上下界。</p></div>`;
 const hi=result.bound_upper.median,lo=result.bound_lower.median;
 return `<section class="result-intro"><strong>中位数区间：${esc(lo)} ～ ${esc(hi)} 百分点</strong>
  <p>${esc(result.interpretation)}</p></section>
 <div class="checksum">INPUT SHA256 ${esc(run.checksum)} · 冻结于 revision ${run.frozen_revision}</div>
 <h2>最优配对 ${result.cardinality} 份 <small>总时差 ${result.total_time_diff} 分钟 · 同成本配法全部纳入</small></h2>
 ${boundBlock('下界',result.bound_lower,result)}
 ${boundBlock('上界',result.bound_upper,result)}
 <h2>核对记录</h2><ul>${result.checks.map(c=>`<li>${esc(c)}</li>`).join('')}</ul>
 <details><summary>冻结参数与两册输入</summary><pre>${esc(JSON.stringify({parameters:run.parameters,snapshot:run.snapshot},null,2))}</pre></details>
 ${isAdmin?reviewForm(run):(run.review?`<div class="method-note"><strong>已复核</strong><p>${esc(run.review.note)}</p></div>`:'<p class="method-note">等待负责人复核。</p>')}`;
}

function boundBlock(label,b,result){
 return `<h3>${label}：中位数 ${esc(b.median)} 百分点</h3>
 ${table(['初测读数','初测采样','复测读数','复测采样','时差（分钟）','复测−初测'],
   b.pairing.map(r=>[r.initial_key,r.initial_time,r.retest_key,r.retest_time,r.time_diff,r.delta]))}`;
}

function reviewForm(run){
 if(run.review)return `<div class="method-note"><strong>已复核</strong><p>${esc(run.review.note)}</p><small>${esc(run.review.author)} · ${esc(run.review.created_at)}</small></div>`;
 return `<form id="pairing-review-form"><label>复核意见与后续动作<textarea name="note" minlength="5" maxlength="2000" required placeholder="写明配对范围、方法边界与下一步动作"></textarea></label><button type="submit">确认复核并归档</button></form>`;
}

export function bind(refresh){
 const sel=document.getElementById('pairing-study-select');
 if(sel)sel.onchange=()=>{pairingStore.studyId=+sel.value;refresh();};
 bindForm('pairing-study-form',async data=>{const s=await pairing.createStudy(data);pairingStore.studyId=s.id;await loadPairing();refresh();});
 bindForm('pairing-reading-form',async data=>{
  const row={booklet:data.booklet,source_key:data.source_key,sampled_at:+data.sampled_at,moisture:data.moisture};
  await pairing.correct(pairingStore.studyId,{readings:[row]});
  await loadPairing();refresh();
  document.getElementById('notice').textContent='已保存这次更正并递增版本号';
 });
 bindForm('pairing-run-form',async data=>{
  const r=await pairing.createRun(pairingStore.studyId,{name:data.name,tolerance_minutes:+data.tolerance_minutes});
  pairingStore.runId=r.id;await loadPairing();location.hash='pairing/'+r.id;
 });
 bindForm('pairing-review-form',async data=>{
  const id=Number(location.hash.split('/')[1]);await pairing.review(id,data.note);refresh();
 });
}
