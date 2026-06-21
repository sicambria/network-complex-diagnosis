function loadToolsMenu(){
  if(toolsMenuLoaded)return;
  fetch('/api/tools/menu').then(function(r){return r.json();}).then(function(d){
    var container=document.getElementById('tools-container');
    if(!container)return;
    var tools=d.tools||[];
    if(!tools.length){container.innerHTML='<div class="empty-note">No tools available.</div>';return;}
    var html='';
    var layers=[1,2,3,4,5];
    var layerNames={1:'Physical (L1)',2:'Data Link (L2)',3:'Network (L3)',4:'Transport (L4)',5:'Application (L5-7)'};
    var layerDescs={1:'Cables, signal, interface hardware errors',2:'WiFi, switching, frame-level issues',3:'IP routing, ICMP, path MTU, gateway',4:'TCP/UDP, connections, retransmits, throughput',5:'DNS, HTTP, speed tests, bufferbloat'};
    for(var li=0;li<layers.length;li++){
      var layerNum=layers[li];
      var layerTools=tools.filter(function(t){return t.layer===layerNum;});
      if(!layerTools.length)continue;
      html+='<div class="card" style="margin-top:16px;padding:12px 16px">';
      html+='<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">';
      html+='<span style="font-size:14px;font-weight:700;color:var(--accent)">Layer '+layerNum+' &mdash; '+layerNames[layerNum]+'</span>';
      html+='<span style="font-size:11px;color:var(--info)">'+layerDescs[layerNum]+'</span>';
      html+='</div>';
      for(var ti=0;ti<layerTools.length;ti++){
        html+=renderToolCard(layerTools[ti]);
      }
      html+='</div>';
    }
    container.innerHTML=html;
    toolsMenuLoaded=true;
  }).catch(function(e){console.log('tools menu load error',e);});
}

