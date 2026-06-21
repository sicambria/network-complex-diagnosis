let scoreChart=null, liveChart=null, liveTimer=null, liveTimerTick=null, liveData=[], liveDataLat=[], liveStartTime=0, liveTimerSec=0;
let isRunning=false;

function initScoreChart(canvas,val){if(!canvas)return;let ctx=canvas.getContext('2d');let ok=val==null?0:val;let color=ok>=70?'#22c55e':ok>=40?'#eab308':'#ef4444';if(scoreChart)scoreChart.destroy();scoreChart=new Chart(ctx,{type:'doughnut',data:{datasets:[{data:[ok,100-ok],backgroundColor:[color,'#334155'],borderWidth:0,circumference:180,rotation:270}],labels:['Score','']},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{enabled:false}}}});}

function getOpts(){
  return {
    speedtest: document.getElementById('opt-speedtest').checked,
    download_test: document.getElementById('opt-download').checked,
    connection_test: document.getElementById('opt-connection').checked,
    reliability_test: document.getElementById('opt-reliability').checked,
    wellknown_test: document.getElementById('opt-wellknown').checked,
    trace: document.getElementById('opt-trace').checked,
    bufferbloat: document.getElementById('opt-bufferbloat').checked,
    iperf3: document.getElementById('opt-iperf3').checked
  };
}

function saveOpts(){
  let k='netdiag_opts';
  try{localStorage.setItem(k,JSON.stringify(getOpts()));}catch(e){}
}

function loadOpts(){
  try{
    let d=JSON.parse(localStorage.getItem('netdiag_opts'));
    if(d){for(let k in d){let el=document.getElementById('opt-'+k);if(el)el.checked=d[k];}}
  }catch(e){}
}

function toggleOptions(){
  let p=document.getElementById('options-panel');
  p.classList.toggle('open');
}

function switchTab(tab){
  document.querySelectorAll('.tab-content').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('nav button').forEach(b=>b.classList.remove('active'));
  document.getElementById('tab-'+tab).classList.add('active');
  document.querySelector('nav button[data-tab="'+tab+'"]').classList.add('active');
  if(tab==='history')loadSessions();
  if(tab==='settings'){loadSettings();loadTools();}
  if(tab==='tools')loadToolsMenu();
  if(tab==='troubleshoot')startActivityPoll();else stopActivityPoll();
}

document.querySelectorAll('nav button').forEach(b=>b.addEventListener('click',function(){switchTab(b.dataset.tab);}));
loadOpts();

// -- Activity log ("under the hood") ---------------------------------------------

let activityTimer=null;

function renderActivity(items){
  let list=document.getElementById('activity-list');
  if(!list)return;
  if(!items||!items.length){list.innerHTML='<div class="empty-note">No activity yet.</div>';return;}
  list.innerHTML=items.map(function(a){
    let ts=(a.ts||'').split('T')[1]||a.ts||'';
    let okCls=a.ok?'ok':'fail';
    let okIcon=a.ok?'OK':'FAIL';
    let dur=a.duration_ms!=null?a.duration_ms.toFixed(1)+' ms':'';
    return '<div class="activity-row"><span class="ts">'+ts+'</span>'+
      '<span class="kind">'+(a.kind||'')+'</span>'+
      '<span class="alabel">'+(a.label||'')+'</span>'+
      '<span class="dur">'+dur+'</span>'+
      '<span class="'+okCls+'">'+okIcon+'</span></div>';
  }).join('');
}

function pollActivity(){
  fetch('/api/activity').then(function(r){return r.json();}).then(function(d){
    renderActivity(d.activity||[]);
  }).catch(function(){});
}

function startActivityPoll(){
  if(activityTimer)return;
  pollActivity();
  activityTimer=setInterval(pollActivity,2000);
}

function stopActivityPoll(){
  if(activityTimer){clearInterval(activityTimer);activityTimer=null;}
}

// -- Settings tab -------------------------------------------------------------------

