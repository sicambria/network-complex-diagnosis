function renderResults(results,stackLayers,logDiv){
  renderStackLayers(stackLayers);
  let diagnoses=results.diagnosis||[];
  logDiv.textContent+='\n\n--- Results ---\nHealth Score: '+(results.health_score||'?')+'/100\n\nDiagnosis:';
  diagnoses.forEach(function(d){logDiv.textContent+='\n['+d.severity+'] ['+d.layer+'] '+d.title;if(d.detail)logDiv.textContent+='\n  '+d.detail;if(d.fix)logDiv.textContent+='\n  Fix: '+d.fix;});
  logDiv.scrollTop=logDiv.scrollHeight;
}

function updateDashboard(results){
  document.getElementById('health-val').textContent=results.health_score!=null?results.health_score:'--';
  let sig=results.wifi&&results.wifi.signal_dbm;document.getElementById('sig-val').textContent=sig!=null?sig+' dBm':'--';
  let gw=results.gateway_ping;document.getElementById('gw-val').textContent=gw?gw.p95_ms+'ms':'--';
  let sp=results.speedtest;document.getElementById('spd-val').textContent=sp&&sp.download_mbps?sp.download_mbps+'M':'--';
  initScoreChart(document.getElementById('scoreChart'),results.health_score);
  let events=document.getElementById('events-list');
  let items=[];
  (results.diagnosis||[]).forEach(function(d){if(d.severity!=='clean')items.push({dot:d.severity,text:'['+d.layer+'] '+d.title,time:results.timestamp});});
  if(!items.length&&results.timestamp)items.push({dot:'clean',text:'All clear',time:results.timestamp});
  events.innerHTML=items.map(function(i){return '<div class="event-bar"><span class="event-dot '+i.dot+'"></span><span>'+i.text+'</span><span style="color:var(--info);margin-left:auto">'+(i.time||'').slice(11,19)+'</span></div>';}).join('');
}

function toggleLiveMonitor(){
  if(liveTimer){stopLiveMonitor();}
  else{startLiveMonitor();}
}

function startLiveMonitor(){
  if(liveTimer)return;
  document.getElementById('live-container').style.display='block';
  document.getElementById('quality-card').style.display='block';
  document.getElementById('events-card').style.display='block';
  let btn=document.getElementById('live-toggle-btn');
  btn.textContent='Stop Monitoring';
  btn.className='btn btn-secondary';
  if(!liveChart && typeof Chart!=='undefined'){
    let c=document.getElementById('liveChart');
    if(c){
      try{
        liveChart=new Chart(c,{type:'line',data:{labels:[],datasets:[{label:'Signal dBm',data:[],borderColor:'#f97316',backgroundColor:'rgba(249,115,22,0.1)',fill:true,tension:0.3,pointRadius:2,borderWidth:2}]},options:{responsive:true,maintainAspectRatio:false,scales:{x:{display:true,ticks:{color:'#64748b',maxTicksLimit:10,font:{size:10}},grid:{color:'#334155'}},y:{display:true,ticks:{color:'#64748b',font:{size:10}},grid:{color:'#334155'}}},plugins:{legend:{display:false}}}});
      }catch(e){console.log('chart init failed',e);}
    }
  }
  liveData=[];liveDataLat=[];
  liveStartTime=Date.now();
  liveTimerSec=0;
  updateTimer();
  liveTimerTick=setInterval(function(){liveTimerSec++;updateTimer();},1000);
  fetch('/api/monitor/start',{method:'POST'}).catch(function(){});
  pollMonitor();
  liveTimer=setInterval(pollMonitor,1500);
}

function stopLiveMonitor(){
  if(liveTimer){clearInterval(liveTimer);liveTimer=null;}
  if(liveTimerTick){clearInterval(liveTimerTick);liveTimerTick=null;}
  fetch('/api/monitor/stop',{method:'POST'}).catch(function(){});
  let btn=document.getElementById('live-toggle-btn');
  btn.textContent='Start Monitoring';
  btn.className='btn btn-orange';
}

function updateTimer(){
  let m=Math.floor(liveTimerSec/60);
  let s=liveTimerSec%60;
  document.getElementById('live-timer').textContent=(m<10?'0':'')+m+':'+(s<10?'0':'')+s;
}

function arrStats(arr,key){
  if(!arr||arr.length<2)return null;
  var vals=arr.map(function(p){return p.v;}).filter(function(v){return v!=null;});
  if(vals.length<2)return null;
  vals.sort(function(a,b){return a-b;});
  var sum=0, i;
  for(i=0;i<vals.length;i++)sum+=vals[i];
  var avg=(sum/vals.length).toFixed(1);
  var med=vals.length%2?vals[(vals.length-1)/2]:(vals[vals.length/2-1]+vals[vals.length/2])/2;
  return {avg:avg,med:med.toFixed(1),min:vals[0].toFixed(1),max:vals[vals.length-1].toFixed(1)};
}