function renderToolCard(tool){
  var html='<div class="tool-card" id="tool-card-'+tool.id+'">';
  html+='<div class="tool-layer">'+(tool.layer===0?'':tool.layer_name)+'</div>';
  html+='<h3>'+tool.name+'</h3>';
  html+='<div class="tool-desc">'+tool.desc+'</div>';
  if(tool.docs)html+='<div class="tool-docs">'+tool.docs+'</div>';
  if(tool.presets&&tool.presets.length){
    html+='<div class="tool-presets">';
    for(var pi=0;pi<tool.presets.length;pi++){
      var p=tool.presets[pi];
      html+='<button class="tool-preset-btn" onclick="applyPreset(\''+tool.id+'\',\''+p.name.replace(/'/g,"\\'")+'\')">'+p.name+'</button>';
    }
    html+='</div>';
  }
  if(tool.params&&tool.params.length){
    html+='<div class="tool-params" id="tool-params-'+tool.id+'">';
    for(var pi=0;pi<tool.params.length;pi++){
      var p=tool.params[pi];
      var ptype=p.type||'text';
      if(ptype==='checkbox'){
        html+='<div class="tool-param-row"><label style="flex-direction:row;align-items:center;gap:6px;cursor:pointer">';
        html+='<input id="tool-param-'+tool.id+'-'+p.key+'" class="tool-param" type="checkbox" data-tool="'+tool.id+'" data-key="'+p.key+'"'+(p.default?' checked':'')+'>';
        html+=p.label+'</label></div>';
      }else{
        var extra='';
        if(ptype==='number'){
          if(p.min!=null)extra+=' min="'+p.min+'"';
          if(p.max!=null)extra+=' max="'+p.max+'"';
          if(p.step!=null)extra+=' step="'+p.step+'"';
        }
        html+='<div class="tool-param-row"><label for="tool-param-'+tool.id+'-'+p.key+'">'+p.label+'</label>';
        html+='<input id="tool-param-'+tool.id+'-'+p.key+'" class="tool-param" type="'+ptype+'" value="'+p.default+'" data-tool="'+tool.id+'" data-key="'+p.key+'"'+extra+'>';
        html+='</div>';
      }
    }
    html+='</div>';
  }
  html+='<div class="tool-actions">';
  html+='<button class="btn" id="tool-run-'+tool.id+'" onclick="runTool(\''+tool.id+'\')">Run</button>';
  html+='<span class="tool-status" id="tool-status-'+tool.id+'"></span>';
  html+='</div>';
  html+='<div class="tool-result" id="tool-result-'+tool.id+'"></div>';
  html+='</div>';
  return html;
}

function getParamValue(el){
  if(el.type==='checkbox')return el.checked;
  return el.value;
}

function applyPreset(toolId,presetName){
  fetch('/api/tools/menu').then(function(r){return r.json();}).then(function(d){
    var tools=d.tools||[];
    for(var i=0;i<tools.length;i++){
      if(tools[i].id===toolId){
        var presets=tools[i].presets||[];
        for(var j=0;j<presets.length;j++){
          if(presets[j].name===presetName){
            var vals=presets[j].values;
            for(var k in vals){
              var el=document.getElementById('tool-param-'+toolId+'-'+k);
              if(el){
                if(el.type==='checkbox')el.checked=!!vals[k];
                else el.value=vals[k];
              }
            }
            return;
          }
        }
      }
    }
  });
}

function runTool(toolId){
  var btn=document.getElementById('tool-run-'+toolId);
  var statusEl=document.getElementById('tool-status-'+toolId);
  var resultEl=document.getElementById('tool-result-'+toolId);
  var card=document.getElementById('tool-card-'+toolId);
  if(!btn||btn.disabled)return;
  btn.disabled=true;
  btn.textContent='Running...';
  statusEl.textContent='Running...';
  resultEl.classList.remove('show');
  resultEl.textContent='';
  if(card)card.className='tool-card running';
  var params={};
  document.querySelectorAll('#tool-params-'+toolId+' .tool-param').forEach(function(el){
    params[el.dataset.key]=getParamValue(el);
  });
  fetch('/api/tool/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tool_id:toolId,params:params})}).then(function(r){
    return r.json();
  }).then(function(data){
    if(data.error){
      statusEl.textContent='Error: '+data.error;
      if(card)card.className='tool-card error';
      btn.disabled=false;
      btn.textContent='Run';
      return;
    }
    statusEl.textContent='Running...';
    pollToolResult(toolId);
  }).catch(function(e){
    statusEl.textContent='Request failed';
    if(card)card.className='tool-card error';
    btn.disabled=false;
    btn.textContent='Run';
  });
}

function pollToolResult(toolId){
  var btn=document.getElementById('tool-run-'+toolId);
  var statusEl=document.getElementById('tool-status-'+toolId);
  var resultEl=document.getElementById('tool-result-'+toolId);
  var card=document.getElementById('tool-card-'+toolId);
  fetch('/api/tool/status').then(function(r){return r.json();}).then(function(s){
    if(s.running){
      setTimeout(function(){pollToolResult(toolId);},300);
      return;
    }
    btn.disabled=false;
    btn.textContent='Run';
    if(s.error){
      statusEl.textContent='Error';
      if(card)card.className='tool-card error';
      resultEl.textContent='ERROR: '+s.error;
      resultEl.classList.add('show');
    }else if(s.result){
      statusEl.textContent='Done';
      if(card)card.className='tool-card done';
      renderToolResult(toolId,s.result,resultEl);
      resultEl.classList.add('show');
    }else{
      statusEl.textContent='No result';
      if(card)card.className='tool-card';
    }
  }).catch(function(e){
    setTimeout(function(){pollToolResult(toolId);},500);
  });
}

