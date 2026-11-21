const $ = id => document.getElementById(id);
const source = new Audio(), accompaniment = new Audio(), editedPreview = new Audio();
source.preload = accompaniment.preload = 'metadata';
let projects = [], current = 0, selected = 0, token = '', busy = false;
let activeVoice = 'source', activeJob = null, pollTimer = null, modelReady = false, svcReady = false, yingReady = false, transcriptionReady = false, separationReady = false;
let batchQueue = [], batchProjectId = null;
const PARAM_DEFAULTS = {melody:{steps:32,cfg:3,seed:-1},score:{steps:32,cfg:3,seed:-1},ying:{steps:64,cfg:2.5,seed:42,t_shift:.5,silence:.5}};
const PARAM_INPUTS = {steps:'paramSteps',cfg:'paramCfg',seed:'paramSeed',t_shift:'paramShift',silence:'paramSilence'};
let parameterDrafts = {}, parameterMode = 'melody', savedParameterDefaults = {};
let midiSlotDrafts = new Map(), midiSlotSignature = '', midiRoll = null, midiLoadRevision = 0;
let voiceBeforeEditor = null;
let timelineSelected = 0, timelineDirty = false, timelineBoundaryDirty = false, timelineBoundaryDraft = null;
const timelineHasChanges = () => timelineDirty || timelineBoundaryDirty;
const lyricUnits = text => text.replaceAll('<SP>', ' ').match(/[\u4e00-\u9fff]|[A-Za-z]+(?:'[A-Za-z]+)*/g) || [];
function lyricUnitMap(original, replacement) {
  const before=lyricUnits(original), after=lyricUnits(replacement), n=before.length, m=after.length;
  if(!n||!m)return null;
  if(n===m)return before.map((_,i)=>i);
  // Match the backend's SequenceMatcher with autojunk=False, including its
  // earliest-source / earliest-target tie break for repeated lyric units.
  const targetPositions = new Map();
  after.forEach((word,index) => {
    const key=word.toLowerCase();
    if (!targetPositions.has(key)) targetPositions.set(key,[]);
    targetPositions.get(key).push(index);
  });
  const matches=[], ranges=[[0,n,0,m]];
  while (ranges.length) {
    const [a0,a1,b0,b1]=ranges.pop();
    let best=0, ai=a0, bj=b0, previous=new Map();
    for (let i=a0;i<a1;i++) {
      const row=new Map();
      for (const j of targetPositions.get(before[i].toLowerCase()) || []) {
        if (j<b0) continue;
        if (j>=b1) break;
        const length=(previous.get(j-1)||0)+1;
        row.set(j,length);
        if (length>best) {best=length; ai=i-length+1; bj=j-length+1;}
      }
      previous=row;
    }
    if (!best) continue;
    for (let k=0;k<best;k++) matches.push([ai+k,bj+k]);
    if (ai>a0 && bj>b0) ranges.push([a0,ai,b0,bj]);
    if (ai+best<a1 && bj+best<b1) ranges.push([ai+best,a1,bj+best,b1]);
  }
  matches.sort((x,y)=>x[0]-y[0]);
  const map=Array(n).fill(-1);let oi=0,oj=0;
  for(const [mi,mj] of [...matches,[n,m]]){
    const oc=mi-oi,nc=mj-oj;
    if(oc||nc){if(nc<1||oc<nc)return null;for(let k=0;k<oc;k++)map[oi+k]=oj+Math.min(nc-1,Math.floor(k*nc/oc));}
    if(mi<n)map[mi]=mj;
    oi=mi+1;oj=mj+1;
  }
  return map.some(x=>x<0)?null:map;
}
let importKind = 'source', toastTimer, clipEnd = null, waveRevision = 0, playbackFrame;
let svcTargetKind = 'source';
let svcDraftFile = null, svcDraftDuration = null, svcDraftUrl = null, svcDraftProjectId = null;
const peaks = new Map();
function cachePeaks(id,values) {
  peaks.delete(id);peaks.set(id,values);
  while(peaks.size>4)peaks.delete(peaks.keys().next().value);
  return values;
}
const project = () => projects[current];
const duration = () => project()?.duration || 30;
const changed = x => x.text !== x.original || (x.mode === 'score' && !!(x.midi_note_edits || x.midi_slots?.length));
const fmt = n => String(Math.floor(n / 60)).padStart(2, '0') + ':' + String(Math.floor(n % 60)).padStart(2, '0');
function toast(text) {
  const message = $('toast');
  const dialogs = Array.from(document.querySelectorAll('dialog[open]'));
  (dialogs.at(-1) || document.body).appendChild(message);
  message.textContent = text;
  message.classList.add('visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $('toast').classList.remove('visible'), 5500);
}
async function api(path, options = {}) {
  const request = () => fetch('/api/' + path, {...options, headers: {'X-Studio-Token': token, ...options.headers}});
  let response = await request();
  if (response.status === 403 && (await response.clone().json().catch(() => ({}))).error === '会话已失效，请刷新页面。') {
    const sessionResponse = await fetch('/api/session');
    if (!sessionResponse.ok) throw new Error('本机会话恢复失败，请稍后重试。');
    const session = await sessionResponse.json();
    if (!session.token) throw new Error('本机会话恢复失败，请稍后重试。');
    token = session.token;
    response = await request();
  }
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw Object.assign(new Error(data.error || '操作失败（' + response.status + '），请重试。'), {issues:data.issues});
  }
  return response.json();
}
async function mutate(action) {
  if (busy) { toast('正在保存或导入，请稍候。'); return; }
  busy = true; $('edit').inert = true; $('newProject').inert = true; $('timelineEditor').inert = true;
  $('saveState').textContent = '正在处理…';
  try { await action(); $('saveState').textContent = '已保存到本机'; }
  catch (error) { $('saveState').textContent = '未保存'; if ($('edit').open) $('validation').textContent = error.message; toast(error.message); }
  finally {
    busy = false; $('edit').inert = false; $('newProject').inert = false; $('timelineEditor').inert = false;
    if($('timelineEditor').open){refreshWordWave();refreshTimelineBoundary();}
    if($('edit').open)drawEditorWave();
  }
}
function replaceProject(p) { projects[current] = p; render(); }
function render() {
  const p = project();
  $('songName').textContent = document.querySelector('.project-name').textContent = p?.name || '开始一个新工程';
  $('projectCount').textContent = projects.length;
  $('projects').replaceChildren(); $('projectSelect').replaceChildren();
  $('projectPickerOptions').replaceChildren(); $('projectPickerLabel').textContent=p?.name || '选择工程';
  projects.forEach((item, i) => {
    const row = document.createElement('div'); row.className = 'project-row' + (i === current ? ' active' : '');
    const button = document.createElement('button');
    button.className = 'project' + (i === current ? ' active' : '');
    const cover = document.createElement('span'); cover.className = 'cover'; cover.textContent = '♫';
    const title = document.createElement('div'); title.className = 'project-copy';
    const name = document.createElement('span'); name.className = 'project-title'; name.textContent = item.name; name.title = item.name; title.append(name);
    const sub = document.createElement('small'); sub.textContent = item.source_issue ? '仅功能测试 · 无演唱，不可生成' : item.lines.length + ' 句 · ' + item.lines.filter(x => x.render_state === 'ready').length + ' 已生成';
    title.append(sub); button.append(cover, title); button.onclick = () => switchProject(i);
    const remove = document.createElement('button'); remove.className = 'project-delete'; remove.type = 'button';
    remove.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3m3 0-1 13H7L6 7m4 4v5m4-5v5"/></svg>'; remove.title = '删除工程'; remove.setAttribute('aria-label', '删除工程：' + item.name);
    remove.disabled = !!activeJob; remove.onclick = () => deleteProject(i);
    row.append(button, remove); $('projects').append(row);
    const option = document.createElement('option'); option.value = i; option.textContent = item.name; option.selected = i === current;
    $('projectSelect').append(option);
    const choice=document.createElement('button'); choice.type='button'; choice.className='project-picker-option'; choice.setAttribute('aria-current',String(i===current));
    const choiceName=document.createElement('span'); choiceName.textContent=item.name; const mark=document.createElement('span'); mark.className='project-picker-mark'; mark.textContent=i===current?'✓':''; choice.append(choiceName,mark);
    choice.onclick=()=>{ $('projectPicker').open=false; switchProject(i); }; $('projectPickerOptions').append(choice);
  });
  if (!projects.length) {
    const empty = document.createElement('p'); empty.className = 'projects-empty'; empty.textContent = '还没有工程';
    $('projects').append(empty);
  }
  $('sourceIssue').hidden = !p?.source_issue;
  $('sourceIssueText').textContent = p?.source_issue || '';
  $('useSingingExample').hidden = !projects.some(x => !x.source_issue && x.rendered_available);
  $('counts').replaceChildren();
  if (p) [p.lines.length + ' 句歌词', p.lines.filter(changed).length + ' 处改词', p.lines.filter(x => x.render_state === 'ready').length + ' 已生成'].forEach(text => {
    const span = document.createElement('span'); span.textContent = text; $('counts').append(span);
  });
  $('lyricsList').replaceChildren();
  if (p && !p.lines.length && p.assets.source) {
    const empty=document.createElement('div');empty.className='empty-project';
    empty.textContent='音频已准备好，请导入 SRT 字幕或 LRC 歌词。';$('lyricsList').append(empty);
  }
  for (const [i, line] of (p?.lines || []).entries()) {
    const row = document.createElement('article'); row.className = 'lyric-row' + (i === selected ? ' selected' : '');
    row.id = 'line-' + i;
    const copy = document.createElement('div'); copy.className = 'lyric-copy';
    const words = document.createElement('div'); words.className = 'words';
    const displayText=line.text.replaceAll('<SP>', ' ');
    let textCursor=0;
    const chars = lyricUnits(line.text);
    const timed = line.unit_timings?.length === chars.length;
    chars.forEach((char, j) => {
      const index=displayText.indexOf(char,textCursor);
      if(index>textCursor)words.append(document.createTextNode(displayText.slice(textCursor,index)));
      textCursor=index+char.length;
      const b = document.createElement('button'); b.className = 'word'; b.textContent = char;
      const timing = timed ? line.unit_timings[j] : null;
      b.title = timing ? '已对齐 · ' + timing.start.toFixed(3) + ' — ' + timing.end.toFixed(3) : '按单位数估算定位；可在歌词时间轴提取真实时间';
      b.dataset.start = timing ? timing.start : line.start + j / Math.max(chars.length, 1) * (line.end - line.start);
      b.dataset.end = timing ? timing.end : line.start + (j+1) / Math.max(chars.length, 1) * (line.end - line.start);
      b.onclick = () => {
        selected = i;
        seek(timing ? timing.start : line.start + j / Math.max(chars.length, 1) * (line.end - line.start));
        document.querySelectorAll('.lyric-row').forEach((r, k) => r.classList.toggle('selected', k === i));
      };
      words.append(b);
    });
    if(textCursor<displayText.length)words.append(document.createTextNode(displayText.slice(textCursor)));
    const original = document.createElement('div'); original.className = 'source';
    original.textContent = changed(line) ? '原词：' + line.original : '原词 · ' + fmt(line.start) + ' — ' + fmt(line.end);
    const state = document.createElement('div'); state.className = 'state'; state.title = line.render_warning || '';
    state.textContent = line.needs_review ? '自动识词 · 请校对原词和边界' : line.render_legacy ? '旧版可能唱原词 · 请重新生成' : line.render_state === 'ready' ? ('已生成 · ' + ({score:'MIDI',melody:'旋律',ying:'YingMusic'}[line.mode] || line.mode) + (line.render_warning ? ' · 建议试听校对' : '')) : line.render_state === 'stale' ? '修改后待重新生成' : changed(line) ? '待生成' : '';
    const edit = document.createElement('button'); edit.className = 'edit-button'; edit.textContent = '♧ 改词'; edit.onclick = () => openEdit(i);
    copy.append(words, original, state); row.append(copy, edit); $('lyricsList').append(row);
  }
  const pending = p?.lines.filter(x => changed(x) && x.render_state !== 'ready').length || 0;
  const unaligned = p?.lines.filter(x => x.alignment_state !== 'ready').length || 0;
  $('batchAlign').textContent = '提取未对齐歌词 ' + unaligned + ' 句';
  $('batchAlign').disabled = !p || !!activeJob || (modelReady && (!unaligned || !p.assets.source || !!p.source_issue));
  $('batchAlign').title = p?.source_issue || '批量提取未对齐的逐字时间；已有时间不会被覆盖';
  $('transcribe').disabled = !p || !!activeJob || (transcriptionReady && (!p.assets.source || !!p.source_issue));
  $('transcribe').title = !transcriptionReady ? '歌词识别组件未安装，可先导入 SRT 或 LRC。' : '';
  $('lyricHistory').disabled = !p || !!activeJob;
  $('batch').textContent = '生成待处理 ' + pending + ' 句'; $('batch').disabled = !p || !!activeJob || (modelReady && (!pending || !!p.source_issue));
  $('batch').title = p?.source_issue || '仅生成待处理的改词，已生成的唱句直接复用；可在单句编辑中手动重新生成。';
  $('regenerateAll').disabled = !p || !!activeJob || !p.lines.length || (modelReady && (!p.assets.source || !!p.source_issue));
  $('regenerateAll').title = p?.source_issue || '重新生成工程内所有唱句，包含已生成的句子。';
  $('versionRendered').disabled = !(p?.rendered_preview_available ?? p?.rendered_available);
  $('versionRendered').title = p?.rendered_preview_stale ? '试听上次生成的改词翻唱，最新修改待生成。' : '试听已生成的改词翻唱';
  $('versionSvc').disabled = !p;
  $('versionSvc').title = p?.svc_available ? '试听 SVC 音色转换结果' : '尚无转换结果，点击设置音色转换';
  $('exportSvc').disabled = !p?.svc_available;
  $('exportSvcMix').disabled = !p?.svc_mixed_available;
  $('startSvc').disabled = !p || !!activeJob || (svcReady && (!p.assets.source || !!p.source_issue));
  $('recompose').disabled = !p?.assets.source || !!activeJob || !p?.lines.some(x=>x.render_state==='ready');
  $('recompose').textContent = p?.assets.accompaniment ? '重新混音' : '重新合成';
  $('recompose').title = '复用已生成的唱句重新合成人声和伴奏，无需重新运行演唱模型';
  $('exportVoice').disabled = $('exportMix').disabled = !p?.rendered_available || !p?.render_complete;
  if (!p?.assets.accompaniment) $('exportMix').disabled = true;
  ['importSource', 'importAccompaniment', 'importLyrics', 'exportLyrics'].forEach(id => $(id).disabled = !p);
  $('importSong').disabled = !p || !!activeJob;
  $('importSong').title = !separationReady ? '人声分离组件未安装，可先导入原唱干声和伴奏。' : '';
  $('seek').max = duration(); updateClock(+$('seek').value,true);
}
function pause() {
  source.pause(); accompaniment.pause(); clipEnd = null; cancelAnimationFrame(playbackFrame); $('play').textContent = '▶';
  editedPreview.pause();
  $('previewEditedSegment').textContent = '▶ 试听改词';
  $('previewSegment').textContent = '▶ 试听原唱';
}
function seek(value) {
  const position = Math.max(0, Math.min(value, duration()));
  for (const audio of [source, accompaniment]) {
    if (audio.readyState >= 1) audio.currentTime = Math.min(position, audio.duration || position);
  }
  $('seek').value = position; updateClock(position,true);
}
async function play(end = null) {
  if (!project()?.assets.source) { toast('请先导入原唱或干声音频。'); return; }
  clipEnd = end;
  if (source.currentTime >= duration() - 0.05) seek(0);
  try {
    if (accompaniment.readyState >= 1 && source.currentTime < accompaniment.duration) {
      accompaniment.currentTime = source.currentTime;
      accompaniment.play().catch(() => {});
    }
    await source.play(); $('play').textContent = 'Ⅱ';
    cancelAnimationFrame(playbackFrame);
    function watchClip() {
      if (source.paused) return;
      if (clipEnd !== null && source.currentTime >= clipEnd) { pause(); return; }
      $('seek').value = source.currentTime; updateClock(source.currentTime);
      playbackFrame = requestAnimationFrame(watchClip);
    }
    playbackFrame = requestAnimationFrame(watchClip);
  } catch (_) { pause(); toast('无法播放音频，请检查格式或重新导入。'); }
}
source.addEventListener('timeupdate', () => {
  $('seek').value = source.currentTime; updateClock(source.currentTime);
  if (clipEnd !== null && source.currentTime >= clipEnd) pause();
  if (!source.paused && !accompaniment.paused && Math.abs(source.currentTime - accompaniment.currentTime) > 0.15) {
    accompaniment.currentTime = Math.min(source.currentTime, accompaniment.duration);
  }
});
source.addEventListener('ended', pause);
source.addEventListener('error', () => { pause(); toast('原唱音频无法解码，请换用 WAV 或 MP3。'); });
function lyricFill(time,start,end) {
  return end > start ? Math.max(0,Math.min(1,(time-start)/(end-start))) : (time>=end?1:0);
}
function paintLyrics(row,time) {
  row?.querySelectorAll('.word[data-start]').forEach(word=>{
    word.style.setProperty('--lyric-progress',(lyricFill(time,+word.dataset.start,+word.dataset.end)*100).toFixed(2)+'%');
  });
}
function updateClock(time=+$('seek').value,refreshAll=false) {
  $('clock').textContent = fmt(time) + ' / ' + fmt(duration());
  document.querySelectorAll('.track-wave').forEach(track=>{const total=duration();track.style.setProperty('--track-position',Math.max(0,Math.min(100,total?time/total*100:0))+'%');track.setAttribute('aria-valuemax',String(total));track.setAttribute('aria-valuenow',String(Math.max(0,Math.min(time,total)).toFixed(1)));track.setAttribute('aria-valuetext',fmt(time)+' / '+fmt(total));});
  const lines=project()?.lines || [];
  const index=lines.findIndex(line=>time>=line.start && time<line.end);
  const rows=$('lyricsList').children;
  if(refreshAll) Array.from(rows).forEach(row=>paintLyrics(row,time));
  if(index>=0) {
    if(index!==selected && !$('edit').open && !$('timelineEditor').open) {
      const previous=selected;selected=index;
      rows[previous]?.classList.remove('selected');rows[index]?.classList.add('selected');
      paintLyrics(rows[previous],time);
      if(!source.paused && rows[index]) {
        const list=$('lyricsList'),lineRect=rows[index].getBoundingClientRect(),listRect=list.getBoundingClientRect();
        list.scrollTo({top:list.scrollTop+lineRect.top+lineRect.height/2-listRect.top-listRect.height/2,behavior:'smooth'});
      }
    }
    paintLyrics(rows[index],time);
  } else paintLyrics(rows[selected],time);
  phraseWaveEditor?.setPlayhead(time); editWordEditor?.setPlayhead(time); wordWaveEditor?.setPlayhead(time); timelinePhraseWaveEditor?.setPlayhead(time);
}
const waveValues = new WeakMap();
function paintWave(element) {
  const values = waveValues.get(element);
  if (!values?.length || !element.clientWidth) return;
  const width = element.clientWidth, count = Math.min(values.length, Math.max(1, Math.floor(width / 2)));
  const envelope = [];
  for (let i = 0; i < count; i++) {
    let peak = 0;
    for (let j = Math.floor(i * values.length / count); j < Math.floor((i + 1) * values.length / count); j++) peak = Math.max(peak, values[j]);
    envelope.push(peak);
  }
  const positive = envelope.filter(v => v > 1e-8).sort((a,b) => a-b);
  const reference = positive[Math.min(positive.length-1, Math.floor(positive.length * .96))] || 1;
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', `0 0 ${width} 100`); svg.setAttribute('preserveAspectRatio', 'none'); svg.setAttribute('aria-hidden', 'true');
  const path = document.createElementNS(svg.namespaceURI, 'path');
  path.setAttribute('d', envelope.map((v,i) => {
    const half = Math.max(.5, Math.min(1, v / reference * .92) * 48), x = ((i+.5)*width/count).toFixed(2);
    return `M${x},${(50-half).toFixed(2)}V${(50+half).toFixed(2)}`;
  }).join(''));
  svg.append(path); element.replaceChildren(svg);
}
const waveObserver = new ResizeObserver(entries => entries.forEach(({target}) => paintWave(target)));
function drawWave(element, values, label = '尚未导入音频') {
  waveObserver.unobserve(element); waveValues.set(element, values);
  element.replaceChildren(); element.classList.toggle('empty', !values?.length);
  if (!values?.length) { element.textContent = label; return; }
  paintWave(element); waveObserver.observe(element);
}
async function decodePeaks(asset, revision) {
  if (!asset) return null;
  if (peaks.has(asset.id)) return cachePeaks(asset.id,peaks.get(asset.id));
  const serverPeaks=async()=>{
    if(asset.duration>1800)return null;
    const response=await fetch('/api/assets/'+asset.id+'/waveform');
    if(!response.ok)return null;
    const result=(await response.json()).peaks;
    if(!Array.isArray(result)||!result.length)return null;
    return cachePeaks(asset.id,result);
  };
  const response = await fetch('/api/assets/' + asset.id);
  if (!response.ok) return null;
  if (+(response.headers.get('content-length') || 0) > 80 * 1024 * 1024) {
    await response.body?.cancel();return serverPeaks();
  }
  const buffer = await response.arrayBuffer();
  if (revision !== waveRevision) return null;
  const context = new AudioContext();
  try {
    const decoded = await context.decodeAudioData(buffer);
    const channels = Array.from({length: decoded.numberOfChannels}, (_,i) => decoded.getChannelData(i));
    const bins = Math.min(360000, Math.max(1, Math.ceil(decoded.duration * 200)));
    const result = [];
    for (let i = 0; i < bins; i++) {
      let max = 0;
      for (let j = Math.floor(i * decoded.length / bins); j < Math.floor((i + 1) * decoded.length / bins); j++) {
        for (const channel of channels) max = Math.max(max, Math.abs(channel[j]));
      }
      result.push(max);
    }
    return cachePeaks(asset.id,result);
  } catch (_) { return serverPeaks(); }
  finally { await context.close(); }
}
let editWordEditor, editWordDraft, editWordDirty;
let phraseWaveEditor = null, wordWaveEditor = null, timelinePhraseWaveEditor = null;
function refreshEditWordWave() {
  const p=project(), line=p?.lines[selected];
  if (!line || !editWordDraft) return;
  const start=+$('start').value, end=+$('end').value;
  const moved=start!==line.start || end!==line.end;
  $('editExtractWords').disabled=moved || busy || !!activeJob;
  $('editWordHint').textContent=moved ? '保存新句边界后可重新提取逐字时间。' :
    editWordDraft.length ? '拖动字块或边缘调整时间，保存或生成时一起提交。修改逐字时间会清除旧的手动 MIDI 音符编排。' : '提取原唱逐字时间后即可拖动调整。';
  $('editWordWave').hidden=moved || !editWordDraft.length;
  if ($('editWordWave').hidden) return;
  editWordEditor ||= new WaveEditor($('editWordWave'),{draw:drawWave,onSeek:t=>{pause();seek(t);},onChange:regions=>{
    editWordDraft=regions.map(({text,start,end})=>({text,start,end}));
    editWordDirty=true;
  }});
  editWordEditor.setData({key:p.id+line.id+':'+start+':'+end, duration:duration(),domain:[start,end],
    values:peaks.get(p.assets.source?.id),regions:editWordDraft,disabled:busy||!!activeJob,coupleAdjacent:true});
}
$('editExtractWords').onclick=()=> {
  if (!modelReady) { openModelSetup('singer'); return; }
  if (editWordDirty) { toast('请先保存当前逐字时间。'); return; }
  mutate(async()=>{
    const p=project();
    activeJob=await api('projects/'+p.id+'/alignments',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({revision:p.revision,segment_ids:[p.lines[selected].id]})});
    render();pollJob();
  });
};
function drawEditorWave() {
  refreshEditWordWave();
  const values = peaks.get(project()?.assets.source?.id);
  const start = +$('start').value, end = +$('end').value;
  const slice = values?.slice(Math.floor(start / duration() * values.length), Math.ceil(end / duration() * values.length));
  drawWave(document.querySelector('[data-wave="3"]'), slice, '导入音频后显示当前句波形');
  drawWave(document.querySelector('[data-wave="4"]'), values, '整首概览');
  if (phraseWaveEditor) {
    const p=project(), line=p.lines[selected];
    const previous=p.lines[selected-1], following=p.lines[selected+1];
    phraseWaveEditor.setData({key:p.id+line.id+':'+line.start+':'+line.end, values, duration:duration(),
      domain:[Math.max(previous ? previous.start+.12 : 0,line.start-1),Math.min(following ? following.end-.12 : duration(),line.end+1)],
      regions:[{text:'当前句',start,end}], disabled:busy||!!activeJob, minDuration:.12});
  }
}
function ensurePhraseWave() {
  if(phraseWaveEditor)return;
  const old=document.querySelector('[data-wave="3"]'), host=document.createElement('div');
  old.hidden=true;old.before(host);
  phraseWaveEditor=new WaveEditor(host,{draw:drawWave,onSeek:t=>{pause();seek(t);},onChange:regions=>{
    const r=regions[0];$('start').value=$('startRange').value=r.start.toFixed(3);$('end').value=$('endRange').value=r.end.toFixed(3);
    validate();
  }});
}
function refreshWordWave() {
  const p=project(), line=p?.lines[timelineSelected];if(!line||!wordWaveEditor)return;
  const regions=Array.from($('wordTimeline').children).map(row=>({text:row.dataset.word,start:parseFloat(row.querySelector('.word-start').value),end:parseFloat(row.querySelector('.word-end').value)}));
  if(regions.some(r=>!Number.isFinite(r.start)||!Number.isFinite(r.end)))return;
  wordWaveEditor.setData({key:p.id+line.id+':'+line.start+':'+line.end,values:peaks.get(p.assets.source?.id),duration:duration(),domain:[line.start,line.end],regions,disabled:busy||!!activeJob,coupleAdjacent:true});
}
function ensureWordWave() {
  if(wordWaveEditor)return;
  const host=$('timelineWordWave');
  wordWaveEditor=new WaveEditor(host,{draw:drawWave,onSeek:t=>{pause();seek(t);},onChange:regions=>{
    regions.forEach((r,i)=>{const row=$('wordTimeline').children[i];row.querySelector('.word-start').value=r.start.toFixed(3);row.querySelector('.word-end').value=r.end.toFixed(3);});
    timelineDirty=true;
  }});
  host.querySelector('small').textContent += ' · 推动边界会联动相邻字';
}

function refreshTimelineBoundary() {
  const p=project(), line=p?.lines[timelineSelected]; if(!line || !timelinePhraseWaveEditor || !timelineBoundaryDraft)return;
  const previous=p.lines[timelineSelected-1], next=p.lines[timelineSelected+1], draft=timelineBoundaryDraft;
  timelinePhraseWaveEditor.setData({key:p.id+line.id+':'+line.start+':'+line.end,
    values:peaks.get(p.assets.source?.id),duration:duration(),
    domain:[Math.max(previous ? previous.start+.12 : 0,line.start-1),Math.min(next ? next.end-.12 : duration(),line.end+1)],
    regions:[{text:'当前句',start:draft.start,end:draft.end}],disabled:busy||!!activeJob,minDuration:.12});
  $('timelineBoundaryTimes').textContent=draft.start.toFixed(3)+' — '+draft.end.toFixed(3)+' s';
  $('timelineSaveBoundary').disabled=!timelineBoundaryDirty||busy||!!activeJob;
  $('timelineResetBoundary').disabled=!timelineBoundaryDirty||busy||!!activeJob;
  $('saveOriginal').disabled=timelineBoundaryDirty||!!activeJob;
  $('saveWordTimes').disabled=timelineBoundaryDirty||!(line.unit_timings||[]).length||!!activeJob;
  $('extractWords').disabled=timelineBoundaryDirty||!!activeJob||(modelReady&&!!p.source_issue);
  $('extractWordsEmpty').disabled=$('extractWords').disabled;
  $('openPhraseEditor').disabled=timelineBoundaryDirty||!!activeJob;
  $('splitPhrase').disabled=timelineBoundaryDirty||!!activeJob||lyricUnits(line.original).length<2;
  $('mergePhrase').disabled=timelineBoundaryDirty||!!activeJob||timelineSelected>=p.lines.length-1;
  $('takeHistory').disabled=timelineBoundaryDirty||!!activeJob;
  drawWave($('timelineOverview'),peaks.get(p.assets.source?.id),'整首波形');
}
function ensureTimelinePhraseWave() {
  if(timelinePhraseWaveEditor)return;
  timelinePhraseWaveEditor=new WaveEditor($('timelinePhraseWave'),{draw:drawWave,onSeek:t=>{pause();seek(t);},onChange:regions=>{
    const line=project()?.lines[timelineSelected];if(!line)return;
    timelineBoundaryDraft={start:regions[0].start,end:regions[0].end};
    timelineBoundaryDirty=Math.abs(timelineBoundaryDraft.start-line.start)>.0005||Math.abs(timelineBoundaryDraft.end-line.end)>.0005;
    $('timelineBoundaryTimes').textContent=timelineBoundaryDraft.start.toFixed(3)+' — '+timelineBoundaryDraft.end.toFixed(3)+' s';
    $('timelineSaveBoundary').disabled=!timelineBoundaryDirty||busy||!!activeJob;
    $('timelineResetBoundary').disabled=!timelineBoundaryDirty||busy||!!activeJob;
    $('saveOriginal').disabled=timelineBoundaryDirty||!!activeJob;
    $('saveWordTimes').disabled=timelineBoundaryDirty||!(line.unit_timings||[]).length||!!activeJob;
    const p=project();
    $('extractWords').disabled=timelineBoundaryDirty||!!activeJob||(modelReady&&!!p.source_issue);
  $('extractWordsEmpty').disabled=$('extractWords').disabled;
    $('openPhraseEditor').disabled=timelineBoundaryDirty||!!activeJob;
    $('splitPhrase').disabled=timelineBoundaryDirty||!!activeJob||lyricUnits(line.original).length<2;
    $('mergePhrase').disabled=timelineBoundaryDirty||!!activeJob||timelineSelected>=p.lines.length-1;
    $('takeHistory').disabled=timelineBoundaryDirty||!!activeJob;
  }});
}
async function loadAudio(position = 0, resume = false) {
  pause(); const p = project(), revision = ++waveRevision;
  if (activeVoice === 'rendered' && !(p?.rendered_preview_available ?? p?.rendered_available)) activeVoice = 'source';
  if (activeVoice === 'svc' && !p?.svc_available) activeVoice = 'source';
  document.querySelectorAll('[data-version]').forEach(b => b.classList.toggle('chosen', b.dataset.version === activeVoice));
  for (const [kind, audio, selector] of [['source', source, '1'], ['accompaniment', accompaniment, '2']]) {
    const asset = kind === 'source' && activeVoice === 'rendered' ? {id: p.composite.rendered, name: '改词演唱', duration: p.duration}
      : kind === 'source' && activeVoice === 'svc' ? {id:p.svc.asset_id,name:'音色转换',duration:p.duration} : p?.assets[kind];
    if (asset) {
      audio.onloadedmetadata = () => {
        if (revision !== waveRevision) return;
        audio.currentTime = Math.min(position, audio.duration);
        if (kind === 'source' && resume) play();
      };
      audio.src = '/api/assets/' + asset.id;
    }
    else { audio.removeAttribute('src'); audio.load(); }
    const el = document.querySelector('[data-wave="' + selector + '"]');
    drawWave(el, null, asset ? '正在读取波形…' : '未导入音频');
    if (asset) decodePeaks(asset, revision).then(values => {
      if (revision === waveRevision) {
        drawWave(el, values, '波形暂不可用，仍可尝试试听');
        if ($('edit').open) drawEditorWave();
        if ($('timelineEditor').open) { refreshWordWave(); refreshTimelineBoundary(); }
      }
    }).catch(() => { if (revision === waveRevision) drawWave(el, null, '波形暂不可用'); });
  }
  $('voiceLabel').textContent = activeVoice === 'rendered' ? (p?.rendered_preview_stale ? '上次生成的改词演唱 · 有修改待生成' : '改词演唱（未生成部分保留原唱）') : activeVoice === 'svc' ? '音色转换' : p?.assets.source?.name || '未导入原唱';
  document.querySelector('.track:nth-child(2) small').textContent = p?.assets.accompaniment?.name || '未导入伴奏';
  $('seek').value = position; updateClock(position,true);
}
function switchProject(i) {
  if (busy) { toast('当前操作尚未完成。'); return; }
  current = i; selected = 0; activeVoice = (project()?.rendered_preview_available ?? project()?.rendered_available) ? 'rendered' : 'source'; render(); loadAudio();
}
function askConfirm(title, message, confirmLabel = '确认') {
  const dialog = $('confirmAction');
  if (dialog.open) return Promise.resolve(false);
  $('confirmTitle').textContent = title;
  $('confirmMessage').textContent = message;
  $('confirmAccept').textContent = confirmLabel;
  return new Promise(resolve => {
    const finish = value => {
      dialog.removeEventListener('cancel', onCancel);
      $('confirmAccept').onclick = $('confirmCancel').onclick = null;
      dialog.close(); resolve(value);
    };
    const onCancel = event => { event.preventDefault(); finish(false); };
    $('confirmAccept').onclick = () => finish(true);
    $('confirmCancel').onclick = () => finish(false);
    dialog.addEventListener('cancel', onCancel);
    dialog.showModal();
  });
}
async function deleteProject(i) {
  const item = projects[i];
  if (!item) return;
  if (busy || activeJob) { toast('请等待当前操作完成后再删除工程。'); return; }
  if (!await askConfirm('删除工程', '确定删除“' + item.name + '”吗？工程记录和未被其他工程使用的音频文件都会删除，无法撤销。', '删除工程')) return;
  mutate(async () => {
    pause();
    await api('projects/' + item.id, {method:'DELETE'});
    projects.splice(i, 1);
    if (i < current) current--;
    else if (i === current) current = projects.length ? Math.min(i, projects.length - 1) : 0;
    selected = 0; activeVoice = 'source'; voiceBeforeEditor = null;
    render(); loadAudio();
  });
}
function enterOriginalEditor() {
  if (!voiceBeforeEditor) voiceBeforeEditor = {projectId: project()?.id, voice: activeVoice};
  if (activeVoice !== 'source') { const position = source.currentTime || +$('seek').value; activeVoice = 'source'; loadAudio(position); }
}
function leaveOriginalEditor() {
  const previous = voiceBeforeEditor; voiceBeforeEditor = null;
  if (previous?.projectId === project()?.id) {
    activeVoice = previous.voice;
    loadAudio(source.currentTime || +$('seek').value);
  }
}
function parameterValues(mode=parameterMode) {
  return Object.fromEntries(Object.keys(PARAM_DEFAULTS[mode]).map(key => [key,$(PARAM_INPUTS[key]).value]));
}
function showParameterMode(mode) {
  parameterMode=mode;
  const values={...PARAM_DEFAULTS[mode],...(savedParameterDefaults[mode] || {}),...(parameterDrafts[mode] || {})};
  for(const key of Object.keys(values)) $(PARAM_INPUTS[key]).value=values[key];
  document.querySelectorAll('.ying-param').forEach(element=>{element.hidden=mode!=='ying';});
}
function validParameters() {
  return Object.keys(PARAM_DEFAULTS[parameterMode]).every(key=>{
    const input=$(PARAM_INPUTS[key]); return input.value!=='' && Number.isFinite(input.valueAsNumber) && input.checkValidity();
  });
}
$('resetParams').onclick=()=>{parameterDrafts[parameterMode]={...PARAM_DEFAULTS[parameterMode]};showParameterMode(parameterMode);validate();};
$('setDefaultParams').onclick = () => {
  if (!validParameters()) { toast('请先检查参数范围。'); return; }
  mutate(async () => {
    savedParameterDefaults = await api('generation-defaults', {method:'PUT',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({mode:parameterMode,parameters:Object.fromEntries(Object.entries(parameterValues()).map(([k,v])=>[k,Number(v)]))})});
    $('parameterStatus').textContent = '已保存当前模式的全局默认参数，后续未设置参数的句子将使用这组值。';
  });
};
$('applyPendingParams').onclick = () => {
  if (!validParameters()) { toast('请先检查参数范围。'); return; }
  mutate(async () => {
    const p=project();
    const result=await api('projects/'+p.id+'/generation-parameters',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({revision:p.revision,mode:parameterMode,scope:'pending',
        parameters:Object.fromEntries(Object.entries(parameterValues()).map(([k,v])=>[k,Number(v)]))})});
    replaceProject(result.project);
    $('parameterStatus').textContent='已应用到当前模式的 '+result.count+' 句待生成歌词。';
  });
};
Object.values(PARAM_INPUTS).forEach(id=>$(id).oninput=validate);
function openEdit(i) {
  if (busy) return;
  const line = project()?.lines[i]; if (!line) return;
  selected = i; render(); pause();
  enterOriginalEditor();
  selected = i; // Loading the original track must not change the sentence being edited.
  $('parameterStatus').textContent='全局默认用于尚未单独设置参数的句子，各生成模式分别保存。';
  $('editMeta').textContent = '第 ' + (i + 1) + ' 句 · ' + fmt(line.start) + ' — ' + fmt(line.end);
  $('sourceText').textContent = $('original').textContent = line.original;
  $('newLyrics').value = line.text;
  midiSlotDrafts = new Map((line.midi_slots || []).map(x => [x.index + ':' + x.word.toLowerCase(), {slots:x.slots.join(' | '),gap_policy:x.gap_policy}]));
  midiSlotSignature = ''; midiRoll = null;
  $('midiRoll').hidden = true;
  $('analyzeMidi').hidden = true;
  $('midiNotesStatus').textContent = '正在读取音符…';
  for (const id of ['start', 'end', 'startRange', 'endRange']) { $(id).max = duration(); $(id).value = id.startsWith('start') ? line.start : line.end; }
  $('restore').checked = !!line.restore;
  $('regenerate').disabled = !!activeJob || (modelReady && !!project()?.source_issue);
  $('regenerate').title = project()?.source_issue || '';
  document.querySelectorAll('[data-mode]').forEach(b => b.classList.toggle('chosen', b.dataset.mode === line.mode));
  parameterDrafts = {[line.mode]:{...(line.generation_params || {})}};
  showParameterMode(line.mode);
  syncRestore();
  editWordDraft=(line.unit_timings || []).map(word=>({...word})); editWordDirty=false;
  ensurePhraseWave(); validate(); $('edit').showModal(); drawEditorWave();
  loadMidiNotes(project(), line);
  const initialParameterValues=JSON.stringify(parameterValues());
  api('generation-defaults').then(settings=>{
    savedParameterDefaults=settings;
    if ($('edit').open && project()?.lines[selected]?.id===line.id && !line.generation_params &&
        Object.keys(parameterDrafts[line.mode] || {}).length===0 && JSON.stringify(parameterValues())===initialParameterValues) showParameterMode(parameterMode);
  }).catch(error=>toast('读取默认参数失败：'+error.message));
}

