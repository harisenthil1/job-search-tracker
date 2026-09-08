const $=id=>document.getElementById(id);
let state=null,dashboard=null,currentView=null,ws=null,toastTimer=null;
let stateReceivedAt=0,dashDebounce=null;
const fmtTime=s=>{s=Math.max(0,Math.floor(s||0));const m=Math.floor(s/60),sec=s%60;return `${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`};
const fmtMin=s=>`${Math.round((s||0)/60)}m`;
const esc=s=>String(s??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
async function api(url,opt={}){const r=await fetch(url,{headers:{'Content-Type':'application/json',...(opt.headers||{})},...opt});if(!r.ok){let m=await r.text();try{m=JSON.parse(m).detail||m}catch{}throw new Error(m)}return r.json()}
function toast(msg,error=false){const t=$('toast');t.textContent=msg;t.className=`toast ${error?'error':''}`;clearTimeout(toastTimer);toastTimer=setTimeout(()=>t.classList.add('hidden'),2800)}
function siteConfirm(message,title='Confirm'){return new Promise(resolve=>{const back=$('modalBackdrop'),ok=$('modalConfirm'),cancel=$('modalCancel');$('modalTitle').textContent=title;$('modalMessage').textContent=message;back.classList.remove('hidden');const done=v=>{back.classList.add('hidden');ok.onclick=null;cancel.onclick=null;back.onclick=null;document.onkeydown=null;resolve(v)};ok.onclick=()=>done(true);cancel.onclick=()=>done(false);back.onclick=e=>{if(e.target===back)done(false)};document.onkeydown=e=>{if(e.key==='Escape')done(false)}})}

function acceptState(next){state=next;stateReceivedAt=Date.now();if(!currentView||!state.buckets.some(b=>b.slug===currentView))currentView=state.buckets[0]?.slug||'dashboard'}
async function refresh(){const [s,d]=await Promise.all([api('/api/state'),api('/api/dashboard')]);acceptState(s);dashboard=d;render()}
function connectWS(){
  try{
    ws=new WebSocket(`ws://${location.host}/ws`);
    ws.onmessage=e=>{const x=JSON.parse(e.data);if(x.type==='state'){acceptState(x.state);render();refreshDashboardSoon()}};
    ws.onclose=()=>setTimeout(connectWS,750);
    ws.onerror=()=>{try{ws.close()}catch{}};
  }catch{setTimeout(connectWS,750)}
}
function refreshDashboardSoon(){clearTimeout(dashDebounce);dashDebounce=setTimeout(async()=>{try{dashboard=await api('/api/dashboard');renderDashboard()}catch{}},120)}

// State from the backend is authoritative at stateReceivedAt. Between backend
// events, only elapsed time changes, so project that locally instead of polling.
function liveDelta(){return state?.running?Math.max(0,(Date.now()-stateReceivedAt)/1000):0}
function liveBucketStatus(slug){
  const base=state?.bucket_status?.[slug];if(!base)return null;
  const delta=liveDelta(),active=state?.active?.bucket;
  const pct=Number(base.percentage||0)/100;
  const used=Number(base.used_seconds||0)+(active===slug?delta:0);
  const ledger=Number(base.ledger_seconds||0)+delta*((active===slug?1:0)-pct);
  const allowed=Number(base.allowed_seconds||0);
  return {...base,used_seconds:used,ledger_seconds:ledger,remaining_seconds:allowed-used,over:allowed>0?used>=allowed:used>0};
}
function liveTimeToday(slug){return Number(dashboard?.time_today?.[slug]||0)+(state?.running&&state?.active?.bucket===slug?liveDelta():0)}

function renderTabs(){const tabs=$('tabs');tabs.innerHTML=state.buckets.map(b=>`<button class="tab ${currentView===b.slug?'active':''}" data-view="${esc(b.slug)}">${esc(b.name)}</button>`).join('')}
function displayView(view){currentView=view;$('bucketView').classList.toggle('hidden',view==='dashboard');$('dashboardView').classList.toggle('hidden',view!=='dashboard');renderTabs();render()}
function render(){if(!state)return;renderTabs();renderBucket();renderDashboard()}
function renderBucket(){
  if(!state||currentView==='dashboard')return;
  const b=currentView,focus=state.focus[b],bs=liveBucketStatus(b);
  const runningHere=state.running&&state.active?.bucket===b;
  const blocked=state.running&&!runningHere;
  $('startBtn').disabled=state.running||!focus;$('stopBtn').disabled=!state.running;
  $('runningNotice').classList.toggle('hidden',!blocked);if(blocked)$('runningNotice').textContent=`${state.active.metro_name} is still running in ${state.active.bucket}. Stop first.`;
  $('bucketTimer').textContent=fmtTime(bs?.used_seconds||0);$('bucketTimer').classList.toggle('over',!!bs?.over);$('hardTodayTop').textContent=dashboard?.hard_today||0;
  const controls=!!state.settings.website_controls;$('websiteActions').classList.toggle('hidden',!controls);$('addEasy').disabled=!runningHere;$('addHard').disabled=!runningHere;
  $('focusMetroName').textContent=focus?.name||'Bucket complete';$('focusCbsa').textContent=focus?.cbsa||'';$('focusCard').classList.toggle('empty',!focus);
  const activePhase=runningHere&&focus?state.active.phase:null,swDone=!!focus?.software_done,hwDone=!!focus?.hardware_done,sw=$('focusSoftware'),hw=$('focusHardware');
  sw.textContent=swDone?'✓ Software':'Software';hw.textContent=hwDone?'✓ Hardware':'Hardware';sw.className=`focus-phase ${swDone?'done':''} ${activePhase==='software'?'active':''}`;hw.className=`focus-phase ${hwDone?'done':''} ${activePhase==='hardware'?'active':''}`;sw.disabled=!(runningHere&&focus&&activePhase==='software');hw.disabled=!(runningHere&&focus&&activePhase==='hardware');
  const rows=state.metros[b]||[];$('metroQueue').innerHTML=rows.map((m,i)=>{const done=m.software_done&&m.hardware_done,status=done?'Done':m.is_focus?'Current':m.software_done?'Hardware next':'Waiting';return `<div class="queue-row ${m.is_focus?'focus':''} ${done?'done':''}"><div>${i+1}</div><div><div class="metro-name">${esc(m.name)}</div><div class="cbsa">${esc(m.cbsa)}</div></div><div class="queue-status">${status}</div></div>`}).join('')||'<div class="queue-row"><div>—</div><div class="muted">No metros in this bucket.</div><div></div></div>';
}
function metric(label,value){return `<div class="metric"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`}
function renderDashboard(){if(!state||!dashboard)return;$('presetMinutes').value=state.settings.preset_minutes;$('dashHardTotal').textContent=dashboard.hard_total;$('dashHardToday').textContent=dashboard.hard_today;$('dashEasyToday').textContent=dashboard.easy_today;$('dashDailyAvg').textContent=dashboard.hard_daily_average.toFixed(1);$('websiteControls').checked=!!state.settings.website_controls;
  $('pctGrid').innerHTML=state.buckets.map(b=>{const bs=liveBucketStatus(b.slug)||{};return `<label><span>${esc(b.name)} %</span><input class="pct-input" data-bucket="${esc(b.slug)}" type="number" min="0" max="100" step="1" value="${Number(bs.percentage||0).toFixed(1)}"><small class="alloc-note">${fmtMin(bs.allowed_seconds||0)} this block</small></label>`}).join('');
  $('todayTimes').innerHTML=state.buckets.map(b=>metric(b.name,fmtMin(liveTimeToday(b.slug)))).join('')+metric('Total',fmtMin(state.buckets.reduce((a,b)=>a+liveTimeToday(b.slug),0)));
  $('avgTimes').innerHTML=state.buckets.map(b=>metric(b.name,fmtMin(dashboard.avg_time_per_active_day[b.slug]||0))).join('')+metric('Hard apps/day',dashboard.hard_daily_average.toFixed(1));
  $('ledger').innerHTML=state.buckets.map(b=>{const v=liveBucketStatus(b.slug)?.ledger_seconds||0;return metric(b.name,`${v>=0?'+':''}${fmtMin(v)}`)}).join('');renderChart(dashboard.chart,dashboard.trend)
}
function renderChart(data,trend){const svg=$('trendChart'),badge=$('trendBadge');badge.textContent=trend==='green'?'↗':'↘';badge.className=`trend-badge ${trend}`;if(!data.length){svg.innerHTML='<text x="28" y="125" fill="#91a0b2">No hard applications yet.</text>';return}const W=900,H=250,p=32,max=Math.max(1,...data.map(d=>d.hard));const pts=data.map((d,i)=>{const x=p+(W-2*p)*(data.length===1?.5:i/(data.length-1));const y=H-p-(H-2*p)*(d.hard/max);return [x,y]});const color=trend==='green'?'#76d79a':'#ff6574';svg.innerHTML=`<line x1="${p}" y1="${H-p}" x2="${W-p}" y2="${H-p}" stroke="#465468"/><polyline points="${pts.map(p=>p.join(',')).join(' ')}" fill="none" stroke="${color}" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/><text x="${p}" y="${H-7}" fill="#91a0b2">${data[0].date.slice(5)}</text><text x="${W-p-45}" y="${H-7}" fill="#91a0b2">${data[data.length-1].date.slice(5)}</text>`}

$('tabs').addEventListener('click',e=>{const t=e.target.closest('.tab');if(t)displayView(t.dataset.view)});$('dashboardBtn').onclick=()=>displayView('dashboard');
$('startBtn').onclick=async()=>{try{acceptState(await api('/api/timer/start',{method:'POST',body:JSON.stringify({bucket:currentView})}));render()}catch(e){toast(e.message,true)}};
$('stopBtn').onclick=async()=>{try{acceptState(await api('/api/timer/stop',{method:'POST',body:'{}'}));dashboard=await api('/api/dashboard');render()}catch(e){toast(e.message,true)}};
async function completePhase(phase){if(phase==='hardware'){const yes=await siteConfirm('Finish hardware for this metro? This cannot be undone for this cycle.','Finish hardware');if(!yes)return}try{acceptState(await api('/api/phase/complete',{method:'POST',body:JSON.stringify({phase,confirmed:true})}));render()}catch(err){toast(err.message,true)}}
$('focusSoftware').onclick=()=>{if(!$('focusSoftware').disabled)completePhase('software')};$('focusHardware').onclick=()=>{if(!$('focusHardware').disabled)completePhase('hardware')};
$('addEasy').onclick=()=>addApp('easy');$('addHard').onclick=()=>addApp('hard');async function addApp(type){try{acceptState(await api('/api/applications',{method:'POST',body:JSON.stringify({apply_type:type})}));render();refreshDashboardSoon()}catch(e){toast(e.message,true)}}
$('updatePreset').onclick=async()=>{const percentages={};document.querySelectorAll('.pct-input').forEach(i=>percentages[i.dataset.bucket]=+i.value);try{acceptState(await api('/api/preset',{method:'POST',body:JSON.stringify({minutes:+$('presetMinutes').value,percentages})}));dashboard=await api('/api/dashboard');render();toast('Preset updated.')}catch(e){toast(e.message,true)}};
$('resetBlock').onclick=async()=>{const yes=await siteConfirm('Start a fresh allocation block? Ledger and history stay intact.','Start new block');if(!yes)return;try{acceptState(await api('/api/block/reset',{method:'POST',body:'{}'}));dashboard=await api('/api/dashboard');render();toast('New block started.')}catch(e){toast(e.message,true)}};
$('websiteControls').onchange=async e=>{try{acceptState(await api('/api/settings/website-controls',{method:'POST',body:JSON.stringify({enabled:e.target.checked})}));render()}catch(err){toast(err.message,true)}};
$('syncData').onclick=async()=>{const yes=await siteConfirm('Sync CSV files from the data folder? Bucket percentages, ledger, bucket-time tracking, and current metro progress will reset. Historical applications and per-metro time will be kept.','Sync bucket data');if(!yes)return;try{const r=await api('/api/data/sync',{method:'POST',body:'{}'});acceptState(r.state);dashboard=await api('/api/dashboard');$('syncStatus').textContent=`Synced ${r.buckets} buckets / ${r.metros} metros.`;currentView=state.buckets[0]?.slug||'dashboard';render();toast('Bucket data synced.')}catch(e){toast(e.message,true)}};

// Smooth local clock/ledger update only. No network request is made here.
setInterval(()=>{if(!state?.running)return;if(currentView==='dashboard')renderDashboard();else renderBucket()},250);
refresh().then(connectWS).catch(e=>toast(e.message,true));