function applyConfigToForm(cfg){
  document.getElementById('cfg-hosts').value=(cfg.hosts||[]).join(' ');
  document.getElementById('cfg-ping-count').value=cfg.ping_count;
  document.getElementById('cfg-ping-interval').value=cfg.ping_interval;
  document.getElementById('cfg-ping-timeout').value=cfg.ping_timeout;
  document.getElementById('cfg-dns-hosts').value=(cfg.dns_hosts||[]).join(' ');
  document.getElementById('cfg-dns-count').value=cfg.dns_count;
  document.getElementById('cfg-tcp-count').value=cfg.tcp_count;
  document.getElementById('cfg-monitor-external').value=(cfg.monitor_external_hosts||[]).join(' ');
  document.getElementById('cfg-monitor-dns').value=cfg.monitor_dns_host||'';
  let tcp=cfg.monitor_tcp_target||['1.1.1.1',443];
  document.getElementById('cfg-monitor-tcp-host').value=tcp[0];
  document.getElementById('cfg-monitor-tcp-port').value=tcp[1];
  document.getElementById('cfg-monitor-interval').value=cfg.monitor_interval;
}

function loadSettings(){
  fetch('/api/config').then(function(r){return r.json();}).then(function(cfg){
    applyConfigToForm(cfg);
    document.getElementById('settings-status').textContent='';
  }).catch(function(){
    document.getElementById('settings-status').textContent='Could not load settings.';
  });
}

function saveSettings(){
  let status=document.getElementById('settings-status');
  let body={
    hosts:document.getElementById('cfg-hosts').value.trim().split(/\s+/).filter(Boolean),
    ping_count:parseInt(document.getElementById('cfg-ping-count').value,10),
    ping_interval:parseFloat(document.getElementById('cfg-ping-interval').value),
    ping_timeout:parseInt(document.getElementById('cfg-ping-timeout').value,10),
    dns_hosts:document.getElementById('cfg-dns-hosts').value.trim().split(/\s+/).filter(Boolean),
    dns_count:parseInt(document.getElementById('cfg-dns-count').value,10),
    tcp_count:parseInt(document.getElementById('cfg-tcp-count').value,10),
    monitor_external_hosts:document.getElementById('cfg-monitor-external').value.trim().split(/\s+/).filter(Boolean),
    monitor_dns_host:document.getElementById('cfg-monitor-dns').value.trim(),
    monitor_tcp_target:[document.getElementById('cfg-monitor-tcp-host').value.trim(),parseInt(document.getElementById('cfg-monitor-tcp-port').value,10)],
    monitor_interval:parseFloat(document.getElementById('cfg-monitor-interval').value)
  };
  status.textContent='Saving...';
  fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}).then(function(r){return r.json();}).then(function(cfg){
    applyConfigToForm(cfg);
    status.textContent='Saved. Takes effect on the next diagnostic run / monitor restart.';
  }).catch(function(){
    status.textContent='Save failed.';
  });
}

function resetSettings(){
  fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
    hosts:['1.1.1.1','8.8.8.8','9.9.9.9','google.com'],
    dns_hosts:['google.com','cloudflare.com','quad9.net'],
    tcp_targets:[['1.1.1.1',443],['8.8.8.8',443],['google.com',443]],
    ping_count:20,ping_interval:0.5,ping_timeout:2,
    dns_count:10,tcp_count:10,monitor_interval:1.0,monitor_external_hosts:['1.1.1.1','8.8.8.8'],
    monitor_dns_host:'google.com',monitor_tcp_target:['1.1.1.1',443]
  })}).then(function(){loadSettings();});
}

function loadTools(){
  fetch('/api/tools').then(function(r){return r.json();}).then(function(d){
    let missing=new Set((d.missing_required||[]).concat(d.missing_optional||[]));
    let required=new Set(d.checked_required||[]);
    let all=(d.checked_required||[]).concat(d.checked_optional||[]);
    let tbody=document.getElementById('tools-tbody');
    tbody.innerHTML=all.map(function(t){
      let ok=!missing.has(t);
      let label=t+(required.has(t)?' (required)':'');
      return '<tr><td>'+label+'</td><td class="'+(ok?'tool-ok':'tool-missing')+'">'+(ok?'Available':'Missing')+'</td></tr>';
    }).join('');
    let hints=[];
    if(d.install_hint_required)hints.push('Required: '+d.install_hint_required);
    if(d.install_hint_optional)hints.push('Optional: '+d.install_hint_optional);
    document.getElementById('tools-hint').textContent=hints.join('  |  ')||'All checked tools are available.';
  }).catch(function(){});
}
document.querySelectorAll('#options-panel input').forEach(function(el){el.addEventListener('change',saveOpts);});