function renderMidiSlotRows(mode, chars, originalUnits, unitMap) {
  const editor = $('midiSlotEditor'); editor.hidden = mode !== 'score';
  const signature = mode + ':' + JSON.stringify([chars, originalUnits, unitMap]);
  if (signature === midiSlotSignature) return;
  midiSlotSignature = signature; const host = $('midiSlotRows'); host.replaceChildren();
  chars.forEach((word, index) => {
    if (!/^[A-Za-z]+(?:'[A-Za-z]+)*$/.test(word)) return;
    const key = index + ':' + word.toLowerCase(), draft = midiSlotDrafts.get(key) || {slots:'',gap_policy:'auto'};
    midiSlotDrafts.set(key, draft);
    const row = document.createElement('div'); row.className = 'midi-slot-row';
    const label = document.createElement('label'); label.textContent = word + ' · 英文音素';
    const input = document.createElement('input'); input.type = 'text'; input.value = draft.slots; input.placeholder = '自动分配；例如 R-EH1 | S | T';
    input.setAttribute('aria-label', word + ' 的音素槽位');
    input.oninput = () => { draft.slots = input.value; validate(); };
    const select = document.createElement('select'); select.setAttribute('aria-label', word + ' 后面的空档');
    for (const [value, labelText] of [['auto','自动选择空档'],['keep','保留后字原位置'],['move','借用后续空档']]) {
      const option = document.createElement('option'); option.value = value; option.textContent = labelText; select.append(option);
    }
    select.value = draft.gap_policy; select.onchange = () => { draft.gap_policy = select.value; validate(); };
    label.append(input); row.append(label, select); host.append(row);
  });
  if (!host.children.length) { const hint = document.createElement('p'); hint.className = 'muted tiny'; hint.textContent = '当前句没有英文音素需要分配。'; host.append(hint); }
}

function midiSlotEntries() {
  return lyricUnits($('newLyrics').value).flatMap((word, index) => {
    const draft = midiSlotDrafts.get(index + ':' + word.toLowerCase());
    if (!draft) return [];
    const slots = draft.slots.trim() ? draft.slots.split('|').map(x => x.trim().toUpperCase()) : [];
    return slots.length || draft.gap_policy !== 'auto' ? [{index,word,slots,gap_policy:draft.gap_policy}] : [];
  });
}

async function loadMidiNotes(p, line) {
  const revision = ++midiLoadRevision;
  const currentLine = () => midiLoadRevision === revision && project()?.id === p.id && project()?.lines[selected]?.id === line.id && $('edit').open;
  try {
    const result = await api('projects/' + p.id + '/segments/' + line.id + '/midi-notes');
    if (!currentLine()) return;
    $('analyzeMidi').hidden = result.available;
    if (!result.available) {
      $('midiNotesStatus').textContent = '分析原唱后可编辑每个音符。';
      validate();
      return;
    }
    const saved = line.midi_note_edits;
    const compatible = saved && saved.source_id === p.assets?.source?.id && saved.original === line.original &&
      saved.start === line.start && saved.end === line.end && saved.events.length === result.notes.length &&
      saved.events.every((e,i) => e.text === result.notes[i].text && e.type === result.notes[i].type &&
        Math.abs(e.source_duration - (result.notes[i].end-result.notes[i].start)) < .03);
    midiRoll = new MidiRoll($('midiRoll'), validate);
    midiRoll.load(result.notes, line, compatible ? saved : null);
    if (saved?.lyrics && JSON.stringify(lyricUnits(saved.lyrics)) !== JSON.stringify(lyricUnits($('newLyrics').value)))
      midiRoll.assignment = null;
    $('midiNotesStatus').textContent = saved && !compatible ? '原唱分析已变化，请按当前音符重新编排。' :
      result.notes.length + ' 个槽位 · ' + line.start.toFixed(3) + ' — ' + line.end.toFixed(3) + ' s';
    validate();
  } catch (error) {
    if (currentLine()) {
      $('midiNotesStatus').textContent = '读取音符失败：' + error.message;
      $('analyzeMidi').hidden = false;
    }
  }
}
$('analyzeMidi').onclick = async () => {
  if (!modelReady) { openModelSetup('singer'); return; }
  const p = project(), line = p.lines[selected];
  if (activeJob || !line) return;
  if (+$('start').value !== line.start || +$('end').value !== line.end) { toast('请先保存整句边界，再分析音符。'); return; }
  $('analyzeMidi').disabled = true;
  try {
    activeJob = await api('projects/'+p.id+'/note-analyses', {method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({revision:p.revision,segment_ids:[line.id]})});
    render(); pollJob();
  } catch (error) { $('midiNotesStatus').textContent = error.message; }
  finally { validate(); }
};
function validate() {
  const p = project(), a = +$('start').value, b = +$('end').value;
  const chars = lyricUnits($('newLyrics').value);
  const originalUnits = lyricUnits(p.lines[selected].original), n = originalUnits.length;
  const unitMap = lyricUnitMap(p.lines[selected].original, $('newLyrics').value);
  const mode = document.querySelector('[data-mode].chosen')?.dataset.mode;
  renderMidiSlotRows(mode, chars, originalUnits, unitMap);
  const controls = midiSlotEntries();
  if (mode === 'score' && midiRoll) midiRoll.update(chars, unitMap, controls);
  $('analyzeMidi').disabled = !!activeJob || !p.assets?.source || (modelReady && !!p.source_issue);
  for (const select of $('midiSlotRows').querySelectorAll('select')) {
    select.hidden = select.disabled = !!midiRoll?.assignment;
    select.title = select.disabled ? '空拍已在卷帘中手动分配' : '';
  }
  const mappingOk = (mode === 'score' && !!midiRoll?.assignment) || mode === 'ying' || (unitMap !== null && chars.every((word,j) => unitMap.filter(x=>x===j).length < 2 || /^[A-Za-z]+(?:'[A-Za-z]+)*$/.test(word)));
  let midiError = '';
  if (mode === 'score') {
    if (controls.some(entry => entry.slots.some(slot => !/^[A-Z]+[012]?(?:-[A-Z]+[012]?)*$/.test(slot)))) midiError = '音素槽位请用 | 分隔，槽位内用 - 连接音素。';
    if (midiRoll?.edited && (a !== p.lines[selected].start || b !== p.lines[selected].end))
      midiError = '波形边界已改变。点击「生成当前句」可确认清除旧 MIDI 编排并按新边界生成。';
    else if (midiRoll) midiError ||= midiRoll.error();

  }
  const previous = p.lines[selected - 1], next = p.lines[selected + 1];
  const ok = $('start').value !== '' && $('end').value !== '' && Number.isFinite(a) && Number.isFinite(b)
    && a >= 0 && b-a >= .12-1e-9 && b <= duration() && (!previous || a >= previous.start+.12-1e-9)
    && (!next || b <= next.end-.12+1e-9) && chars.length > 0 && mappingOk && validParameters() && !midiError;
  $('save').disabled = !ok || busy;
  $('slotCount').textContent = '发音单位参考：原唱 ' + n + ' · 新词 ' + chars.length + '；实际对齐以分析结果为准';
  $('slots').replaceChildren();
  chars.forEach((char, i) => { const slot = document.createElement('div'), label = document.createElement('small'); label.textContent = '单位 ' + (i + 1); slot.append(label, document.createTextNode(char)); $('slots').append(slot); });
  if(mode==='score' && midiRoll?.assignment) $('slotHint').textContent='已使用钢琴卷帘中的歌词槽位。';
  else if(mode==='ying') $('slotHint').textContent='YingMusic 可尝试不同字数；改动较大时请试听节奏和发音。';
  else if(unitMap && chars.length!==n){
    const groups=chars.map((word,j)=>originalUnits.filter((_,i)=>unitMap[i]===j));
    const examples=groups.map((group,j)=>group.length>1?group.join('+')+' → '+chars[j]:'').filter(Boolean);
    $('slotHint').textContent=examples.length?'多音符映射：'+examples.join('；')+'。同一新词会覆盖对应原唱音符，试听确认发音与旋律。':'新旧单位数量不同；映射仍按顺序逐槽对应，请试听确认。';
  } else $('slotHint').textContent = unitMap ? '单位映射可用；实际旋律槽位与发音适配仍需试听校对。' : '当前仅支持多个原唱字映射到一个新词；请勿增加独立发音单位。';
  const affected = [previous && a < previous.end ? '上一句终点' : '', next && b > next.start ? '下一句起点' : ''].filter(Boolean);
  $('validation').textContent = ok ? (affected.length ? '保存时将同步调整'+affected.join('和')+'；受影响句子需要重新对齐和生成。' : (p.source_issue ? '当前为无演唱测试音，只能编辑与试听，不能生成。' : '保存后写入本机工程。')) : midiError || (!validParameters() ? '生成参数超出范围或为空，请检查参数设置。' : !mappingOk ? '新词无法自动对齐。可在 MIDI 钢琴卷帘中为每个字词指定音符，或调整歌词。' : '歌词不能为空；当前句和相邻句均须至少保留 120 毫秒，边界不能超出音频。');
  drawEditorWave(); return ok;
}
$('newLyrics').oninput = validate;
for (const id of ['start', 'end']) {
  $(id).oninput = () => { $(id + 'Range').value = $(id).value; validate(); };
  $(id + 'Range').oninput = () => { $(id).value = $(id + 'Range').value; validate(); };
}
$('save').onclick = () => {
  if (!validate()) return;
  mutate(async () => {
    const saved = await savePhrase();
    replaceProject(saved); leaveOriginalEditor(); $('edit').close(); toast('歌词与边界已保存到本机。');
  });
};
function savePhrase() {
  const p=project(), line=p.lines[selected];
  const mode=document.querySelector('[data-mode].chosen').dataset.mode;
  const value={revision:p.revision,text:$('newLyrics').value,start:+$('start').value,end:+$('end').value,
      mode,restore:$('restore').checked,
      generation_params:Object.fromEntries(Object.entries(parameterValues()).map(([key,value])=>[key,Number(value)]))};
  if (mode === 'score') {
    value.midi_slots = midiSlotEntries();
    if (midiRoll) value.midi_note_edits = midiRoll.value(p.assets.source.id, value.text);
  }
  if (editWordDirty && value.start===line.start && value.end===line.end) {
    value.unit_timings=editWordDraft;
    value.midi_note_edits=null;
  }
  return api('projects/'+p.id+'/segments/'+line.id+'/phrase', {method:'PUT',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(value)});
}
$('closeEdit').onclick = () => { if (!busy) { pause(); leaveOriginalEditor(); $('edit').close(); } };
$('edit').addEventListener('cancel', event => { if (busy) event.preventDefault(); else { pause(); leaveOriginalEditor(); } });
$('edit').addEventListener('close', pause);
function syncRestore() {
  const score=document.querySelector('[data-mode].chosen')?.dataset.mode==='score';
  $('restore').disabled=!score||!svcReady||!!activeJob;
  $('restore').title=!score?'仅 MIDI 模式可使用':!svcReady?'本地 SVC 组件未就绪':'';
}
document.querySelectorAll('[data-mode]').forEach(b => b.onclick = () => {
  parameterDrafts[parameterMode]=parameterValues();
  document.querySelectorAll('[data-mode]').forEach(x => x.classList.toggle('chosen', x === b));
  showParameterMode(b.dataset.mode);
  if(b.dataset.mode!=='score')$('restore').checked=false;
  syncRestore();validate();
});
$('regenerate').onclick = async () => {
  if (busy || activeJob) return;
  if (!modelReady) { openModelSetup('singer'); return; }
  const currentLine = project().lines[selected];
  const start = +$('start').value, end = +$('end').value;
  const score = document.querySelector('[data-mode].chosen')?.dataset.mode === 'score';
  if (score && midiRoll?.edited && (start !== currentLine.start || end !== currentLine.end)) {
    if (!await askConfirm('按新边界生成',
      '旧的手动 MIDI 编排和音素槽位将清除，软件会按新波形边界重新分析。', '按新边界生成')) return;
    if (busy || activeJob || !$('edit').open || project()?.lines[selected] !== currentLine ||
        +$('start').value !== start || +$('end').value !== end) return;
    midiRoll = null;
    $('midiRoll').hidden = true;
    $('midiNotesStatus').textContent = '生成时将按新边界重新分析 MIDI。';
    midiSlotDrafts.clear();
    midiSlotSignature = '';
  }
  if (!validate()) {
    toast($('validation').textContent);
    return;
  }
  mutate(async () => {
    const line = project().lines[selected];
    const saved = await savePhrase();
    replaceProject(saved); leaveOriginalEditor(); $('edit').close(); pause();
    await startRender([line.id]);
  });
};
async function startBatch(lines, p) {
  const groups=[];
  for (const group of [lines.filter(x=>x.mode!=='ying'),lines.filter(x=>x.mode==='ying')]) {
    for (let i=0;i<group.length;i+=100) groups.push(group.slice(i,i+100).map(x=>x.id));
  }
  if (!groups.length) return;
  batchProjectId=p.id;
  batchQueue=groups.slice(1);
  try { await startRender(groups[0],p); }
  catch(error) { batchQueue=[];batchProjectId=null;throw error; }
}
$('batch').onclick = () => {
  if (!modelReady) { openModelSetup('singer'); return; }
  return mutate(async () => {
    const p=project();
    await startBatch(p.lines.filter(x=>changed(x)&&x.render_state!=='ready'),p);
  });
};
$('regenerateAll').onclick = async () => {
  if (!modelReady) { openModelSetup('singer'); return; }
  const p=project();
  if (busy || activeJob || !p?.lines.length) return;
  if (!await askConfirm('重新生成全部','将重新生成“'+p.name+'”内全部 '+p.lines.length+' 句，包含已生成句子。每句沿用已保存的生成参数。是否开始？','开始重新生成')) return;
  if (project()?.id!==p.id || project()?.revision!==p.revision || activeJob || busy) {
    toast('工程或任务状态已改变，请重新点击。'); return;
  }
  return mutate(()=>startBatch(p.lines,p));
};
$('recompose').onclick=()=>mutate(()=>startCompose());
async function startCompose() {
  const p=project();
  activeJob=await api('projects/'+p.id+'/compose',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:p.revision})});
  render();pollJob();
}
$('batchAlign').onclick = async () => {
  if (!modelReady) { openModelSetup('singer'); return; }
  const p=project(), lines=p.lines.filter(x=>x.alignment_state!=='ready').slice(0,100);
  if(!lines.length)return;
  const invalid=lines.find(x=>x.end-x.start<.12-1e-9 || x.end-x.start>20);
  if(invalid){toast('第 '+(p.lines.indexOf(invalid)+1)+' 句须为 0.12–20 秒，请先调整边界后再提取。');return;}
  if(lines.some(x=>x.render_state==='ready')&&!await askConfirm('提取逐字时间','提取逐字时间会使涉及句子的已生成结果过期，需要重新生成。是否继续？','继续提取'))return;
  mutate(async()=>{
    activeJob=await api('projects/'+p.id+'/alignments',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({revision:p.revision,segment_ids:lines.map(x=>x.id)})});
    render();pollJob();
    if(p.lines.filter(x=>x.alignment_state!=='ready').length>100)toast('本次提取前 100 句；完成后可再次点击处理剩余句子。');
  });
};
$('transcribe').onclick = async () => {
  if (!transcriptionReady) { openModelSetup('transcription'); return; }
  const p=project();if(!p?.assets.source)return;
  if(p.lines.length&&!await askConfirm('自动识词','自动识词将替换当前歌词；原有内容会保存在历史歌词中。是否继续？','继续识词'))return;
  mutate(async()=>{
    activeJob=await api('projects/'+p.id+'/transcription',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({revision:p.revision,replace:!!p.lines.length})});
    render();pollJob();
  });
};
$('lyricHistory').onclick=async()=>{
  const p=project();if(!p)return;
  try{
    const result=await api('projects/'+p.id+'/lyric-history');
    if(project()?.id!==p.id)return;
    const list=$('lyricHistoryList');list.replaceChildren();
    if(!result.history.length){const empty=document.createElement('p');empty.className='muted';empty.textContent='还没有可恢复的历史歌词。';list.append(empty);}
    result.history.forEach(item=>{
      const row=document.createElement('div');row.className='lyric-history-row';
      const detail=document.createElement('div'),title=document.createElement('strong'),preview=document.createElement('p');
      title.textContent=item.reason+' · '+new Date(item.created_at*1000).toLocaleString('zh-CN');
      preview.className='muted tiny';preview.textContent=item.count+' 句 · '+item.preview+(item.compatible?'':' · 原唱已更换，不能直接恢复');
      detail.append(title,preview);
      const restore=document.createElement('button');restore.textContent='恢复此版本';restore.disabled=!item.compatible||!!activeJob;
      restore.onclick=async()=>{
        if(!await askConfirm('恢复历史歌词','恢复此版本将替换当前歌词；当前内容也会保存到历史中。是否继续？','恢复歌词'))return;
        mutate(async()=>{
          const current=project();
          const updated=await api('projects/'+current.id+'/lyric-history/'+item.id+'/restore',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:current.revision})});
          selected=0;replaceProject(updated);$('lyricHistoryDialog').close();loadAudio();
          if(!updated.rendered_available && updated.lines.some(x=>x.render_state==='ready')){await startCompose();toast('歌词已恢复，正在重建可用的演唱。');}
          else toast('历史歌词已恢复，恢复前的内容仍保存在历史中。');
        });
      };
      row.append(detail,restore);list.append(row);
    });
    $('lyricHistoryDialog').showModal();
  }catch(error){toast(error.message);}
};
$('closeLyricHistory').onclick=()=>$('lyricHistoryDialog').close();

