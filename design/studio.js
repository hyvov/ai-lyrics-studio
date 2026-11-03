const $ = id => document.getElementById(id);
const source = new Audio(), accompaniment = new Audio();
source.preload = accompaniment.preload = 'metadata';
let projects = [], current = 0, selected = 0, token = '', busy = false;
let activeVoice = 'source', activeJob = null, pollTimer = null, modelReady = false, svcReady = false;
let timelineSelected = 0, timelineDirty = false;
const lyricUnits = text => text.replaceAll('<SP>', ' ').match(/[\u4e00-\u9fff]|[A-Za-z]+(?:'[A-Za-z]+)*/g) || [];
let importKind = 'source', toastTimer, clipEnd = null, waveRevision = 0, playbackFrame;
let svcTargetKind = 'source';
const peaks = new Map();
function cachePeaks(id,values) {
  peaks.delete(id);peaks.set(id,values);
  while(peaks.size>4)peaks.delete(peaks.keys().next().value);
  return values;
}
const project = () => projects[current];
const duration = () => project()?.duration || 30;
const changed = x => x.text !== x.original;
const fmt = n => String(Math.floor(n / 60)).padStart(2, '0') + ':' + String(Math.floor(n % 60)).padStart(2, '0');
function toast(text) {
  $('toast').textContent = text;
  $('toast').classList.add('visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $('toast').classList.remove('visible'), 5500);
}
async function api(path, options = {}) {
  const response = await fetch('/api/' + path, {
    ...options,
    headers: {'X-Studio-Token': token, ...options.headers},
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.error || '操作失败（' + response.status + '），请重试。');
  }
  return response.json();
}
async function mutate(action) {
  if (busy) { toast('正在保存或导入，请稍候。'); return; }
  busy = true; $('edit').inert = true; $('newProject').inert = true; $('timelineEditor').inert = true;
  $('saveState').textContent = '正在处理…';
  try { await action(); $('saveState').textContent = '已保存到本机'; }
  catch (error) { $('saveState').textContent = '未保存'; toast(error.message); }
  finally {
    busy = false; $('edit').inert = false; $('newProject').inert = false; $('timelineEditor').inert = false;
    if($('timelineEditor').open)refreshWordWave();
    if($('edit').open)drawEditorWave();
  }
}
function replaceProject(p) { projects[current] = p; render(); }
function render() {
  const p = project();
  $('songName').textContent = document.querySelector('.project-name').textContent = p?.name || '开始一个新工程';
  $('projectCount').textContent = projects.length;
  $('projects').replaceChildren(); $('projectSelect').replaceChildren();
  projects.forEach((item, i) => {
    const button = document.createElement('button');
    button.className = 'project' + (i === current ? ' active' : '');
    const cover = document.createElement('span'); cover.className = 'cover'; cover.textContent = '♫';
    const title = document.createElement('div'); title.textContent = item.name;
    const sub = document.createElement('small'); sub.textContent = item.source_issue ? '仅功能测试 · 无演唱，不可生成' : item.lines.length + ' 句 · ' + item.lines.filter(x => x.render_state === 'ready').length + ' 已生成';
    title.append(sub); button.append(cover, title); button.onclick = () => switchProject(i);
    $('projects').append(button);
    const option = document.createElement('option'); option.value = i; option.textContent = item.name; option.selected = i === current;
    $('projectSelect').append(option);
  });
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
      if (timing) { b.dataset.start = timing.start; b.dataset.end = timing.end; }
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
    state.textContent = line.needs_review ? '自动识词 · 请校对原词和边界' : line.render_state === 'ready' ? (line.render_warning ? '已生成 · 建议试听校对' : '已生成') : line.render_state === 'stale' ? '修改后待重新生成' : changed(line) ? '待生成' : '';
    const edit = document.createElement('button'); edit.className = 'edit-button'; edit.textContent = '♧ 改词'; edit.onclick = () => openEdit(i);
    copy.append(words, original, state); row.append(copy, edit); $('lyricsList').append(row);
  }
  const pending = p?.lines.filter(x => changed(x) && x.render_state !== 'ready').length || 0;
  const unaligned = p?.lines.filter(x => x.alignment_state !== 'ready').length || 0;
  $('batchAlign').textContent = '提取未对齐歌词 ' + unaligned + ' 句';
  $('batchAlign').disabled = !unaligned || !!activeJob || !modelReady || !p?.assets.source || !!p?.source_issue;
  $('batchAlign').title = p?.source_issue || '批量提取未对齐的逐字时间；已有时间不会被覆盖';
  $('transcribe').disabled = !p?.assets.source || !!activeJob || !!p?.source_issue;
  $('lyricHistory').disabled = !p || !!activeJob;
  $('batch').textContent = '生成 ' + pending + ' 句'; $('batch').disabled = !pending || !!activeJob || !modelReady || !!p?.source_issue;
  $('batch').title = p?.source_issue || '';
  $('versionRendered').disabled = !p?.rendered_available;
  $('versionSvc').disabled = $('exportSvc').disabled = !p?.svc_available;
  $('exportSvcMix').disabled = !p?.svc_mixed_available;
  $('startSvc').disabled = !p?.assets.source || !!activeJob || !!p?.source_issue;
  $('recompose').disabled = !p?.assets.source || !!activeJob || !p?.lines.some(x=>x.render_state==='ready');
  $('recompose').textContent = p?.assets.accompaniment ? '重新混音' : '重新合成';
  $('recompose').title = '复用已生成的唱句重新合成人声和伴奏，无需重新运行演唱模型';
  $('exportVoice').disabled = $('exportMix').disabled = !p?.rendered_available || !p?.render_complete;
  if (!p?.assets.accompaniment) $('exportMix').disabled = true;
  ['importSong', 'importSource', 'importAccompaniment', 'importLyrics', 'exportLyrics'].forEach(id => $(id).disabled = !p);
  $('seek').max = duration(); updateClock();
}
function pause() {
  source.pause(); accompaniment.pause(); clipEnd = null; cancelAnimationFrame(playbackFrame); $('play').textContent = '▶';
  $('previewSegment').textContent = '▶ 试听当前句';
}
function seek(value) {
  const position = Math.max(0, Math.min(value, duration()));
  for (const audio of [source, accompaniment]) {
    if (audio.readyState >= 1) audio.currentTime = Math.min(position, audio.duration || position);
  }
  $('seek').value = position; updateClock();
}
async function play(end = null) {
  if (!project()?.assets.source) { toast('请先导入原唱或干声音频。'); return; }
  clipEnd = end;
  if (source.currentTime >= duration() - 0.05) seek(0);
  try {
    if (accompaniment.readyState >= 1 && source.currentTime < accompaniment.duration) {
      accompaniment.currentTime = source.currentTime;
      await accompaniment.play();
    }
    await source.play(); $('play').textContent = 'Ⅱ';
    cancelAnimationFrame(playbackFrame);
    function watchClip() {
      if (source.paused) return;
      if (clipEnd !== null && source.currentTime >= clipEnd) { pause(); return; }
      playbackFrame = requestAnimationFrame(watchClip);
    }
    playbackFrame = requestAnimationFrame(watchClip);
  } catch (_) { pause(); toast('无法播放音频，请检查格式或重新导入。'); }
}
source.addEventListener('timeupdate', () => {
  $('seek').value = source.currentTime; updateClock();
  if (clipEnd !== null && source.currentTime >= clipEnd) pause();
  if (!source.paused && !accompaniment.paused && Math.abs(source.currentTime - accompaniment.currentTime) > 0.15) {
    accompaniment.currentTime = Math.min(source.currentTime, accompaniment.duration);
  }
});
source.addEventListener('ended', pause);
source.addEventListener('error', () => { pause(); toast('原唱音频无法解码，请换用 WAV 或 MP3。'); });
function updateClock() {
  const time = +$('seek').value;
  $('clock').textContent = fmt(time) + ' / ' + fmt(duration());
  document.querySelectorAll('.word[data-start]').forEach(b => b.classList.toggle('playing-word', time >= +b.dataset.start && time < +b.dataset.end));
  phraseWaveEditor?.setPlayhead(time); wordWaveEditor?.setPlayhead(time);
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
let phraseWaveEditor = null, wordWaveEditor = null;
function drawEditorWave() {
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
  const host=document.createElement('div');$('timelineHint').after(host);
  wordWaveEditor=new WaveEditor(host,{draw:drawWave,onSeek:t=>{pause();seek(t);},onChange:regions=>{
    regions.forEach((r,i)=>{const row=$('wordTimeline').children[i];row.querySelector('.word-start').value=r.start.toFixed(3);row.querySelector('.word-end').value=r.end.toFixed(3);});
    timelineDirty=true;
  }});
  host.querySelector('small').textContent += ' · 推动边界会联动相邻字';
}
async function loadAudio(position = 0, resume = false) {
  pause(); const p = project(), revision = ++waveRevision;
  if (activeVoice === 'rendered' && !p?.rendered_available) activeVoice = 'source';
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
        if ($('timelineEditor').open) refreshWordWave();
      }
    }).catch(() => { if (revision === waveRevision) drawWave(el, null, '波形暂不可用'); });
  }
  $('voiceLabel').textContent = activeVoice === 'rendered' ? '改词演唱（未生成部分保留原唱）' : activeVoice === 'svc' ? '音色转换' : p?.assets.source?.name || '未导入原唱';
  document.querySelector('.track:nth-child(2) small').textContent = p?.assets.accompaniment?.name || '未导入伴奏';
  $('seek').value = position; updateClock();
}
function switchProject(i) {
  if (busy) { toast('当前操作尚未完成。'); return; }
  current = i; selected = 0; activeVoice = 'source'; render(); loadAudio();
}
function openEdit(i) {
  if (busy) return;
  const line = project()?.lines[i]; if (!line) return;
  selected = i; render(); pause();
  if (activeVoice !== 'source') { activeVoice = 'source'; loadAudio(); }
  $('editMeta').textContent = '第 ' + (i + 1) + ' 句 · ' + fmt(line.start) + ' — ' + fmt(line.end);
  $('sourceText').textContent = $('original').textContent = line.original;
  $('newLyrics').value = line.text;
  for (const id of ['start', 'end', 'startRange', 'endRange']) { $(id).max = duration(); $(id).value = id.startsWith('start') ? line.start : line.end; }
  $('restore').checked = !!line.restore;
  $('regenerate').disabled = !!activeJob || !modelReady || !!project()?.source_issue;
  $('regenerate').title = project()?.source_issue || '';
  document.querySelectorAll('[data-mode]').forEach(b => b.classList.toggle('chosen', b.dataset.mode === line.mode));
  syncRestore();
  ensurePhraseWave(); validate(); $('edit').showModal(); drawEditorWave();
}
function validate() {
  const p = project(), a = +$('start').value, b = +$('end').value;
  const chars = lyricUnits($('newLyrics').value);
  const n = lyricUnits(p.lines[selected].original).length;
  const previous = p.lines[selected - 1], next = p.lines[selected + 1];
  const ok = $('start').value !== '' && $('end').value !== '' && Number.isFinite(a) && Number.isFinite(b)
    && a >= 0 && b-a >= .12-1e-9 && b <= duration() && (!previous || a >= previous.start+.12-1e-9)
    && (!next || b <= next.end-.12+1e-9) && chars.length > 0;
  $('save').disabled = !ok || busy;
  $('slotCount').textContent = chars.length + ' / ' + n;
  $('slots').replaceChildren();
  chars.forEach((char, i) => { const slot = document.createElement('div'), label = document.createElement('small'); label.textContent = '字 ' + (i + 1); slot.append(label, document.createTextNode(char)); $('slots').append(slot); });
  $('slotHint').textContent = chars.length === n ? '字数一致；实际旋律槽位与发音适配仍需模型分析。' : '字数发生变化，实际分配需结合音符和发音。';
  const affected = [previous && a < previous.end ? '上一句终点' : '', next && b > next.start ? '下一句起点' : ''].filter(Boolean);
  $('validation').textContent = ok ? (affected.length ? '保存时将同步调整'+affected.join('和')+'；受影响句子需要重新对齐和生成。' : (p.source_issue ? '当前为无演唱测试音，只能编辑与试听，不能生成。' : '保存后写入本机工程。')) : '歌词不能为空；当前句和相邻句均须至少保留 120 毫秒，边界不能超出音频。';
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
    replaceProject(saved); loadAudio(); $('edit').close(); toast('歌词与边界已保存到本机。');
  });
};
function savePhrase() {
  const p=project(), line=p.lines[selected];
  return api('projects/'+p.id+'/segments/'+line.id+'/phrase', {method:'PUT',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({revision:p.revision,text:$('newLyrics').value,start:+$('start').value,end:+$('end').value,
      mode:document.querySelector('[data-mode].chosen').dataset.mode,restore:$('restore').checked})});
}
$('closeEdit').onclick = () => { if (!busy) { pause(); $('edit').close(); } };
$('edit').addEventListener('cancel', event => { if (busy) event.preventDefault(); else pause(); });
$('edit').addEventListener('close', pause);
function syncRestore() {
  const score=document.querySelector('[data-mode].chosen')?.dataset.mode==='score';
  $('restore').disabled=!score||!svcReady||!!activeJob;
  $('restore').title=!score?'仅 MIDI 模式可使用':!svcReady?'本地 SVC 组件未就绪':'';
}
document.querySelectorAll('[data-mode]').forEach(b => b.onclick = () => {
  document.querySelectorAll('[data-mode]').forEach(x => x.classList.toggle('chosen', x === b));
  if(b.dataset.mode!=='score')$('restore').checked=false;
  syncRestore();validate();
});
$('regenerate').onclick = () => {
  if (!validate()) return;
  mutate(async () => {
    const line = project().lines[selected];
    const saved = await savePhrase();
    replaceProject(saved); $('edit').close(); pause();
    await startRender([line.id]);
  });
};
$('batch').onclick = () => mutate(() => startRender(project().lines.filter(x => changed(x) && x.render_state !== 'ready').map(x => x.id)));
$('recompose').onclick=()=>mutate(()=>startCompose());
async function startCompose() {
  const p=project();
  activeJob=await api('projects/'+p.id+'/compose',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:p.revision})});
  render();pollJob();
}
$('batchAlign').onclick = () => {
  const p=project(), lines=p.lines.filter(x=>x.alignment_state!=='ready').slice(0,100);
  if(!lines.length)return;
  const invalid=lines.find(x=>x.end-x.start<.12-1e-9 || x.end-x.start>20);
  if(invalid){toast('第 '+(p.lines.indexOf(invalid)+1)+' 句须为 0.12–20 秒，请先调整边界后再提取。');return;}
  if(lines.some(x=>x.render_state==='ready')&&!confirm('提取逐字时间会使涉及句子的已生成结果过期，需要重新生成。是否继续？'))return;
  mutate(async()=>{
    activeJob=await api('projects/'+p.id+'/alignments',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({revision:p.revision,segment_ids:lines.map(x=>x.id)})});
    render();pollJob();
    if(p.lines.filter(x=>x.alignment_state!=='ready').length>100)toast('本次提取前 100 句；完成后可再次点击处理剩余句子。');
  });
};
$('transcribe').onclick = () => {
  const p=project();if(!p?.assets.source)return;
  if(p.lines.length&&!confirm('自动识词将替换当前歌词；原有内容会保存在历史歌词中。是否继续？'))return;
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
      restore.onclick=()=>{
        if(!confirm('恢复此版本将替换当前歌词；当前内容也会保存到历史中。是否继续？'))return;
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

$('theme').onclick = () => { const dark = document.body.classList.toggle('dark'); $('theme').textContent = dark ? '☀ 白天' : '☾ 夜间'; };
document.querySelectorAll('[data-info]').forEach(b => b.onclick = () => toast(b.dataset.info));
document.querySelectorAll('.mute').forEach((b, i) => b.onclick = () => {
  const audio = i ? accompaniment : source; audio.muted = !audio.muted; b.classList.toggle('off', audio.muted); b.setAttribute('aria-pressed', audio.muted);
});
$('play').onclick = () => source.paused ? play() : pause();
$('previewSegment').onclick = () => {
  if (!source.paused) { pause(); return; }
  if (!validate()) return; seek(+$('start').value); play(+$('end').value);
};
$('seek').oninput = () => { clipEnd = null; seek(+$('seek').value); };
$('timeline').onclick = openTimeline;
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
$('importSong').onclick = () => {
  if(activeJob){toast('当前模型任务尚未完成。');return;}
  if(project()?.assets.source && !confirm('分离成功后会替换当前人声与伴奏，相关歌词对齐和生成结果需要重新处理。是否继续？'))return;
  chooseAudio('song');
};
$('startSvc').onclick = () => {
  if(activeJob){toast('当前模型任务尚未完成。');return;}
  svcTargetKind=activeVoice==='rendered'?'rendered':'source';
  toast('请选择 1–30 秒清晰参考人声，将转换当前'+(svcTargetKind==='rendered'?'改词演唱':'原唱人声')+'。');
  chooseAudio('reference_pending');
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
$('lyricsFile').onchange = () => {
  const file = $('lyricsFile').files[0]; if (!file) return;
  if (project().lines.length && !confirm('导入字幕或歌词会替换当前歌词；原有内容会保存在历史歌词中。是否继续？')) return;
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
    projects = await api('projects'); render(); loadAudio(); $('saveState').textContent = '本机服务已连接';
    document.querySelector('.badge').textContent = modelReady ? '共用模型已就绪' : '模型环境待配置';
    const runtime = await api('runtime');
    svcReady=!!runtime.svc_ready;
    if (runtime.active_job) { activeJob = runtime.active_job; pollJob(); }

  } catch (error) {
    render(); document.querySelector('.badge').textContent = '服务未连接'; toast('请运行 python -m backend.app，再打开本机服务地址。');
  }
}
function openTimeline() {
  if (!project()?.lines.length) { toast('请先导入 SRT / LRC。'); return; }
  if (busy) return;
  timelineSelected = Math.min(selected, project().lines.length-1); timelineDirty = false;
  const position = +$('seek').value;
  activeVoice = 'source'; loadAudio(position);
  renderTimeline(); $('timelineEditor').showModal();
}
function renderTimeline() {
  const p = project(), line = p?.lines[timelineSelected];
  if (!line) return;
  const list = $('timelineLines'); list.replaceChildren();
  p.lines.forEach((x,i) => {
    const button = document.createElement('button');
    button.className = i === timelineSelected ? 'selected' : '';
    const text = document.createElement('span'); text.textContent = (i+1)+'. '+x.text;
    const time = document.createElement('small'); time.textContent=x.start.toFixed(3)+' — '+x.end.toFixed(3)+' s';
    button.append(text,time);button.onclick=()=>{
      if (busy || timelineDirty && !confirm('当前有未保存的修改，放弃后切换句子？')) return;
      timelineSelected=i;timelineDirty=false;pause();renderTimeline();
    };list.append(button);
  });
  $('timelineLabel').textContent='第 '+(timelineSelected+1)+' 句 · '+line.start.toFixed(3)+' — '+line.end.toFixed(3)+' s';
  $('timelineOriginal').value=line.original;
  const timings=line.unit_timings||[];
  $('timelineHint').textContent=timings.length ? '已保存逐字时间 · '+({manual:'手动校正',HubertFA:'歌声对齐',Qwen:'自动对齐'}[line.alignment_engine]||'已对齐')+' · 手动保存会使旧生成过期。' : '还没有真实逐字时间，请提取；主界面暂按单位数估算定位。';
  if(line.needs_review) $('timelineHint').textContent='自动识词草稿，请校对原词和整句边界，确认后点击保存原词校正。'+$('timelineHint').textContent;
  $('extractWords').disabled=!!activeJob||!modelReady||!!p.source_issue;
  $('extractWords').title=p.source_issue||'';
  $('cancelAlignment').hidden=!activeJob || activeJob.kind!=='align';
  $('wordTimeline').replaceChildren();
  const targets=lyricUnits(line.text);
  timings.forEach((timing,i)=>{
    const row=document.createElement('div');row.className='word-time-row';row.dataset.word=timing.text;
    const button=document.createElement('button');button.textContent=timing.text+(targets.length===timings.length && targets[i]!==timing.text?' → '+targets[i]:'');
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
  ensureWordWave(); refreshWordWave();
}
$('timelineOriginal').oninput=()=>{timelineDirty=true;};
$('closeTimeline').onclick=()=>{
  if(busy||timelineDirty&&!confirm('放弃尚未保存的时间轴修改？'))return;
  timelineDirty=false;pause();$('timelineEditor').close();
};
$('timelineEditor').addEventListener('cancel',event=>{
  if(busy||timelineDirty&&!confirm('放弃尚未保存的时间轴修改？'))event.preventDefault();
  else{timelineDirty=false;pause();}
});
$('saveOriginal').onclick=()=>{
  const line=project().lines[timelineSelected], text=$('timelineOriginal').value.trim();
  if(text===line.original && !line.needs_review){toast('原词没有变化；如调整了逐字时间，请点击保存逐字时间。');return;}
  const rows=Array.from($('wordTimeline').children), saved=line.unit_timings||[];
  const timesChanged=rows.length===saved.length && rows.some((row,i)=>
    row.querySelector('.word-start').value!==saved[i].start.toFixed(3) || row.querySelector('.word-end').value!==saved[i].end.toFixed(3));
  if(timesChanged&&!confirm(text===line.original ? '确认原词会放弃尚未保存的逐字时间。是否继续？' : '保存原词会使当前句的旧对齐失效，尚未保存的逐字时间也会丢失。是否继续？'))return;
  mutate(async()=>{
  const p=project(),line=p.lines[timelineSelected];
  const updated=await api('projects/'+p.id+'/segments/'+line.id+'/original',{
    method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:p.revision,text:$('timelineOriginal').value})});
  timelineDirty=false;replaceProject(updated);renderTimeline();toast('原词校正已保存；已修改的新歌词保留，原词变化后需重新对齐。');
  });
};
$('extractWords').onclick=()=>{
  const p=project(),line=p.lines[timelineSelected];
  if($('timelineOriginal').value.trim()!==line.original){toast('请先保存原词校正，再提取时间。');return;}
  if(timelineDirty&&!confirm('重新提取会替换尚未保存的逐字时间，是否继续？'))return;
  if(line.render_state==='ready'&&!confirm('重新提取会使当前句的旧生成过期，是否继续？'))return;
  mutate(async()=>{
    activeJob=await api('projects/'+p.id+'/alignments',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:p.revision,segment_ids:[line.id]})});
    timelineDirty=false;renderTimeline();render();pollJob();
  });
};
$('cancelAlignment').onclick=()=>$('cancelJob').click();
$('saveWordTimes').onclick=()=>mutate(async()=>{
  const p=project(),line=p.lines[timelineSelected];
  if($('timelineOriginal').value.trim()!==line.original)throw new Error('请先保存原词校正。');
  const timings=Array.from($('wordTimeline').children).map(row=>({text:row.dataset.word,
    start:parseFloat(row.querySelector('.word-start').value),end:parseFloat(row.querySelector('.word-end').value)}));
  const updated=await api('projects/'+p.id+'/segments/'+line.id+'/timeline',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:p.revision,unit_timings:timings})});
  timelineDirty=false;replaceProject(updated);renderTimeline();toast('逐字时间已保存，下一次生成会复用这些边界。');
});
$('openPhraseEditor').onclick=()=>{
  if(timelineDirty&&!confirm('放弃尚未保存的修改并打开整句编辑？'))return;
  const index=timelineSelected;timelineDirty=false;$('timelineEditor').close();openEdit(index);
};
$('splitPhrase').onclick=()=>{
  if(timelineDirty&&!confirm('拆分将放弃尚未保存的修改，是否继续？'))return;
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
$('mergePhrase').onclick=()=>{
  if(timelineDirty&&!confirm('合并将放弃尚未保存的修改，是否继续？'))return;
  if(!confirm('将当前句与下一句合并，相关对齐和生成结果需要重新处理。是否继续？'))return;
  mutate(async()=>{
    const p=project(),line=p.lines[timelineSelected];
    const saved=await api('projects/'+p.id+'/segments/'+line.id+'/structure',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:p.revision,action:'merge'})});
    timelineDirty=false;replaceProject(saved);renderTimeline();toast('已合并；请检查边界并重新对齐。');
  });
};
$('takeHistory').onclick=async()=>{
  if(timelineDirty&&!confirm('切换历史版本会放弃尚未保存的时间轴修改，是否继续查看？'))return;
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
      detail.textContent=(take.created_at?new Date(take.created_at*1000).toLocaleString('zh-CN'):'历史版本')+' · '+(take.lyrics||'歌词信息未记录')+(take.compatible?'':' · 与当前歌词或边界不一致，仅可试听');
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

const STAGES = {starting:'启动模型进程', separation_decode:'读取歌曲', separation_vocal:'分离人声与伴奏', separation_dry:'人声去混响与回声', asr_decode:'读取人声', asr_load:'加载识词模型', asr_recognize:'识别歌词草稿', svc_restore:'恢复原句音色', svc_pitch:'分析参考与目标人声音高', svc_load:'加载音色转换模型', svc_generate:'转换人声音色', svc_mix:'合成变声混音', alignment_cached:'复用已保存的逐字时间', alignment:'原词逐字对齐', singing_alignment:'歌声逐字对齐', alignment_warning:'校验短音节边界', pitch_warning:'修复 MIDI 音高', pitch:'提取旋律音高', notes:'提取音符与槽位', loading_singer:'加载演唱模型', generating:'生成演唱', composing:'拼接与混音', complete:'任务完成'};
async function startRender(ids) {
  const p = project();
  activeJob = await api('projects/' + p.id + '/renders', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:p.revision,segment_ids:ids})});
  render(); pollJob();
}
async function pollJob() {
  clearTimeout(pollTimer);
  if (!activeJob) return;
  const panel = $('jobPanel'); panel.hidden = false;
  try {
    const job = await api('jobs/' + activeJob.id); activeJob = job;
    $('cancelJob').textContent = job.kind === 'align' ? '取消对齐' : job.kind === 'separate' ? '取消分离' : job.kind === 'transcribe' ? '取消识词' : job.kind === 'svc' ? '取消音色转换' : job.kind === 'compose' ? '取消混音' : '取消生成';
    $('jobStage').textContent = STAGES[job.stage] || job.stage;
    if ($('timelineEditor').open && !timelineDirty && !['succeeded','failed','cancelled','interrupted','stale'].includes(job.status)) $('timelineHint').textContent='正在'+(STAGES[job.stage]||job.stage)+'…';
    $('jobDetail').textContent = (job.position ? ' · 第 ' + job.position + ' / ' + job.total + ' 句' : '') + ' · 模型进程独立运行';
    if (['succeeded','failed','cancelled','interrupted','stale'].includes(job.status)) {
      activeJob = null; panel.hidden = true;
      const p = project();
      if (p && p.id === job.project_id) {
        const latest = await api('projects/' + p.id); replaceProject(latest);
        if (['render','compose'].includes(job.kind) && job.status === 'succeeded' && latest.rendered_available) activeVoice = 'rendered';
        if (job.kind === 'svc' && job.status === 'succeeded' && latest.svc_available) activeVoice = 'svc';
        if (job.kind !== 'align') loadAudio();
      }
      render();
      if ($('timelineEditor').open && !timelineDirty) renderTimeline();
      toast(job.status === 'succeeded' ? (job.kind === 'align' ? '逐字时间已提取并保存。' : job.kind === 'separate' ? '人声和伴奏已分离并导入工程。' : job.kind === 'transcribe' ? '已生成歌词草稿，请逐句校对原词和边界。' : job.kind === 'svc' ? '音色转换完成，可试听或导出。' : job.kind === 'compose' ? '已复用演唱片段完成重新混音。' : '真实演唱已生成，可切换原唱/改词试听。') + (job.warnings.length ? job.warnings.join(' ') : '') : job.error || '任务已取消。');
      return;
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
  if (mode === 'rendered' && !project()?.rendered_available) { toast('请先生成当前改词。'); return; }
  if (mode === 'svc' && !project()?.svc_available) { toast('请先完成音色转换。'); return; }
  const position = source.currentTime || +$('seek').value, resume = !source.paused;
  activeVoice = mode; loadAudio(position, resume);
});
for (const [id, kind] of [['exportVoice','rendered'],['exportMix','mixed'],['exportSvc','svc'],['exportSvcMix','svc_mixed']]) {
  $(id).onclick = () => { const a = document.createElement('a'); a.href='/api/projects/'+project().id+'/exports/'+kind; a.download=kind+'.wav'; a.click(); };
}
$('runtimeInfo').onclick = async () => {
  try {
    const state = await api('runtime'); const root = $('runtimeDetails'); root.replaceChildren();
    const summary = document.createElement('p'); summary.textContent=state.ready?'模型文件已就绪，按需加载。':'模型环境不完整。';root.append(summary);
    const separator = document.createElement('p'); separator.textContent = state.separation_ready ? '✓ 本地人声分离模型已就绪' : '人声分离模型未安装'; root.append(separator);
    const recognizer = document.createElement('p'); recognizer.textContent = state.transcription_ready ? '✓ 本地歌词识别模型已就绪' : '歌词识别模型未安装'; root.append(recognizer);
    const svc = document.createElement('p'); svc.textContent = state.svc_ready ? '✓ 本地音色转换模型已就绪' : '音色转换模型未安装'; root.append(svc);
    const aligner = document.createElement('p'); aligner.textContent = state.singing_aligner ? '✓ 歌声对齐模型 HubertFA 已就绪' : '歌声对齐组件未配置，将使用 Qwen 备用对齐'; root.append(aligner);
    for(const [name,ready] of Object.entries(state.checks)) {const p=document.createElement('p');p.textContent=(ready?'✓ ':'✕ ')+name;root.append(p);}
    for(const text of [state.limits, '权重目录：'+state.model_root,'推理代码：'+state.source_root]) {const p=document.createElement('p');p.textContent=text;root.append(p);}
    $('runtimeDialog').showModal();
  } catch(error) {toast(error.message);}
};
$('closeRuntime').onclick=()=>$('runtimeDialog').close();
init();