function updateLiveStats(){
  var ss=arrStats(liveData);
  var ls=arrStats(liveDataLat);
  if(ss){
    document.getElementById('stat-sig-avg').textContent=ss.avg;
    document.getElementById('stat-sig-med').textContent=ss.med;
    document.getElementById('stat-sig-min').textContent=ss.min;
    document.getElementById('stat-sig-max').textContent=ss.max;
  }
  if(ls){
    document.getElementById('stat-lat-avg').textContent=ls.avg;
    document.getElementById('stat-lat-med').textContent=ls.med;
    document.getElementById('stat-lat-min').textContent=ls.min;
    document.getElementById('stat-lat-max').textContent=ls.max;
  }
}

function pollMonitor(){
  var t0=Date.now();
  fetch('/api/monitor').then(function(r){return r.json();}).then(function(d){
    let sig=d.wifi&&d.wifi.signal_dbm;
    let el=document.getElementById('live-sig-val');
    let circle=document.getElementById('live-circle');
    let textEl=document.getElementById('live-sig-text');

    if(sig!=null){
      el.textContent=sig;
      let cls=sig>=-50?'green':sig>=-70?'yellow':'red';
      circle.className='live-signal-circle '+cls;
      if(sig>=-50)textEl.textContent='Excellent signal';
      else if(sig>=-60)textEl.textContent='Good signal';
      else if(sig>=-70)textEl.textContent='Fair signal';
      else if(sig>=-80)textEl.textContent='Weak signal';
      else textEl.textContent='Very weak signal';
    }else{
      el.textContent='--';
      circle.className='live-signal-circle red';
      textEl.textContent='No WiFi data';
    }

    let lat=d.gateway_latency_ms;
    let latEl=document.getElementById('live-latency');
    if(lat!=null){
      latEl.textContent=lat.toFixed(0);
      latEl.className='value '+(lat<50?'green':lat<150?'yellow':'red');
    }else{latEl.textContent='--';latEl.className='value';}

    let noise=d.wifi&&d.wifi.noise_dbm;
    let noiseEl=document.getElementById('live-noise');
    noiseEl.textContent=noise!=null?noise+' dBm':'N/A';

    let cu=d.wifi&&d.wifi.channel_util;
    let cuEl=document.getElementById('live-channel');
    if(cu!=null){cuEl.textContent=cu+'%';cuEl.className='value '+(cu<40?'green':cu<70?'yellow':'red');}
    else{cuEl.textContent='--';cuEl.className='value';}

    let ifaceEl=document.getElementById('live-iface');
    ifaceEl.textContent=d.wifi&&d.wifi.interface?d.wifi.interface:(d.wifi&&d.wifi.available?'wifi':'--');

    let ssid=d.wifi&&d.wifi.ssid;
    let ssidEl=document.getElementById('live-ssid');
    let ssidStatEl=document.getElementById('live-ssid-stat');
    if(ssid){
      ssidEl.textContent=ssid;
      ssidStatEl.textContent=ssid;
      ssidStatEl.className='value';
    }else{
      ssidEl.textContent='--';
      ssidStatEl.textContent='--';
      ssidStatEl.className='value';
    }

    if(sig!=null){
      liveData.push({t:new Date(),v:sig});
      if(liveData.length>60)liveData.shift();
    }
    if(lat!=null){
      liveDataLat.push({t:new Date(),v:lat});
      if(liveDataLat.length>60)liveDataLat.shift();
    }
    if(liveChart){
      liveChart.data.labels=liveData.map(function(p){return p.t.toLocaleTimeString();});
      liveChart.data.datasets[0].data=liveData.map(function(p){return p.v;});
      liveChart.update('none');
    }
    updateLiveStats();

    let health=d.health_score||0;
    let hBar=document.getElementById('live-health-bar');
    let hVal=document.getElementById('live-health-val');
    let hCls=health>=70?'good':health>=40?'warning':'bad';
    hBar.style.width=health+'%';
    hBar.className='bar-inner '+hCls;
    hVal.textContent=health;
    renderAdvancedMonitor(d.advanced);
    console.log('poll ok',Date.now()-t0+'ms','sig='+sig,'lat='+lat);
  }).catch(function(e){
    console.log('poll fail',Date.now()-t0+'ms',e);
  });
}

function targetLabel(key){
  if(key==='gateway')return 'Gateway (router)';
  if(key==='dns')return 'DNS resolution';
  if(key==='tcp')return 'TCP handshake';
  if(key.indexOf('external:')===0)return 'Internet ('+key.slice(9)+')';
  return key;
}

function lossClass(loss){
  if(loss==null)return '';
  if(loss>=5)return 'loss-bad';
  if(loss>0)return 'loss-warn';
  return 'loss-ok';
}