function pretty(l){if(l==='wellknown')return '100-site reproduction';if(l==='reliability')return 'Reliability';return l.replace(/_/g,' ').replace(/(^\w|\s\w)/g,function(m){return m.toUpperCase();});}

function sev(label,ok,total,rtt){
  if(total==null||total===0)return 'running';
  let loss=total>0?(1-(ok||0)/total)*100:0;
  if(label==='interface')return rtt>0?'bad':'clean';
  if(label==='wifi')return rtt!=null&&rtt<-80?'bad':(rtt!=null&&rtt<-70?'warning':'clean');
  if(label==='ethtool')return ok>0?'clean':'bad';
  if(label.startsWith('dns_')||label.startsWith('tcp_'))return loss>0?'bad':'clean';
  if(label==='tcp_sockets')return ok>0?'clean':'warning';
  if(label==='bufferbloat')return (rtt||0)>300?'bad':(rtt>200?'warning':'clean');
  if(label==='mtr')return ok>0?'clean':'warning';
  if(label==='speedtest')return rtt>0?(rtt<10?'warning':'clean'):'info';
  if(label==='iperf3')return rtt>0?'clean':'info';
  if(label==='download_test')return ok>0?'clean':'warning';
  if(label==='http_latency')return ok>0?'clean':'warning';
  if(label==='mtu_probe')return ok>0?'clean':'warning';
  if(label==='reliability'||label==='reliability_low'||label==='wellknown'||label==='wellknown_low')return (ok||0)>0?'clean':'info';
  if(loss>=5)return 'bad';
  if(loss>=1)return 'warning';
  if(rtt!=null&&rtt>=300)return 'bad';
  if(rtt!=null&&rtt>=150)return 'warning';
  if(rtt!=null&&rtt>=80)return 'warning';
  return 'clean';
}

function summary(label,ok,total,rtt,st){
  if(st==='running')return 'Running...';
  if(st==='error')return 'Failed';
  if(label==='interface')return rtt>0?rtt+' errors':'No errors';
  if(label==='wifi')return rtt!=null?rtt+' dBm':'N/A';
  if(label==='ethtool')return ok>0?'Full duplex':'Half duplex';
  if(label.startsWith('dns_')||label.startsWith('tcp_')){
    let loss=total>0?(1-(ok||0)/total)*100:0;
    return (ok||0)+'/'+total+', '+(rtt||'?')+'ms'+(loss>0?', '+loss.toFixed(0)+'% loss':'');
  }
  if(label==='tcp_sockets')return ok>0?'Clean':'Retrans: '+rtt+'%';
  if(label==='bufferbloat')return ((rtt||0)/100).toFixed(1)+'x';
  if(label==='mtr')return ok>0?'Route clean':'Mid-route loss (check findings)';
  if(label==='speedtest')return rtt>0?(rtt+' Mbps'):'Not available';
  if(label==='iperf3')return rtt>0?(rtt+' Mbps'):'Inconclusive';
  if(label==='download_test')return ok+' images ok, '+(rtt||0)+' Mbps agg';
  if(label==='http_latency')return ok+'/'+total+' hosts OK';
  if(label==='mtu_probe')return rtt+' MTU';
  if(label==='reliability'||label==='reliability_low'||label==='wellknown'||label==='wellknown_low')return (ok||0)+'/'+(total||'?')+' trials ok';
  if(total>0){
    let loss=total>0?(1-(ok||0)/total)*100:0;
    return (rtt||'?')+'ms, '+loss.toFixed(1)+'% loss';
  }
  return 'Done';
}