document.querySelectorAll('[data-info]').forEach(b => b.onclick = () => toast(b.dataset.info));
document.querySelectorAll('.mute').forEach((b, i) => b.onclick = () => {
  const audio = i ? accompaniment : source; audio.muted = !audio.muted; b.classList.toggle('off', audio.muted); b.setAttribute('aria-pressed', audio.muted);
});
$('play').onclick = () => source.paused ? play() : pause();
$('previewSegment').onclick = async () => {
  if (!source.paused) { pause(); return; }
  const a=Number($('start').value), b=Number($('end').value);
  if (!$('start').value || !$('end').value || !Number.isFinite(a) || !Number.isFinite(b) || a<0 || b<=a || b>duration()) {
    toast('请先设置有效的试听起点和终点。'); return;
  }
  if (!project()?.assets.source) { toast('请先导入原唱或干声音频。'); return; }
  if (source.readyState < 1) {
    $('previewSegment').disabled=true;
    try { await new Promise((resolve,reject)=>{
      const timeout=setTimeout(()=>finish(false),10000);
      function finish(ok){clearTimeout(timeout);source.removeEventListener('loadedmetadata',ready);source.removeEventListener('error',failed);ok?resolve():reject(new Error());}
      function ready(){finish(true)} function failed(){finish(false)}
      source.addEventListener('loadedmetadata',ready,{once:true});source.addEventListener('error',failed,{once:true});
      if(source.readyState>=1)finish(true);
    }); } catch (_) { toast('音频加载失败，请检查原唱文件。'); return; }
    finally { $('previewSegment').disabled=false; }
    if (!$('edit').open) return;
  }
  pause(); seek(a); await play(b);
  if (!source.paused) $('previewSegment').textContent='Ⅱ 停止原唱';
};
editedPreview.addEventListener('ended', () => {
  $('previewEditedSegment').textContent = '▶ 试听改词';
});
$('previewEditedSegment').onclick = async () => {
  if (!editedPreview.paused) { pause(); return; }
  const p=project(), line=p?.lines[selected], take=p?.renders?.[line?.id];
  if (!take?.asset_id) { toast('当前句还没有改词翻唱，请先生成。'); return; }
  pause();
  editedPreview.src='/api/assets/'+encodeURIComponent(take.asset_id);
  const draftChanged=$('newLyrics').value!==line.text || +$('start').value!==line.start ||
    +$('end').value!==line.end || editWordDirty;
  try {
    await editedPreview.play();
    if (!$('edit').open || editedPreview.paused) return;
    $('previewEditedSegment').textContent='Ⅱ 停止改词';
    if (draftChanged || line.render_state!=='ready') toast('正在试听上次生成的改词版本。');
  } catch (error) {
    if ($('edit').open && error.name!=='AbortError') toast('改词音频加载失败，请检查生成结果。');
  }
};
$('seek').oninput = () => { clipEnd = null; seek(+$('seek').value); };
$('timeline').onclick = openTimeline;
$('timelineQuick').onclick = openTimeline;
function createDialog() { if (busy) return; $('newName').value = ''; $('newProject').showModal(); }
$('create').onclick = $('createMain').onclick = createDialog;
$('cancelCreate').onclick = () => { if (!busy) $('newProject').close(); };
$('newProject').addEventListener('cancel', e => { if (busy) e.preventDefault(); });
$('confirmCreate').onclick = () => {
  const name = $('newName').value.trim(); if (!name) { $('newName').focus(); return; }
  mutate(async () => {
    const p = await api('projects', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name, duration: 30, lines: []})});
    projects.push(p); current = projects.length - 1; selected = 0; render(); loadAudio(); $('newProject').close();
  });
};
$('projectSelect').onchange = e => switchProject(+e.target.value);
function chooseAudio(kind) { if (busy || !project()) return; importKind = kind; $('audioFile').value = ''; $('audioFile').click(); }
$('importSong').onclick = async () => {
  if (!separationReady) { openModelSetup('separation'); return; }
  if(activeJob){toast('当前模型任务尚未完成。');return;}
  if(project()?.assets.source && !await askConfirm('导入并分离歌曲','分离成功后会替换当前人声与伴奏，相关歌词对齐和生成结果需要重新处理。是否继续？','继续导入'))return;
  chooseAudio('song');
};
function syncSvcDialog() {
  const p=project(), reference=p?.assets.reference_pending || p?.assets.reference;
  const target=document.querySelector('input[name="svcTarget"]:checked')?.value;
  const rendered=document.querySelector('input[name="svcTarget"][value="rendered"]');
  rendered.disabled=!p?.rendered_available;
  $('svcComponentState').textContent=svcReady?'SVC 组件已就绪':'SVC 组件尚未就绪';
  $('svcReferenceName').textContent=svcDraftFile?.name || reference?.name || '选择参考人声';
  $('svcReferenceHint').textContent=svcDraftFile ? '已选择 '+svcDraftDuration.toFixed(1)+' 秒，开始转换时导入。' : reference ? '使用工程中已导入的参考人声；可重新选择。' : '建议使用清晰干声，支持 1–30 秒。';
  $('svcStartConvert').disabled=!p || p.id!==svcDraftProjectId || !svcReady || !!activeJob || busy || !(svcDraftFile || reference) || !target || target==='rendered'&&!p.rendered_available;
}
$('startSvc').onclick = () => {
  if (!svcReady) { openModelSetup('svc'); return; }
  const p=project();if(!p || busy)return;
  if(activeJob){toast('当前模型任务尚未完成。');return;}
  svcDraftProjectId=p.id;svcDraftFile=null;svcDraftDuration=null;
  svcTargetKind=activeVoice==='rendered'&&p.rendered_available?'rendered':'source';
  document.querySelector('input[name="svcTarget"][value="'+svcTargetKind+'"]').checked=true;
  const quality=p.svc?.options?.quality || 'standard';
  (document.querySelector('input[name="svcQuality"][value="'+quality+'"]') || document.querySelector('input[name="svcQuality"][value="standard"]')).checked=true;
  $('svcAutoPitch').checked=!!p.svc?.options?.auto_shift;
  $('svcReferencePreview').hidden=true;$('svcReferencePreview').removeAttribute('src');
  $('svcReferenceFile').value='';syncSvcDialog();$('svcDialog').showModal();
};
document.querySelectorAll('input[name="svcTarget"], input[name="svcQuality"]').forEach(input=>input.onchange=syncSvcDialog);
$('svcChooseReference').onclick=()=>{ if(!busy){$('svcReferenceFile').value='';$('svcReferenceFile').click();} };
$('svcReferenceFile').onchange=async()=>{
  const file=$('svcReferenceFile').files[0];if(!file)return;
  try {
    if(file.size>512*1024*1024)throw new Error('文件不能超过 512 MB。');
    const seconds=await audioDuration(file);
    if(seconds<1||seconds>30)throw new Error('参考人声需要 1–30 秒，请裁剪后重试。');
    if(svcDraftUrl)URL.revokeObjectURL(svcDraftUrl);
    svcDraftFile=file;svcDraftDuration=seconds;svcDraftUrl=URL.createObjectURL(file);
    $('svcReferencePreview').src=svcDraftUrl;$('svcReferencePreview').hidden=false;syncSvcDialog();
  } catch(error) { toast(error.message || '无法读取参考人声。'); }
};
$('closeSvc').onclick=()=>{if(!busy)$('svcDialog').close();};
$('svcDialog').addEventListener('cancel',event=>{if(busy)event.preventDefault();});
$('svcDialog').addEventListener('close',()=>{
  $('svcReferencePreview').pause();$('svcReferencePreview').removeAttribute('src');
  if(svcDraftUrl)URL.revokeObjectURL(svcDraftUrl);
  svcDraftUrl=null;svcDraftFile=null;svcDraftDuration=null;svcDraftProjectId=null;
});
$('svcComponentInfo').onclick=()=>{$('svcDialog').close();$('runtimeInfo').click();};
$('svcStartConvert').onclick=()=>{
  if(busy||activeJob)return;
  mutate(async()=>{
    let p=project();if(!p||p.id!==svcDraftProjectId)throw new Error('工程已切换，请重新打开音色转换。');
    svcTargetKind=document.querySelector('input[name="svcTarget"]:checked')?.value;
    const options={quality:document.querySelector('input[name="svcQuality"]:checked')?.value,auto_shift:$('svcAutoPitch').checked};
    if(svcDraftFile){
      p=await api('projects/'+p.id+'/assets/reference_pending',{method:'POST',headers:{'Content-Type':'application/octet-stream',
        'X-File-Name':encodeURIComponent(svcDraftFile.name),'X-Project-Revision':String(p.revision),'X-Audio-Duration':String(svcDraftDuration)},body:svcDraftFile});
      replaceProject(p);
    }
    activeJob=await api('projects/'+p.id+'/svc',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({revision:p.revision,target_kind:svcTargetKind,options})});
    $('svcDialog').close();render();pollJob();toast('音色转换已开始。');
  });
};
$('importSource').onclick = () => chooseAudio('source'); $('importAccompaniment').onclick = () => chooseAudio('accompaniment');
async function audioDuration(file) {
  const url = URL.createObjectURL(file), audio = new Audio();
  try {
    return await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('读取音频超时，请换用 WAV 或 MP3。')), 15000);
      audio.onloadedmetadata = () => { clearTimeout(timer); Number.isFinite(audio.duration) && audio.duration > 0 ? resolve(audio.duration) : reject(new Error('无法识别音频时长。')); };
      audio.onerror = () => { clearTimeout(timer); reject(new Error('浏览器不支持此音频，请换用 WAV 或 MP3。')); }; audio.src = url;
    });
  } finally { audio.removeAttribute('src'); audio.load(); URL.revokeObjectURL(url); }
}
$('audioFile').onchange = () => {
  const file = $('audioFile').files[0]; if (!file) return;
  mutate(async () => {
    if (file.size > 512 * 1024 * 1024) throw new Error('文件不能超过 512 MB。');
    const seconds = await audioDuration(file), p = project();
    if(importKind==='reference_pending' && (seconds<1 || seconds>30))throw new Error('参考人声需要 1–30 秒，请裁剪后重试。');
    if ((importKind === 'source' || importKind === 'song') && p.lines.some(x => x.end > seconds + .001)) throw new Error('新音频比已有歌词短，请创建新工程导入，避免损坏时间轴。');
    const updated = await api('projects/' + p.id + '/assets/' + importKind, {method: 'POST', headers: {
      'Content-Type': 'application/octet-stream', 'X-File-Name': encodeURIComponent(file.name), 'X-Project-Revision': String(p.revision), 'X-Audio-Duration': String(seconds)}, body: file});
    replaceProject(updated);
    if(importKind === 'song') {
      activeJob=await api('projects/'+updated.id+'/separation',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:updated.revision})});
      render(); pollJob(); toast('歌曲已导入，正在分离人声和伴奏。');
    } else if(importKind==='reference_pending') {
      activeJob=await api('projects/'+updated.id+'/svc',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:updated.revision,target_kind:svcTargetKind})});
      render();pollJob();toast('参考人声已导入，正在转换音色。');
    } else {
      loadAudio();
      if(importKind==='accompaniment' && updated.lines.some(x=>x.render_state==='ready') && !activeJob) {
        await startCompose();toast('伴奏已导入，正在复用已生成的唱句重新混音。');
      } else toast('音频已复制到本机工程，可以试听。');
    }
  });
};
$('importLyrics').onclick = () => { if (busy || !project()) return; if (!project().assets.source) { toast('请先导入原唱音频，以确定时间轴长度。'); return; } $('lyricsFile').value = ''; $('lyricsFile').click(); };
$('lyricsFile').onchange = async () => {
  const file = $('lyricsFile').files[0]; if (!file) return;
  if (project().lines.length && !await askConfirm('导入歌词','导入字幕或歌词会替换当前歌词；原有内容会保存在历史歌词中。是否继续？','继续导入')) return;
  mutate(async () => {
    if (file.size > 2 * 1024 * 1024) throw new Error('字幕或歌词文件不能超过 2 MB。');
    const format = file.name.match(/\.(srt|lrc)$/i)?.[1]?.toLowerCase() || 'auto';
    const p = project();
    const updated = await api('projects/' + p.id + '/lyrics', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({revision: p.revision, format, text: await decodeSubtitle(file)})});
    selected = 0; replaceProject(updated);
    $('exportFormat').value = format === 'lrc' ? 'lrc' : 'srt';
    toast('已导入 ' + updated.lines.length + ' 句；SRT 会保留原始起止时间与句间空隙。');
  });
};
let adaptedTarget;
$('importAdapted').onclick = () => {
  if (busy) return;
  const p = project();
  if (!p?.lines.length) { toast('请先导入原歌词，再导入改编歌词。'); return; }
  adaptedTarget = {id:p.id, revision:p.revision};
  $('adaptedFile').value = '';
  $('adaptedFile').click();
};
$('adaptedFile').onchange = async () => {
  const file = $('adaptedFile').files[0], target = adaptedTarget;
  if (!file || !target) return;
  if (file.size > 2 * 1024 * 1024) { toast('改编歌词文件不能超过 2 MB。'); return; }
  const format = file.name.split('.').pop().toLowerCase();
  if (!await askConfirm('导入改编歌词','按原歌词顺序逐句替换新词，沿用原时间轴。TXT 每行一句，导入前的歌词可从历史歌词恢复。','导入改编歌词')) return;
  mutate(async () => {
    const updated = await api('projects/' + target.id + '/adapted-lyrics', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({revision:target.revision, format, text:await decodeSubtitle(file)})});
    if (project()?.id === target.id) replaceProject(updated);
    toast('已匹配 ' + updated.lines.length + ' 句改编歌词。');
  });
};
$('exportLyrics').onclick = () => {
  if (!project()?.lines.length) { toast('请先导入歌词。'); return; }
  const format = $('exportFormat').value;
  const a = document.createElement('a'); a.href = '/api/projects/' + project().id + '/lyrics.' + format; a.download = 'lyrics.' + format; a.click();
};
async function decodeSubtitle(file) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  if (bytes[0] === 0xff && bytes[1] === 0xfe) return new TextDecoder('utf-16le', {fatal:true}).decode(bytes);
  if (bytes[0] === 0xfe && bytes[1] === 0xff) return new TextDecoder('utf-16be', {fatal:true}).decode(bytes);
  try { return new TextDecoder('utf-8', {fatal:true}).decode(bytes); }
  catch (_) {
    try { return new TextDecoder('gb18030', {fatal:true}).decode(bytes); }
    catch (_) { throw new Error('无法识别字幕编码，请另存为 UTF-8 后导入。'); }
  }
}
$('useSingingExample').onclick = () => {
  const index = projects.findIndex(x => !x.source_issue && x.rendered_available);
  if (index >= 0) switchProject(index);
};
async function init() {
  try {
    const session = await api('session'); token = session.token; modelReady = session.capabilities.inference;
    projects = await api('projects'); activeVoice = (project()?.rendered_preview_available ?? project()?.rendered_available) ? 'rendered' : 'source'; render(); loadAudio(); $('saveState').textContent = '本机服务已连接';
    document.querySelector('.badge').textContent = modelReady ? '独立模型已就绪' : '模型环境待配置';
    const runtime = await api('runtime');
    svcReady=!!runtime.svc_ready; transcriptionReady=!!runtime.transcription_ready; separationReady=!!runtime.separation_ready;
    yingReady=!!runtime.ying_ready;
    document.querySelector('[data-mode="ying"]').disabled=!yingReady;
    document.querySelector('[data-mode="ying"]').title=yingReady?'使用本机 YingMusic 模型生成': '未找到本机 YingMusic 模型';
    if (runtime.active_job) { activeJob = runtime.active_job; pollJob(); }
    render();
    if (!runtime.ready) { try { if (!localStorage.getItem('cisheng-setup-seen')) { localStorage.setItem('cisheng-setup-seen','1'); $('runtimeInfo').click(); } } catch (_) {} }

  } catch (error) {
    render(); document.querySelector('.badge').textContent = '服务未连接'; toast('请运行 python -m backend.app，再打开本机服务地址。');
  }
}
function openTimeline() {
  if (!project()?.lines.length) { toast('请先导入 SRT / LRC。'); return; }
  if (busy) return;
  timelineSelected = Math.min(selected, project().lines.length-1); timelineDirty = false; timelineBoundaryDirty = false; timelineBoundaryDraft = null;
  $('timelineExportFormat').value=$('exportFormat').value; syncTimelineFormats();
  enterOriginalEditor();
  renderTimeline(); $('timelineEditor').showModal();
}
function renderTimeline() {
  const p = project(), line = p?.lines[timelineSelected];
  if (!line) return;
  const list = $('timelineLines'); list.replaceChildren();
  p.lines.forEach((x,i) => {
    const button = document.createElement('button');
    button.className = i === timelineSelected ? 'selected' : '';
    const text = document.createElement('span'); text.textContent = (i+1)+'. '+x.text; text.title = x.text;
    const time = document.createElement('small'); time.textContent=x.start.toFixed(3)+' — '+x.end.toFixed(3)+' s';
    button.append(text,time);button.onclick=async()=>{
      if (busy || timelineHasChanges() && !await askConfirm('切换句子','当前有未保存的修改，放弃后切换句子？','放弃并切换')) return;
      timelineSelected=i;timelineDirty=false;timelineBoundaryDirty=false;timelineBoundaryDraft=null;pause();renderTimeline();
    };list.append(button);
  });
  $('timelineLabel').textContent='第 '+(timelineSelected+1)+' 句 · '+line.start.toFixed(3)+' — '+line.end.toFixed(3)+' s';
  $('timelineOriginal').value=line.original;
  $('timelineSource').textContent=line.original;
  if(!timelineBoundaryDraft)timelineBoundaryDraft={start:line.start,end:line.end};
  const timings=line.unit_timings||[];
  $('timelineWordEmpty').hidden=timings.length>0;
  $('timelineWordWave').hidden=timings.length===0;
  $('timelineWordDetails').hidden=timings.length===0;
  $('saveWordTimes').hidden=timings.length===0;
  $('timelineHint').textContent=timings.length ? '已保存逐字时间 · '+({manual:'手动校正',HubertFA:'歌声对齐',Qwen:'自动对齐'}[line.alignment_engine]||'已对齐')+' · 手动保存会使旧生成过期。' : '还没有真实逐字时间，请提取；主界面暂按单位数估算定位。';
  if(line.needs_review) $('timelineHint').textContent='自动识词草稿，请校对原词和整句边界，确认后点击保存原词校正。'+$('timelineHint').textContent;
  $('extractWords').disabled=!!activeJob||(modelReady&&!!p.source_issue);
  $('extractWords').title=p.source_issue||'';
  $('cancelAlignment').hidden=!activeJob || activeJob.kind!=='align';
  $('wordTimeline').replaceChildren();
  const targets=lyricUnits(line.text),unitMap=lyricUnitMap(line.original,line.text);
  timings.forEach((timing,i)=>{
    const row=document.createElement('div');row.className='word-time-row';row.dataset.word=timing.text;
    const button=document.createElement('button');const mapped=unitMap?.[i];button.textContent=timing.text+(mapped!==undefined && mapped!==null && targets[mapped]?.toLowerCase()!==timing.text.toLowerCase()?' → '+targets[mapped]:'');
    button.title='点击试听本字';
    const start=document.createElement('input'),end=document.createElement('input');
    for(const [input,value,label] of [[start,timing.start,'起点'],[end,timing.end,'终点']]) {
      input.type='number';input.step='.001';input.min=line.start;input.max=line.end;input.value=value.toFixed(3);
      input.setAttribute('aria-label','第 '+(i+1)+' 字'+label);input.disabled=!!activeJob;
      input.oninput=()=>{timelineDirty=true;refreshWordWave();};
    }
    start.className='word-start';end.className='word-end';
    button.onclick=()=>{
      const a=parseFloat(start.value),b=parseFloat(end.value);
      if(!Number.isFinite(a)||!Number.isFinite(b)||a>=b){toast('请先填写有效边界。');return;}
      pause();seek(a);play(b);
    };
    const divider=document.createElement('span');divider.textContent='—';
    row.append(button,start,divider,end);$('wordTimeline').append(row);
  });
  $('saveWordTimes').disabled=!timings.length||!!activeJob;
  $('splitPhrase').disabled=!!activeJob||lyricUnits(line.original).length<2;
  $('mergePhrase').disabled=!!activeJob||timelineSelected>=p.lines.length-1;
  $('takeHistory').disabled=!!activeJob;
  ensureTimelinePhraseWave(); refreshTimelineBoundary(); ensureWordWave(); refreshWordWave();
}
$('timelineExport').onclick=()=>{ $('exportFormat').value=$('timelineExportFormat').value; $('exportLyrics').click(); };
$('timelinePreview').onclick=()=>{ if(!timelineBoundaryDraft)return; pause(); seek(timelineBoundaryDraft.start); play(timelineBoundaryDraft.end); };
$('timelineResetBoundary').onclick=()=>{ const line=project()?.lines[timelineSelected];if(!line)return;timelineBoundaryDraft={start:line.start,end:line.end};timelineBoundaryDirty=false;refreshTimelineBoundary(); };
$('timelineSaveBoundary').onclick=async()=>{
  if(!timelineBoundaryDirty || busy || activeJob)return;
  if(timelineDirty && !await askConfirm('保存句边界','保存边界会放弃当前未保存的原词或逐字时间修改。是否继续？','保存边界'))return;
  mutate(async()=>{
    const p=project(),line=p.lines[timelineSelected],draft=timelineBoundaryDraft;
    const value={revision:p.revision,start:draft.start,end:draft.end,text:line.text,mode:line.mode,restore:line.restore};
    if(line.generation_params)value.generation_params=line.generation_params;
    const saved=await api('projects/'+p.id+'/segments/'+line.id+'/phrase',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});
    timelineDirty=false;timelineBoundaryDirty=false;timelineBoundaryDraft=null;replaceProject(saved);renderTimeline();toast('句边界已保存；相关时间轴和生成结果需要重新处理。');
  });
};
$('timelineOriginal').oninput=()=>{timelineDirty=true;};
$('closeTimeline').onclick=async()=>{
  if(busy||timelineHasChanges()&&!await askConfirm('关闭时间轴','放弃尚未保存的时间轴修改？','放弃修改'))return;
  timelineDirty=false;timelineBoundaryDirty=false;timelineBoundaryDraft=null;pause();leaveOriginalEditor();$('timelineEditor').close();
};
$('timelineEditor').addEventListener('cancel',event=>{
  event.preventDefault();
  if(!busy) $('closeTimeline').click();
});
$('saveOriginal').onclick=async()=>{
  const line=project().lines[timelineSelected], text=$('timelineOriginal').value.trim();
  if(text===line.original && !line.needs_review){toast('原词没有变化；如调整了逐字时间，请点击保存逐字时间。');return;}
  const rows=Array.from($('wordTimeline').children), saved=line.unit_timings||[];
  const timesChanged=rows.length===saved.length && rows.some((row,i)=>
    row.querySelector('.word-start').value!==saved[i].start.toFixed(3) || row.querySelector('.word-end').value!==saved[i].end.toFixed(3));
  if(timesChanged&&!await askConfirm('保存原词',text===line.original ? '确认原词会放弃尚未保存的逐字时间。是否继续？' : '保存原词会使当前句的旧对齐失效，尚未保存的逐字时间也会丢失。是否继续？','继续保存'))return;
  mutate(async()=>{
  const p=project(),line=p.lines[timelineSelected];
  const updated=await api('projects/'+p.id+'/segments/'+line.id+'/original',{
    method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:p.revision,text:$('timelineOriginal').value})});
  timelineDirty=false;replaceProject(updated);renderTimeline();toast('原词校正已保存；已修改的新歌词保留，原词变化后需重新对齐。');
  });
};
$('extractWords').onclick=async()=>{
  if (!modelReady) { openModelSetup('singer'); return; }
  const p=project(),line=p.lines[timelineSelected];
  if($('timelineOriginal').value.trim()!==line.original){toast('请先保存原词校正，再提取时间。');return;}
  if(timelineHasChanges()&&!await askConfirm('重新提取时间','重新提取会替换尚未保存的逐字时间，是否继续？','继续提取'))return;
  if(line.render_state==='ready'&&!await askConfirm('重新提取时间','重新提取会使当前句的旧生成过期，是否继续？','继续提取'))return;
  mutate(async()=>{
    activeJob=await api('projects/'+p.id+'/alignments',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:p.revision,segment_ids:[line.id]})});
    timelineDirty=false;renderTimeline();render();pollJob();
  });
};
$('extractWordsEmpty').onclick=()=>$('extractWords').click();
$('cancelAlignment').onclick=()=>$('cancelJob').click();
$('saveWordTimes').onclick=()=>mutate(async()=>{
  const p=project(),line=p.lines[timelineSelected];
  if($('timelineOriginal').value.trim()!==line.original)throw new Error('请先保存原词校正。');
  const timings=Array.from($('wordTimeline').children).map(row=>({text:row.dataset.word,
    start:parseFloat(row.querySelector('.word-start').value),end:parseFloat(row.querySelector('.word-end').value)}));
  const updated=await api('projects/'+p.id+'/segments/'+line.id+'/timeline',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:p.revision,unit_timings:timings})});
  timelineDirty=false;replaceProject(updated);renderTimeline();toast('逐字时间已保存，下一次生成会复用这些边界。');
});
$('openPhraseEditor').onclick=async()=>{
  if(timelineHasChanges()&&!await askConfirm('打开整句编辑','放弃尚未保存的修改并打开整句编辑？','放弃并打开'))return;
  const index=timelineSelected;timelineDirty=false;$('timelineEditor').close();openEdit(index);
};
$('splitPhrase').onclick=async()=>{
  if(timelineHasChanges()&&!await askConfirm('拆分当前句','拆分将放弃尚未保存的修改，是否继续？','继续拆分'))return;
  const line=project().lines[timelineSelected], original=lyricUnits(line.original), replacement=lyricUnits(line.text);
  if(original.length!==replacement.length){toast('原词和新词单位数不同，请先调整到一致后再拆分。');return;}
  const select=$('splitPosition');select.replaceChildren();
  for(let i=1;i<original.length;i++){
    const option=document.createElement('option');option.value=i;option.textContent=original.slice(0,i).join('')+' ｜ '+original.slice(i).join('');select.append(option);
  }
  select.value=String(Math.floor(original.length/2));
  select.onchange=()=>{
    const i=+select.value,t=line.unit_timings||[];
    const estimated=t.length===original.length?(t[i-1].end+t[i].start)/2:line.start+(line.end-line.start)*i/original.length;
    $('splitTime').min=(line.start+.12).toFixed(3);$('splitTime').max=(line.end-.12).toFixed(3);
    $('splitTime').value=Math.max(line.start+.12,Math.min(line.end-.12,estimated)).toFixed(3);
    $('splitPreview').textContent='新词：'+replacement.slice(0,i).join('')+' ｜ '+replacement.slice(i).join('')+'；拆分后需要重新对齐和生成。';
  };
  select.onchange();$('splitDialog').showModal();
};
$('cancelSplit').onclick=()=>$('splitDialog').close();
$('confirmSplit').onclick=()=>mutate(async()=>{
  const p=project(),line=p.lines[timelineSelected];
  const value={revision:p.revision,action:'split',position:+$('splitPosition').value,time:+$('splitTime').value};
  const saved=await api('projects/'+p.id+'/segments/'+line.id+'/structure',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});
  timelineDirty=false;replaceProject(saved);renderTimeline();$('splitDialog').close();toast('已拆分；请检查两句边界并重新对齐。');
});
$('mergePhrase').onclick=async()=>{
  if(timelineHasChanges()&&!await askConfirm('合并歌词','合并将放弃尚未保存的修改，是否继续？','继续合并'))return;
  if(!await askConfirm('合并歌词','将当前句与下一句合并，相关对齐和生成结果需要重新处理。是否继续？','确认合并'))return;
  mutate(async()=>{
    const p=project(),line=p.lines[timelineSelected];
    const saved=await api('projects/'+p.id+'/segments/'+line.id+'/structure',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:p.revision,action:'merge'})});
    timelineDirty=false;replaceProject(saved);renderTimeline();toast('已合并；请检查边界并重新对齐。');
  });
};
$('takeHistory').onclick=async()=>{
  if(timelineHasChanges()&&!await askConfirm('查看历史演唱','切换历史版本会放弃尚未保存的时间轴修改，是否继续查看？','继续查看'))return;
  const p=project(),line=p.lines[timelineSelected];
  try {
    const result=await api('projects/'+p.id+'/segments/'+line.id+'/takes');
    if(project()?.id!==p.id||project()?.lines[timelineSelected]?.id!==line.id)return;
    const list=$('takeList');list.replaceChildren();pause();
    if(!result.takes.length){const empty=document.createElement('p');empty.className='muted';empty.textContent='当前句还没有可试听的历史演唱。';list.append(empty);}
    result.takes.forEach((take,i)=>{
      const row=document.createElement('div');row.className='take-row';
      const description=document.createElement('div');
      const label=document.createElement('strong');label.textContent='版本 '+(result.takes.length-i)+(take.current?' · 当前使用':'');
      const detail=document.createElement('p');detail.className='muted tiny';
      const params=take.generation_params;
      const parameterText=params ? ' · '+params.steps+' 步 · CFG '+params.cfg+(take.seed_used != null?' · 种子 '+take.seed_used:'') : '';
      detail.textContent=(take.created_at?new Date(take.created_at*1000).toLocaleString('zh-CN'):'历史版本')+' · '+(take.lyrics||'歌词信息未记录')+parameterText+(take.compatible?'':' · 与当前歌词、边界或参数不一致，仅可试听');
      description.append(label,detail);
      const audio=document.createElement('audio');audio.controls=true;audio.preload='none';audio.src='/api/assets/'+take.asset_id;audio.setAttribute('aria-label','试听版本 '+(result.takes.length-i));
      const choose=document.createElement('button');choose.textContent=take.current?'使用中':'使用此版本';choose.disabled=take.current||!take.compatible||!!activeJob;
      choose.onclick=()=>mutate(async()=>{
        const current=project(),currentLine=current.lines[timelineSelected];
        const updated=await api('projects/'+current.id+'/segments/'+currentLine.id+'/takes/'+take.asset_id+'/activate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:current.revision})});
        timelineDirty=false;replaceProject(updated);renderTimeline();$('takeDialog').close();
        await startCompose();toast('已切换演唱版本，正在重新合成音频。');
      });
      row.append(description,audio,choose);list.append(row);
    });
    $('takeDialog').showModal();
  }catch(error){toast(error.message);}
};
$('closeTakes').onclick=()=>$('takeDialog').close();
$('takeDialog').addEventListener('close',()=>document.querySelectorAll('#takeList audio').forEach(audio=>audio.pause()));

const STAGES = {starting:'启动模型进程', loading_ying:'加载 YingMusic 模型', separation_decode:'读取歌曲', separation_vocal:'分离人声与伴奏', separation_dry:'人声去混响与回声', asr_decode:'读取人声', asr_load:'加载识词模型', asr_recognize:'识别歌词草稿', svc_restore:'恢复原句音色', svc_pitch:'分析参考与目标人声音高', svc_load:'加载音色转换模型', svc_generate:'转换人声音色', svc_mix:'合成变声混音', reference_masked:'准备本句音色参考', alignment_cached:'复用已保存的逐字时间', alignment:'原词逐字对齐', singing_alignment:'歌声逐字对齐', alignment_warning:'校验短音节边界', pitch_warning:'修复 MIDI 音高', pitch:'提取旋律音高', notes:'提取音符与槽位', loading_singer:'加载演唱模型', generating:'生成演唱', composing:'拼接与混音', complete:'任务完成'};
$('closeRenderErrors').onclick = () => $('renderErrors').close();
function showRenderErrors(p, issues, message) {
  if (project()?.id !== p.id) return;
  const list = $('renderErrorList'), dialog = $('renderErrors');
  list.replaceChildren();
  $('renderErrorTitle').textContent = issues?.length ? '生成遇到问题 · ' + issues.length + ' 句' : '生成失败';
  if (!issues?.length) list.textContent = message;
  for (const issue of issues || []) {
    const index = p.lines.findIndex(x => x.id === issue.segment_id), line = p.lines[index];
    const row = document.createElement('section');
    const title = document.createElement('strong');
    title.textContent = '第 ' + (issue.number || index+1) + ' 句' + (line ? ' · ' + line.start.toFixed(3) + ' — ' + line.end.toFixed(3) + ' s' : '');
    row.append(title);
    for (const text of [...(line ? ['原词　'+line.original,'新词　'+line.text] : []),issue.reason]) {
      const paragraph = document.createElement('p');
      paragraph.textContent = text;
      row.append(paragraph);
    }
    if (line) {
      const button = document.createElement('button');
      button.textContent = '编辑这一句';
      button.onclick = () => {
        const currentIndex = project()?.lines.findIndex(x => x.id === issue.segment_id);
        dialog.close();
        if (project()?.id === p.id && currentIndex >= 0) openEdit(currentIndex);
      };
      row.append(button);
    }
    list.append(row);
  }
  if (!dialog.open) dialog.showModal();
}
async function startRender(ids, p=project()) {
  const defaults=await api('generation-defaults');
  for (const mode of [...new Set(p.lines.filter(x=>ids.includes(x.id)).map(x=>x.mode))]) {
    if (!defaults[mode] || !p.lines.some(x=>ids.includes(x.id)&&x.mode===mode&&!x.generation_params)) continue;
    const result=await api('projects/'+p.id+'/generation-parameters',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({revision:p.revision,mode,scope:'missing',segment_ids:ids,parameters:defaults[mode]})});
    p=result.project;
    if (project()?.id===p.id) replaceProject(p);
  }
  try {
    activeJob = await api('projects/' + p.id + '/renders', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:p.revision,segment_ids:ids})});
  } catch (error) {
    showRenderErrors(p,error.issues,error.message);
    throw error;
  }
  render(); pollJob();
}
async function pollJob() {
  clearTimeout(pollTimer);
  if (!activeJob) return;
  const panel = $('jobPanel'); panel.hidden = false;
  try {
    const job = await api('jobs/' + activeJob.id); activeJob = job;
    $('cancelJob').textContent = job.kind === 'notes' ? '取消音符分析' : job.kind === 'align' ? '取消对齐' : job.kind === 'separate' ? '取消分离' : job.kind === 'transcribe' ? '取消识词' : job.kind === 'svc' ? '取消音色转换' : job.kind === 'compose' ? '取消混音' : '取消生成';
    $('jobStage').textContent = STAGES[job.stage] || job.stage;
    if ($('timelineEditor').open && !timelineHasChanges() && !['succeeded','failed','cancelled','interrupted','stale'].includes(job.status)) $('timelineHint').textContent='正在'+(STAGES[job.stage]||job.stage)+'…';
    if (job.kind === 'notes' && $('edit').open && project()?.id === job.project_id && job.segment_ids.includes(project().lines[selected]?.id))
      $('midiNotesStatus').textContent = (STAGES[job.stage] || '分析音符') + '…';
    $('jobDetail').textContent = job.kind === 'render'
      ? ' · 本次生成 ' + job.segment_ids.length + ' 句，复用 ' + (job.reuse_count || 0) + ' 句' + (job.stage === 'generating' && job.position ? ' · 第 ' + job.position + ' / ' + job.total + ' 句' : '')
      : (job.position ? ' · 第 ' + job.position + ' / ' + job.total + ' 句' : '') + ' · 模型进程独立运行';
    if (['succeeded','failed','cancelled','interrupted','stale'].includes(job.status)) {
      const latest = await api('projects/' + job.project_id);
      activeJob = null; panel.hidden = true;
      const projectIndex = projects.findIndex(item => item.id === job.project_id);
      if (projectIndex !== -1) projects[projectIndex] = latest;
      if (project() && project().id === job.project_id) {
        if (['render','compose'].includes(job.kind) && job.status === 'succeeded' && latest.rendered_available) activeVoice = 'rendered';
        if (job.kind === 'svc' && job.status === 'succeeded' && latest.svc_available) activeVoice = 'svc';
        if (!['align','notes'].includes(job.kind)) loadAudio();
        if (job.kind === 'align' && $('edit').open &&
            job.segment_ids.includes(latest.lines[selected]?.id)) {
          if (job.status === 'succeeded') {
            editWordDraft=(latest.lines[selected].unit_timings || []).map(word=>({...word}));
            editWordDirty=false;
          }
          refreshEditWordWave();
        }
        if (job.kind === 'notes' && $('edit').open && job.segment_ids.includes(latest.lines[selected]?.id)) {
          if (job.status === 'succeeded') await loadMidiNotes(latest, latest.lines[selected]);
          else $('midiNotesStatus').textContent = job.error || '音符分析已取消，可重新分析。';
          validate();
        }
      }
      render();
      if ($('timelineEditor').open && !timelineHasChanges()) renderTimeline();
      if (batchProjectId === job.project_id && job.kind === 'render') {
        if (job.status === 'succeeded' && batchQueue.length) {
          const ids=batchQueue.shift();
          try { await startRender(ids,latest); toast('上一批已完成，正在生成下一批。'); }
          catch(error) { batchQueue=[];batchProjectId=null;toast(error.message); }
          return;
        }
        batchQueue=[];batchProjectId=null;
      }
      if (job.kind === 'render' && job.status === 'failed')
        showRenderErrors(latest,job.error_segment_id ? [{segment_id:job.error_segment_id,reason:job.error}] : [],job.error);
      toast(job.status === 'succeeded' ? (job.kind === 'notes' ? '音符分析完成。' : job.kind === 'align' ? '逐字时间已提取并保存。' : job.kind === 'separate' ? '人声和伴奏已分离并导入工程。' : job.kind === 'transcribe' ? '已生成歌词草稿，请逐句校对原词和边界。' : job.kind === 'svc' ? '音色转换完成，可试听或导出。' : job.kind === 'compose' ? '已复用演唱片段完成重新混音。' : '真实演唱已生成，可切换原唱/改词试听。') + (job.warnings.length ? job.warnings.join(' ') : '') : job.error || '任务已取消。');
      return;
    }
    const progress = $('jobProgress');
    progress.hidden = job.kind !== 'render';
    panel.classList.toggle('render-progress', !progress.hidden);
    if (!progress.hidden) {
      const total = job.segment_ids.length;
      const completed = Math.min(total, job.completed ?? 0);
      const stages = {starting:2, alignment:8, alignment_cached:8, singing_alignment:8,
        pitch:17, notes:24, loading_singer:31, loading_ying:31, generating:40,
        reference_masked:40, svc_restore:92, composing:97, complete:99};
      const estimate = Math.max(stages[job.stage] ?? 2, completed > 0 ? 40 + 50 * completed / total : 0);
      const previous = progress.dataset.job === job.id ? progress.value : 0;
      progress.dataset.job = job.id;
      progress.value = Math.max(previous, estimate);
      panel.style.setProperty('--render-done', progress.value + '%');
    }
    $('cancelJob').disabled = job.status === 'cancelled';
  } catch (error) { $('jobDetail').textContent = '读取进度失败，正在重试：' + error.message; }
  pollTimer = setTimeout(pollJob, 1500);
}
$('cancelJob').onclick = async () => {
  if (!activeJob) return;
  try { await api('jobs/' + activeJob.id + '/cancel', {method:'POST'}); pollJob(); }
  catch (error) { toast(error.message); }
};
document.querySelectorAll('[data-version]').forEach(button => button.onclick = () => {
  const mode = button.dataset.version;
  if (mode === 'rendered' && !(project()?.rendered_preview_available ?? project()?.rendered_available)) { toast('请先生成当前改词。'); return; }
  if (mode === 'svc' && !project()?.svc_available) {
    if (!project()?.assets.source) toast('请先导入原唱或干声，再设置音色转换。');
    else if (project()?.source_issue) toast(project().source_issue);
    else if (activeJob) toast('当前有任务进行中，请完成后再设置音色转换。');
    else $('startSvc').click();
    return;
  }
  const position = source.currentTime || +$('seek').value, resume = !source.paused;
  activeVoice = mode; loadAudio(position, resume);
});
for (const [id, kind] of [['exportVoice','rendered'],['exportMix','mixed'],['exportSvc','svc'],['exportSvcMix','svc_mixed']]) {
  $(id).onclick = () => { const a = document.createElement('a'); a.href='/api/projects/'+project().id+'/exports/'+kind; a.download=kind+'.wav'; a.click(); };
}
async function refreshRuntimeDetails() {
    const state = await api('runtime'); const root = $('runtimeDetails'); root.replaceChildren();
    const heading=document.createElement('div'); heading.className='runtime-heading';
    const title=document.createElement('strong'); title.textContent='组件状态';
    const hint=document.createElement('span'); hint.textContent='按需安装，随时查看'; heading.append(title,hint); root.append(heading);
    const grid=document.createElement('div'); grid.className='runtime-status-grid';
    for (const [name,description,ready] of [
      ['歌声生成','演唱与 MIDI 分析',state.ready],
      ['人声分离','提取人声与伴奏',state.separation_ready],
      ['自动识词','生成歌词草稿',state.transcription_ready],
      ['音色转换','SVC 人声转换',state.svc_ready],
      ['歌声对齐',state.singing_aligner?'HubertFA':'使用备用对齐',state.singing_aligner]
    ]) {
      const card=document.createElement('div'); card.className='runtime-status-card';
      const label=document.createElement('div'); const strong=document.createElement('strong'); strong.textContent=name;
      const small=document.createElement('small'); small.textContent=description; label.append(strong,small);
      const badge=document.createElement('span'); badge.className='runtime-badge '+(ready?'is-ready':'is-missing'); badge.textContent=ready?'● 已就绪':'○ 未安装';
      card.append(label,badge); grid.append(card);
    }
    root.append(grid);
    const diagnostics=document.createElement('details'); diagnostics.className='setup-diagnostics'; const caption=document.createElement('summary');caption.textContent='详细组件与存储位置';diagnostics.append(caption);root.append(diagnostics);
    const checks=document.createElement('div');checks.className='runtime-checks';
    for(const [name,ready] of Object.entries(state.checks)) {const item=document.createElement('span');item.className='runtime-check '+(ready?'is-ready':'is-missing');item.textContent=(ready?'✓ ':'– ')+({singer:'演唱模型',pitch:'音高提取',notes:'音符分析',note_pitch:'音符基频',note_boundaries:'音符边界模型',model_files:'模型配置与词典',aligner:'歌词对齐',python:'独立运行环境',source:'推理程序',worker_dependencies:'音频处理依赖',gpu_packages:'GPU 运算组件'}[name]||name);checks.append(item);} diagnostics.append(checks);
    for(const [label,value] of [['使用范围',state.limits],['模型目录',state.model_root],['推理程序',state.source_root]]) {const row=document.createElement('div');row.className='runtime-path';const name=document.createElement('strong');name.textContent=label;const content=document.createElement('span');content.textContent=value||'—';row.append(name,content);diagnostics.append(row);}
    modelReady=!!state.ready; svcReady=!!state.svc_ready; transcriptionReady=!!state.transcription_ready; separationReady=!!state.separation_ready;
    render();
}
async function openModelSetup(component) {
  try {
    await refreshRuntimeDetails();
    document.querySelectorAll('input[name=setupComponent]').forEach(input=>{input.checked=input.value===component;});
    if (!$('runtimeDialog').open) $('runtimeDialog').showModal();
    await refreshModelSetup();
    if ($('setupMessage').textContent === '歌声核心模型尚未校验。') $('setupMessage').textContent='该功能需要安装相关组件，请选择下载或稍后再说。';
  } catch(error) { toast('无法打开组件管理：'+error.message); }
}
$('runtimeInfo').onclick = async () => {
  try {
    await refreshRuntimeDetails();
    $('runtimeDialog').showModal(); refreshModelSetup();
  } catch(error) {toast(error.message);}
};
let setupTimer = null, setupWasRunning = false;
async function refreshModelSetup() {
  clearTimeout(setupTimer);
  if (!$('runtimeDialog').open) return;
  try {
    const state = await api('model-setup');
    const percent = state.total ? Math.min(100, Math.floor(state.done / state.total * 100)) : 0;
    $('setupMessage').textContent = state.message;
    $('setupPercent').textContent = state.total ? percent + '%' : '';
    $('setupProgress').value = percent;
    $('setupCurrent').textContent = state.current || '共享 GPU 组件约 3.2 GiB，首次使用 AI 功能时自动安装一次。';
    $('setupStart').disabled = state.status === 'running';
    $('setupCancel').hidden = state.status !== 'running';
    $('setupSkip').textContent = state.status === 'running' ? '后台下载，进入工作台' : '先进入工作台';
    document.querySelectorAll('input[name=setupComponent]').forEach(input=>input.disabled=state.status==='running');
    $('setupStart').textContent = state.status === 'running' ? '正在准备…' : state.status === 'failed' ? '重试下载与校验' : '下载所选模型';
    if (state.status === 'running') { setupWasRunning=true; setupTimer = setTimeout(refreshModelSetup, 700); }
    else if (setupWasRunning) { setupWasRunning=false; await refreshRuntimeDetails(); }
  } catch (error) { $('setupMessage').textContent = '无法读取准备状态：' + error.message; }
}
$('setupStart').onclick=async()=>{
  try { const components=Array.from(document.querySelectorAll('input[name=setupComponent]:checked')).map(x=>x.value); if (!components.length) { toast('请选择需要下载的模型。'); return; } $('setupStart').disabled=true; await api('model-setup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({components})}); await refreshModelSetup(); }
  catch(error){ $('setupStart').disabled=false; toast(error.message); }
};
$('closeRuntime').onclick=()=>$('runtimeDialog').close();
$('setupCancel').onclick=async()=>{try {await api('model-setup/cancel',{method:'POST'});await refreshModelSetup();}catch(error){toast(error.message);}};
$('setupSkip').onclick=()=>$('runtimeDialog').close();
$('runtimeDialog').addEventListener('close',()=>clearTimeout(setupTimer));
init().then(checkForUpdates);

const downloadPicker = $('downloadPicker');
for (const id of ['exportLyrics','exportVoice','exportMix']) $(id).addEventListener('click',()=>{downloadPicker.open=false;});
document.addEventListener('click',event=>{if (!downloadPicker.contains(event.target)) downloadPicker.open=false;});
downloadPicker.addEventListener('keydown',event=>{if(event.key==='Escape' && downloadPicker.open){downloadPicker.open=false;downloadPicker.querySelector('summary').focus();}});

function syncDownloadFormats(){document.querySelectorAll('[data-export-format]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.exportFormat===$('exportFormat').value)));}
document.querySelectorAll('[data-export-format]').forEach(button=>button.addEventListener('click',()=>{$('exportFormat').value=button.dataset.exportFormat;syncDownloadFormats();}));
downloadPicker.addEventListener('toggle',syncDownloadFormats);

document.addEventListener('click',event=>{const picker=$('projectPicker');if(!picker.contains(event.target))picker.open=false;});
$('projectPicker').addEventListener('keydown',event=>{if(event.key==='Escape' && $('projectPicker').open){$('projectPicker').open=false;$('projectPicker').querySelector('summary').focus();}});

document.querySelectorAll('.track-wave').forEach(track=>{
  function position(event){if(!project()?.assets.source){toast('请先导入原唱或干声音频。');return;}const rect=track.getBoundingClientRect();if(!rect.width)return;clipEnd=null;seek(Math.max(0,Math.min(1,(event.clientX-rect.left)/rect.width))*duration());}
  track.addEventListener('pointerdown',event=>{if(event.button!==0)return;event.preventDefault();track.focus();track.setPointerCapture(event.pointerId);position(event);});
  track.addEventListener('pointermove',event=>{if(track.hasPointerCapture(event.pointerId))position(event);});
  track.addEventListener('keydown',event=>{if(!project()?.assets.source)return;let target=Number($('seek').value);if(event.key==='ArrowRight'||event.key==='ArrowUp')target+=event.shiftKey?5:1;else if(event.key==='ArrowLeft'||event.key==='ArrowDown')target-=event.shiftKey?5:1;else if(event.key==='Home')target=0;else if(event.key==='End')target=duration();else return;event.preventDefault();clipEnd=null;seek(target);});
});

let updateTimer=null, updateRequested=false;
async function pollUpdate(){
  try {
    const state=await api('update');
    const labels={idle:'尚未检查更新。',checking:'正在检查更新…',available:'发现新版本，是否更新？',downloading:'正在准备更新…',current:'当前已是最新版本。',ready:'更新已准备好。',deferred:'当前有任务运行，请稍后更新。',failed:'暂时无法更新，请重试。',development:'开发版暂不支持更新。',installing:'正在打开更新程序…'};
    $('updateMessage').textContent=labels[state.status]||'暂时无法检查更新。';
    $('updateProgress').hidden=state.status!=='downloading'; $('updateProgress').value=state.progress||0;
    $('updateInstall').hidden=!['available','ready'].includes(state.status);
    $('updateRetry').disabled=['checking','downloading','installing'].includes(state.status);
    if(['checking','downloading'].includes(state.status))updateTimer=setTimeout(pollUpdate,1500);
    else if(state.status==='ready' && updateRequested){updateRequested=false;try{await api('update/install',{method:'POST'});await pollUpdate();}catch(error){toast(error.message);}}
    if(['available','ready'].includes(state.status)){$('updateButton').textContent='↑ 发现新版本';$('updateButton').classList.add('update-available');}
    else if(state.status==='current'){$('updateButton').textContent='↻ 检查更新';$('updateButton').classList.remove('update-available');}
  }catch(error){$('updateMessage').textContent='暂时无法检查更新，请重试。';}
}
async function checkForUpdates(){try{clearTimeout(updateTimer);await api('update/check',{method:'POST'});await pollUpdate();}catch(error){$('updateMessage').textContent='暂时无法检查更新，请重试。';}}
$('updateButton').onclick=()=>{if(!$('updateDialog').open)$('updateDialog').showModal();pollUpdate();};
$('closeUpdate').onclick=()=>$('updateDialog').close();
$('updateRetry').onclick=checkForUpdates;
$('updateInstall').onclick=async()=>{try{const state=await api('update');if(state.status==='available'){updateRequested=true;await api('update/download',{method:'POST'});}else if(state.status==='ready'){await api('update/install',{method:'POST'});}await pollUpdate();}catch(error){updateRequested=false;toast(error.message);}};

function syncTimelineFormats(){document.querySelectorAll('[data-timeline-format]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.timelineFormat===$('timelineExportFormat').value)));}
document.querySelectorAll('[data-timeline-format]').forEach(button=>button.addEventListener('click',()=>{$('timelineExportFormat').value=button.dataset.timelineFormat;syncTimelineFormats();}));