function renderAdvancedMonitor(adv){
  if(!adv)return;
  let tbody=document.getElementById('quality-tbody');
  let targets=adv.targets||{};
  let keys=Object.keys(targets);
  if(!keys.length){
    tbody.innerHTML='<tr><td colspan="6" class="empty-note">Collecting samples...</td></tr>';
  }else{
    tbody.innerHTML=keys.sort().map(function(key){
      let t=targets[key];
      let loss=t.loss_pct;
      return '<tr><td>'+targetLabel(key)+'</td>'+
        '<td class="'+lossClass(loss)+'">'+(loss!=null?loss.toFixed(1):'--')+'</td>'+
        '<td>'+(t.avg_ms!=null?t.avg_ms.toFixed(1):'--')+'</td>'+
        '<td>'+(t.jitter_ms!=null?t.jitter_ms.toFixed(1):'--')+'</td>'+
        '<td>'+(t.p95_ms!=null?t.p95_ms.toFixed(1):'--')+'</td>'+
        '<td>'+(t.samples||0)+'</td></tr>';
    }).join('');
  }

  let hintList=document.getElementById('hint-list');
  let hints=adv.hints||[];
  if(!hints.length){
    hintList.innerHTML='';
  }else{
    hintList.innerHTML=hints.map(function(h){
      return '<div class="hint '+(h.severity||'info')+'">'+h.text+'</div>';
    }).join('');
  }

  let eventList=document.getElementById('event-list');
  let events=adv.events||[];
  if(!events.length){
    eventList.innerHTML='<div class="empty-note">No events recorded yet.</div>';
  }else{
    eventList.innerHTML=events.map(function(ev){
      let start=(ev.start||'').split('T')[1]||ev.start;
      let end=(ev.end||'').split('T')[1]||ev.end;
      return '<div class="event-row"><span><b>'+targetLabel(ev.target)+'</b> failed '+ev.consecutive_failures+
        ' time(s)</span><span>'+start+' &rarr; '+end+'</span></div>';
    }).join('');
  }
}

function loadSessions(){
  Promise.all([
    fetch('/api/history').then(function(r){return r.json();}),
    fetch('/api/reports').then(function(r){return r.json();})
  ]).then(function(results){
    let sessions=results[0].sessions||[];
    let files=results[1].reports||[];
    let list=document.getElementById('sessions-list');
    let html='';
    if(sessions.length){
      html+='<div style="font-size:11px;color:var(--info);margin-bottom:8px">DIAGNOSTIC SESSIONS</div>';
      html+=sessions.map(function(s){
        let ts=s._file?s._file.replace('session_','').replace('.json',''):'';
        let score=s.health_score!=null?s.health_score:'?';
        let badCount=(s.diagnosis||[]).filter(function(d){return d.severity!=='clean';}).length;
        let summary=badCount>0?badCount+' issue(s)':'Clean';
        return '<div class="session-row">'+
          '<div class="session-info"><div class="time">'+ts.replace('_',' ')+'</div><div class="summary">Score '+score+'/100 &mdash; '+summary+'</div></div>'+
          '<div class="session-actions">'+
          '<button class="btn btn-secondary" onclick="viewSession(\''+s._file+'\')">View</button>'+
          '<button class="btn btn-secondary" onclick="exportReport(\''+s._file+'\',\'isp\')" title="Detailed evidence report to submit to your ISP">ISP report</button>'+
          '<button class="btn btn-secondary" onclick="exportReport(\''+s._file+'\',\'html\')">HTML</button>'+
          '<button class="btn btn-secondary" onclick="exportReport(\''+s._file+'\',\'json\')">JSON</button>'+
          '<button class="btn btn-secondary" onclick="exportReport(\''+s._file+'\',\'csv\')">CSV</button>'+
          '</div></div>';
      }).join('');
    }
    if(files.length){
      if(html)html+='<div style="border-top:1px solid var(--border);margin:12px 0"></div>';
      html+='<div style="font-size:11px;color:var(--info);margin-bottom:8px">REPORT FILES</div>';
      html+=files.map(function(f){
        let kb=(f.size/1024).toFixed(1);
        return '<div class="session-row"><div class="session-info"><div class="time">'+f.name+'</div><div class="summary">'+kb+' KB &mdash; '+f.mtime.slice(0,19).replace('T',' ')+'</div></div>'+
          '<div class="session-actions"><button class="btn btn-secondary" onclick="window.open(\'/api/report/'+f.name+'\')">Open</button></div></div>';
      }).join('');
    }
    if(list)list.innerHTML=html||'<p style="color:var(--info)">No sessions or reports yet. Run a diagnostic to create one.</p>';
  });
}



function viewSession(file){
  fetch('/api/session/'+file).then(function(r){return r.json();}).then(function(data){
    let stackLayers=document.createElement('div'),logDiv=document.getElementById('log-output');
    document.getElementById('prog-list').style.display='none';
    document.getElementById('health-live').style.display='none';
    logDiv.style.display='block';logDiv.textContent='';
    document.getElementById('stack-card').style.display='block';
    updateDashboard(data);
    renderResults(data,stackLayers,logDiv);
    switchTab('troubleshoot');
  });
}

function exportReport(file,format){
  window.open('/api/export/'+file+'?format='+format,'_blank');
}

document.addEventListener('visibilitychange',function(){if(document.hidden&&liveTimer)stopLiveMonitor();});
window.addEventListener('beforeunload',function(){if(liveTimer)stopLiveMonitor();});
fetch('/api/status').then(function(r){return r.json();}).then(function(s){if(s.results)updateDashboard(s.results);});
loadSessions();

// -- Tools Menu --------------------------------------------------------------------

let toolsMenuLoaded=false;