function startDiagnostic(opts,dash){
  let prefix=dash?'dash-':'';
  let statusEl=document.getElementById(prefix+'run-status');
  let progressEl=document.getElementById(prefix+'progress');
  let fillEl=document.getElementById(prefix+'progress-fill');
  let labelEl=document.getElementById(prefix+'progress-label');
  let btn=document.getElementById(prefix+'run-btn');
  let stopBtn=dash?null:document.getElementById('stop-btn');
  let logDiv=document.getElementById('log-output');
  let progList=document.getElementById('prog-list');
  let stack=document.getElementById('stack-card');
  let stackLayers=document.getElementById('stack-layers');
  let healthLive=document.getElementById('health-live');
  let healthInner=document.getElementById('health-bar-inner');
  let healthVal=document.getElementById('health-live-val');

  isRunning=true;
  if(btn){btn.disabled=true;btn.textContent='Running...';}
  if(stopBtn){stopBtn.style.display='';stopBtn.disabled=false;stopBtn.textContent='Stop';}
  if(statusEl)statusEl.textContent='Running...';
  if(progressEl)progressEl.style.display='block';
  if(logDiv)logDiv.textContent='';
  if(progList){progList.innerHTML='';progList.style.display='block';}
  if(healthLive)healthLive.style.display='none';
  if(stack)stack.style.display='none';
  if(fillEl){fillEl.style.width='0%';fillEl.className='progress-bar-fill';}
  if(labelEl)labelEl.textContent='';
  let pctElInit=document.getElementById(prefix+'progress-pct');
  if(pctElInit)pctElInit.textContent='';

  fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(opts||getOpts())}).then(function(r){return r.json();}).then(function(data){
    if(data.status!=='ok'){if(statusEl)statusEl.textContent='Error: '+data.message;if(btn){btn.disabled=false;btn.textContent='Start Diagnosis';}if(stopBtn)stopBtn.style.display='none';isRunning=false;return;}
    function poll(){
      fetch('/api/status').then(function(r){return r.json();}).then(function(s){
        let entries=Object.values(s.progress||{});
        let run=entries.filter(function(e){return e.status==='running';}).length;
        let don=entries.filter(function(e){return e.status!=='running';}).length;
        let tot=run+don;
        let pct=tot>0?Math.round(don/tot*100):0;
        let runningProbe=entries.find(function(e){return e.status==='running';});
        if(fillEl){fillEl.style.width=pct+'%';fillEl.className='progress-bar-fill running';}
        if(labelEl)labelEl.textContent=runningProbe?'Checking '+pretty(runningProbe.label)+'...':don+'/'+tot+' checks';
        let pctEl=document.getElementById(prefix+'progress-pct');
        if(pctEl)pctEl.textContent=pct+'%';

        let html='',hScore=0,hCnt=0;
        for(let i=0;i<entries.length;i++){
          let e=entries[i];
          let c=sev(e.label,e.ok,e.total,e.rtt_ms);
          if(e.status!=='running'){
            hScore+=c==='clean'?100:c==='warning'?50:0;
            hCnt++;
          }
          html+='<div class="prog-entry '+c+'"><span class="prog-dot '+c+'"></span><span class="prog-name">'+pretty(e.label)+'</span><span class="prog-result">'+summary(e.label,e.ok,e.total,e.rtt_ms,e.status)+'</span></div>';
        }
        if(progList)progList.innerHTML=html;

        if(hCnt>0&&healthLive){
          healthLive.style.display='flex';
          let avg=Math.round(hScore/hCnt);
          let cls=avg>=70?'good':(avg>=40?'warning':'bad');
          if(healthInner){healthInner.style.width=avg+'%';healthInner.className='health-bar-inner '+cls;}
          if(healthVal)healthVal.textContent=avg;
        }

        if(s.status==='done'||s.status==='error'||s.status==='stopped'){
          isRunning=false;
          if(stopBtn){stopBtn.style.display='none';stopBtn.disabled=false;stopBtn.textContent='Stop';}
          if(s.status==='error'){if(statusEl)statusEl.textContent='Error: '+s.error;if(btn){btn.disabled=false;btn.textContent='Start Diagnosis';}return;}
          if(statusEl)statusEl.textContent=(s.status==='stopped')?'Stopped — partial results below.':'Diagnostic complete.';
          if(fillEl){fillEl.style.width='100%';fillEl.className='progress-bar-fill';}
          if(labelEl)labelEl.textContent=(s.status==='stopped')?'Stopped':'All checks complete';
          let pctElDone=document.getElementById(prefix+'progress-pct');
          if(pctElDone)pctElDone.textContent=(s.status==='stopped')?'':'100%';
          if(btn){btn.disabled=false;btn.textContent='Start Diagnosis';}
          if(stack&&s.results){stack.style.display='block';renderResults(s.results,stackLayers,logDiv);}
          if(s.results&&s.results.health_score!=null){updateDashboard(s.results);}
        }else{setTimeout(poll,500);}
      }).catch(function(){setTimeout(poll,500);});
    }
    poll();
  }).catch(function(e){if(statusEl)statusEl.textContent='Error: '+e;if(btn){btn.disabled=false;btn.textContent='Start Diagnosis';}if(stopBtn)stopBtn.style.display='none';isRunning=false;});
}