function renderToolResult(toolId,result,el){
  if(!result){el.textContent='(no result)';return;}
  if(typeof result==='string'){el.textContent=result;return;}
  if(result instanceof Array){
    el.textContent=JSON.stringify(result,null,2);
    return;
  }
  if(toolId==='diagnose_engine'){
    var diags=result.diagnosis||result||[];
    if(diags instanceof Array){
      var dhtml='';
      for(var di=0;di<diags.length;di++){
        var d=diags[di];
        var sev=d.severity||'info';
        var icon={clean:'✓',warning:'!',bad:'✗',info:'i'}[sev]||'?';
        dhtml+='<div style="padding:8px 10px;margin:4px 0;border-radius:4px;border-left:3px solid '+
          ({clean:'#22c55e',warning:'#eab308',bad:'#ef4444',info:'#38bdf8'}[sev]||'#64748b')+
          ';background:var(--card)">';
        dhtml+='<div style="font-weight:600;font-size:12px">'+icon+' ['+d.layer+'] '+d.title+'</div>';
        if(d.detail)dhtml+='<div style="font-size:11px;color:var(--info);margin-top:2px">'+d.detail+'</div>';
        if(d.fix)dhtml+='<div style="font-size:11px;color:var(--accent);margin-top:2px">Fix: '+d.fix+'</div>';
        dhtml+='</div>';
      }
      el.innerHTML=dhtml||'<span class="rval">No diagnoses</span>';
      return;
    }
  }
  if(toolId==='reliability_test'){
    if(result.available===false){el.innerHTML='<span class="rerr">'+(result.error||'unavailable')+'</span>';return;}
    var col={clean:'#22c55e',warning:'#eab308',bad:'#ef4444',info:'#38bdf8'};
    var ic={clean:'✓',warning:'!',bad:'✗',info:'i'};
    var h='';
    var vs=result.verdict||[];
    for(var i=0;i<vs.length;i++){var v=vs[i];var s=v.severity||'info';
      h+='<div style="padding:8px 10px;margin:4px 0;border-radius:4px;border-left:3px solid '+(col[s]||'#64748b')+';background:var(--card)">';
      h+='<div style="font-weight:600;font-size:12px">'+(ic[s]||'?')+' '+v.title+'</div>';
      if(v.detail)h+='<div style="font-size:11px;color:var(--info);margin-top:2px">'+v.detail+'</div>';
      if(v.fix)h+='<div style="font-size:11px;color:var(--accent);margin-top:2px">Fix: '+v.fix+'</div>';
      h+='</div>';}
    h+='<div style="font-size:11px;margin-top:8px"><span class="rkey">Samples</span>: <span class="rval">'+(result.samples_total||0)+'</span> &nbsp; ';
    h+='<span class="rkey">First-attempt fail</span>: <span class="rval">'+(result.first_attempt_fail_pct==null?'?':result.first_attempt_fail_pct)+'%</span> &nbsp; ';
    h+='<span class="rkey">Recovered on retry</span>: <span class="rval">'+(result.recovered_on_retry||0)+'</span> &nbsp; ';
    h+='<span class="rkey">Hard failures</span>: <span class="rval">'+(result.hard_failures||0)+'</span></div>';
    function tbl(rows){var t='<table style="width:100%;font-size:11px;margin-top:6px;border-collapse:collapse">';for(var r=0;r<rows.length;r++){t+='<tr>';for(var c=0;c<rows[r].length;c++){var tag=r===0?'th':'td';t+='<'+tag+' style="text-align:left;padding:2px 6px;border-bottom:1px solid var(--border)">'+rows[r][c]+'</'+tag+'>';}t+='</tr>';}return t+'</table>';}
    var fam=result.by_family||{};var famRows=[['Family','Samples','First-fail %','Hard-fail %']];
    for(var k in fam){famRows.push([k,fam[k].samples,fam[k].first_fail_pct,fam[k].hard_fail_pct]);}
    if(famRows.length>1)h+='<div class="rkey" style="margin-top:8px">IPv4 vs IPv6</div>'+tbl(famRows);
    var bc=result.by_concurrency||{};
    if(bc.low||bc.high){h+='<div class="rkey" style="margin-top:8px">Low vs high concurrency</div>'+tbl([['Mode','First-fail %','Samples'],['sequential',(bc.low?bc.low.first_fail_pct:'-'),(bc.low?bc.low.samples:'-')],['concurrent',(bc.high?bc.high.first_fail_pct:'-'),(bc.high?bc.high.samples:'-')]]);}
    var bt=result.by_target||[];if(bt.length){var tr=[['Target','IP?','First-fail %','Hard-fail %','TLS p95']];for(var i=0;i<bt.length;i++){var t=bt[i];tr.push([t.host,(t.is_ip?'yes':'no'),t.first_fail_pct,t.hard_fail_pct,(t.tls_p95==null?'-':t.tls_p95)]);}h+='<div class="rkey" style="margin-top:8px">Per target</div>'+tbl(tr);}
    el.innerHTML=h;return;
  }
  if(toolId==='health_score_tool'){
    var hs=result.health_score;
    if(hs!=null){
      var cls=hs>=70?'rval':(hs>=40?'rwarn':'rerr');
      el.innerHTML='<span style="font-size:24px;font-weight:700" class="'+cls+'">'+hs+'</span><span style="font-size:14px;color:var(--info);margin-left:8px">/ 100</span>';
      return;
    }
  }
  if(toolId==='classify_ping'){
    var cls=result.classification||'unknown';
    var ccls={clean:'rval',bad_loss:'rerr',some_loss:'rwarn',bad_latency_spikes:'rerr',latency_spikes:'rwarn',high_jitter:'rwarn'}[cls]||'';
    el.innerHTML='<span style="font-size:18px;font-weight:700" class="'+ccls+'">'+cls.replace(/_/g,' ')+'</span>';
    var host=result.host||'';
    if(host)el.innerHTML+='<br><span class="rkey">Host</span>: <span class="rval">'+host+'</span>';
    return;
  }
  if(toolId==='check_tools'){
    var html='';
    var all=(result.checked_required||[]).concat(result.checked_optional||[]);
    var missing=new Set((result.missing_required||[]).concat(result.missing_optional||[]));
    html+='<table style="width:auto;border-collapse:collapse;font-size:12px">';
    html+='<tr><th style="padding:4px 12px;text-align:left;color:var(--info)">Tool</th><th style="padding:4px 12px;text-align:left;color:var(--info)">Status</th></tr>';
    for(var ci=0;ci<all.length;ci++){
      var ok=!missing.has(all[ci]);
      html+='<tr><td style="padding:3px 12px">'+all[ci]+'</td><td style="padding:3px 12px" class="'+(ok?'rval':'rerr')+'">'+(ok?'Available':'Missing')+'</td></tr>';
    }
    html+='</table>';
    if(result.install_hint_required)html+='<div style="margin-top:6px;font-size:11px;color:var(--info)">'+result.install_hint_required+'</div>';
    if(result.install_hint_optional)html+='<div style="font-size:11px;color:var(--info)">'+result.install_hint_optional+'</div>';
    el.innerHTML=html;
    return;
  }
  if(toolId==='full_diagnostic'){
    var highlights=[];
    if(result.health_score!=null){
      var sc=result.health_score;
      var scls=sc>=70?'rval':(sc>=40?'rwarn':'rerr');
      highlights.push('<span class="rkey">Health Score</span>: <span class="'+scls+'" style="font-size:18px;font-weight:700">'+sc+'</span><span style="font-size:12px;color:var(--info)">/100</span>');
    }
    if(result.gateway)highlights.push('<span class="rkey">Gateway</span>: <span class="rval">'+result.gateway+'</span>');
    if(result.default_interface)highlights.push('<span class="rkey">Interface</span>: <span class="rval">'+result.default_interface+'</span>');
    var diags=result.diagnosis||[];
    var bad=diags.filter(function(d){return d.severity!=='clean';}).length;
    highlights.push('<span class="rkey">Issues found</span>: <span class="'+(bad>0?'rwarn':'rval')+'">'+bad+'</span>');
    el.innerHTML=highlights.join(' &middot; ')+'<br><br>'+el.textContent;
  }

  var lines=[];
  for(var k in result){
    var v=result[k];
    if(v===null||v===undefined)continue;
    if(k==='available'||k==='error'||k==='_file'||k==='_source'||k==='raw'||k==='stdout'||k==='stderr'||k==='diagnosis'||k==='samples'||k==='health_score'||k==='timestamp'||k==='platform'||k==='os'||k==='tools')continue;
    var display=v;
    if(typeof v==='object'){
      if(v instanceof Array){
        if(v.length>5)display='['+v.length+' items]';
        else display=JSON.stringify(v);
      }else if(v&&v.available!==undefined){
        display=v.available?'Available':'Unavailable';
      }else{
        display=JSON.stringify(v);
      }
    }
    if(k==='avg_ms'||k==='rtt_ms'||k==='p95_ms'||k==='p50_ms'||k==='p99_ms'||k==='min_ms'||k==='max_ms'||k==='stdev_ms'||k==='jitter_ms'){
      lines.push('<span class="rkey">'+(k.replace(/_/g,' '))+'</span>: <span class="rval">'+display+' ms</span>');
    }else if(k==='loss_pct'||k==='failure_pct'||k==='retransmit_pct'){
      var cls=parseFloat(v)>5?'rerr':(parseFloat(v)>0?'rwarn':'rval');
      lines.push('<span class="rkey">'+(k.replace(/_/g,' '))+'</span>: <span class="'+cls+'">'+display+'%</span>');
    }else if(k==='signal_dbm'){
      var cls=v<-80?'rerr':(v<-70?'rwarn':'rval');
      lines.push('<span class="rkey">Signal</span>: <span class="'+cls+'">'+display+' dBm</span>');
    }else if(k==='download_mbps'||k==='upload_mbps'||k==='mbps'||k==='avg_mbps'){
      lines.push('<span class="rkey">'+(k.replace(/_/g,' '))+'</span>: <span class="rval">'+display+' Mbps</span>');
    }else if(k==='ratio'){
      var cls=parseFloat(v)>3?'rerr':(parseFloat(v)>2?'rwarn':'rval');
      lines.push('<span class="rkey">Bufferbloat ratio</span>: <span class="'+cls+'">'+display+'x</span>');
    }else if(k==='gateway'||k==='default_interface'){
      lines.push('<span class="rkey">'+(k.replace(/_/g,' '))+'</span>: <span class="rval">'+display+'</span>');
    }else if(k==='rx'||k==='tx'){
      if(typeof v==='object'){
        var sub=result[k]||{};
        lines.push('<span class="rkey">'+k.toUpperCase()+'</span>');
        for(var sk in sub){
          lines.push('  <span class="rkey">'+sk+'</span>: <span class="rval">'+sub[sk]+'</span>');
        }
      }
    }else if(k==='addresses'&&v instanceof Array){
      lines.push('<span class="rkey">Resolved addresses</span>:');
      v.forEach(function(a){lines.push('  <span class="rval">'+(a.ip||a)+'</span>');});
    }else if(k==='hops'&&v instanceof Array){
      lines.push('<span class="rkey">Route hops</span>:');
      v.forEach(function(h){lines.push('  Hop '+h.hop+': <span class="rval">'+(h.host||'*')+'</span> loss: '+(h.loss_pct!=null?h.loss_pct+'%':'?')+' avg: '+(h.avg_ms!=null?h.avg_ms+'ms':'?'));});
    }else if(k==='received'||k==='sent'||k==='queries'||k==='attempts'||k==='failures'||k==='success'||k==='total'||k==='ok'||k==='classification'){
      lines.push('<span class="rkey">'+(k.replace(/_/g,' '))+'</span>: <span class="rval">'+display+'</span>');
    }else{
      lines.push('<span class="rkey">'+(k.replace(/_/g,' '))+'</span>: <span class="rval">'+display+'</span>');
    }
  }
  el.innerHTML=el.innerHTML+lines.join('\n');
}