function stopDiagnostic(){
  let stopBtn=document.getElementById('stop-btn');
  let statusEl=document.getElementById('run-status');
  if(stopBtn){stopBtn.disabled=true;stopBtn.textContent='Stopping...';}
  if(statusEl)statusEl.textContent='Stopping... (finishing current probe)';
  fetch('/api/stop',{method:'POST'}).catch(function(){});
}

function dashRunDiag(){
  startDiagnostic({speedtest:false,download_test:false,connection_test:false,trace:false,bufferbloat:false,iperf3:false},true);
}

function runDiagnostic(){
  startDiagnostic(null,false);
}

function ndEsc(t){return String(t==null?'':t).replace(/[&<>]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c];});}
function ndSevRank(s){var m={bad:0,warning:1,info:2,clean:3};return m[s]==null?2:m[s];}

// Single source of truth: the cards below render diagnose()'s output verbatim, so
// the icon, severity and fix can never disagree (the old per-card recompute caused
// 'red X + No specific fix needed'). Findings interpret; Measurements are raw.
function renderStackLayers(container){
  fetch('/api/status').then(function(r){return r.json();}).then(function(s){
    if(!s.results)return;
    container.innerHTML=ndFindingsHtml(s.results)+ndMeasurementsHtml(s.results);
  });
}

function ndFindingsHtml(r){
  var icon={clean:'O',warning:'!',bad:'X',info:'i'};
  var diag=(r.diagnosis||[]).slice().sort(function(a,b){return ndSevRank(a.severity)-ndSevRank(b.severity);});
  var html='<div class="sect-head">Findings</div>'+
    '<div class="sect-sub">Each finding separates what was measured (facts) from what we infer (interpretation). Click to expand.</div>';
  if(!diag.length)return html+'<div class="fhint">No findings yet.</div>';
  html+=diag.map(function(d){
    var sev=d.severity||'info';
    var conf=d.confidence?'<span class="conf-badge '+ndEsc(d.confidence)+'">'+ndEsc(d.confidence)+' confidence</span>':'';
    var body='';
    if(d.facts&&d.facts.length)
      body+='<div class="fblk facts"><div class="lbl">Measured facts</div><ul>'+d.facts.map(function(f){return '<li>'+ndEsc(f)+'</li>';}).join('')+'</ul></div>';
    if(d.assumption)
      body+='<div class="fblk assume"><div class="lbl">Interpretation (what we infer, and why)</div><p>'+ndEsc(d.assumption)+'</p></div>';
    if(d.fix)
      body+='<div class="fblk fix"><div class="lbl">What to do</div><p>'+ndEsc(d.fix)+'</p></div>';
    if(!body)body='<div class="fhint">No further detail for this item.</div>';
    return '<div class="finding '+sev+'" onclick="this.classList.toggle(\'expanded\')">'+
      '<div class="finding-head">'+
        '<div class="stack-icon '+sev+'">'+(icon[sev]||'?')+'</div>'+
        '<div class="finding-htext"><div class="finding-title">'+ndEsc(d.title)+conf+'</div>'+
        (d.detail?'<div class="finding-detail">'+ndEsc(d.detail)+'</div>':'')+'</div>'+
        '<span class="finding-layer">'+ndEsc(d.layer||'')+'</span>'+
      '</div><div class="finding-body">'+body+'</div></div>';
  }).join('');
  return html;
}

function ndMeasurementsHtml(r){
  var recon=r.icmp_reconciliation||{};
  var filtered={};(recon.filtered_hosts||[]).forEach(function(h){filtered[h]=1;});
  var rows=[];
  function add(k,v,note){rows.push({k:k,v:v,note:note||''});}
  if(r.interface&&r.interface.available){var rx=r.interface.rx||{},tx=r.interface.tx||{};
    add('Interface errors/drops',(rx.errors||0)+(tx.errors||0)+(rx.dropped||0)+(tx.dropped||0));}
  if(r.wifi&&r.wifi.available&&r.wifi.signal_dbm!=null)add('WiFi signal',r.wifi.signal_dbm+' dBm');
  if(r.ethtool&&r.ethtool.available&&r.ethtool.duplex)add('Link duplex',r.ethtool.duplex);
  if(r.gateway_ping)add('Gateway (hop 1)',(r.gateway_ping.loss_pct==null?'?':r.gateway_ping.loss_pct)+'% loss, p95 '+(r.gateway_ping.p95_ms==null?'?':r.gateway_ping.p95_ms)+' ms');
  (r.internet_ping||[]).forEach(function(p){
    add('Ping '+p.label,(p.loss_pct==null?'?':p.loss_pct)+'% ICMP loss, p95 '+(p.p95_ms==null?'?':p.p95_ms)+' ms',
        filtered[p.host]?'ICMP rate-limited by host — not real packet loss':'');});
  (r.tcp||[]).forEach(function(t){var ok=(t.attempts||0)-(t.failures||0);
    add('TCP '+t.host+':'+t.port,ok+'/'+(t.attempts||'?')+' connects, p95 '+(t.p95_ms==null?'?':t.p95_ms)+' ms');});
  (r.dns||[]).forEach(function(d){add('DNS '+d.host,(d.failure_pct||0)+'% fail, p95 '+(d.p95_ms==null?'?':d.p95_ms)+' ms');});
  if(r.bufferbloat&&r.bufferbloat.available&&r.bufferbloat.ratio!=null)add('Bufferbloat ratio',r.bufferbloat.ratio.toFixed(1)+'x');
  if(r.mtr&&r.mtr.hops&&r.mtr.hops.length){var last=r.mtr.hops[r.mtr.hops.length-1];
    add('Route (MTR)',r.mtr.hops.length+' hops, destination loss '+(last.loss_pct||0)+'%');}
  if(r.download_test&&r.download_test.error==null){var dt=r.download_test;
    add('Small-image fetch',(dt.success||0)+'/'+((dt.success||0)+(dt.failures||0))+' ok, '+(dt.avg_mbps||0)+' Mbps aggregate');}
  if(r.connection_test&&r.connection_test.http_latency){var hl=r.connection_test.http_latency;
    var okc=hl.filter(function(h){return !(h.failures>0);}).length;add('HTTP reachability',okc+'/'+hl.length+' hosts ok');}
  if(r.connection_test&&r.connection_test.mtu&&r.connection_test.mtu.available)add('Path MTU',r.connection_test.mtu.mtu+' bytes');
  if(r.speedtest){if(r.speedtest.available&&r.speedtest.download_mbps!=null)add('Speed test',r.speedtest.download_mbps+' Mbps down');else add('Speed test','not available / inconclusive');}
  if(r.reliability_test&&r.reliability_test.available){var rt=r.reliability_test;
    add('Reliability probe',(rt.first_attempt_fail_pct==null?'?':rt.first_attempt_fail_pct)+'% first-attempt fail, '+(rt.recovered_on_retry||0)+' recovered on retry');}
  if(r.wellknown_test&&r.wellknown_test.available){var wt=r.wellknown_test;
    add('100-site reproduction',(wt.samples_total||0)+' attempts to '+(wt.site_count||'?')+' sites, '+(wt.first_attempt_fail_pct==null?'?':wt.first_attempt_fail_pct)+'% first-attempt fail');}
  if(!rows.length)return '';
  return '<div class="sect-head">Measurements</div>'+
    '<div class="sect-sub">Raw values, no judgement applied. The Findings above interpret these numbers.</div>'+
    '<div class="meas-grid">'+rows.map(function(x){
      return '<div class="meas-row"><span class="mk">'+ndEsc(x.k)+'</span><span class="mv">'+ndEsc(x.v)+
        (x.note?'<span class="note">'+ndEsc(x.note)+'</span>':'')+'</span></div>';}).join('')+'</div>';
}
