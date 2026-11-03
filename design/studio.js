const $ = id => document.getElementById(id);
const source = new Audio(), accompaniment = new Audio();
source.preload = accompaniment.preload = 'metadata';
let projects = [], current = 0, selected = 0, token = '', busy = false;
let activeVoice = 'source', activeJob = null, pollTimer = null, modelReady = false;
let importKind = 'source', toastTimer, clipEnd = null, waveRevision = 0, playbackFrame;
const peaks = new Map();
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
  busy = true; $('edit').inert = true; $('newProject').inert = true;
  $('saveState').textContent = '正在处理…';
  try { await action(); $('saveState').textContent = '已保存到本机'; }
  catch (error) { $('saveState').textContent = '未保存'; toast(error.message); }
  finally { busy = false; $('edit').inert = false; $('newProject').inert = false; }
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
  for (const [i, line] of (p?.lines || []).entries()) {
    const row = document.createElement('article'); row.className = 'lyric-row' + (i === selected ? ' selected' : '');
    row.id = 'line-' + i;
    const copy = document.createElement('div'); copy.className = 'lyric-copy';
    const words = document.createElement('div'); words.className = 'words';
    const chars = Array.from(line.text.replaceAll('<SP>', ''));
    chars.forEach((char, j) => {
      const b = document.createElement('button'); b.className = 'word'; b.textContent = char;
      b.title = '按字数估算定位；尚未逐字对齐';
      b.onclick = () => {
        selected = i;
        seek(line.start + j / Math.max(chars.length, 1) * (line.end - line.start));
        document.querySelectorAll('.lyric-row').forEach((r, k) => r.classList.toggle('selected', k === i));
      };
      words.append(b);
    });
    const original = document.createElement('div'); original.className = 'source';
    original.textContent = changed(line) ? '原词：' + line.original : '原词 · ' + fmt(line.start) + ' — ' + fmt(line.end);
    const state = document.createElement('div'); state.className = 'state'; state.title = line.render_warning || '';
    state.textContent = line.render_state === 'ready' ? (line.render_warning ? '已生成 · 建议试听校对' : '已生成') : line.render_state === 'stale' ? '修改后待重新生成' : changed(line) ? '待生成' : '';
    const edit = document.createElement('button'); edit.className = 'edit-button'; edit.textContent = '♧ 改词'; edit.onclick = () => openEdit(i);
    copy.append(words, original, state); row.append(copy, edit); $('lyricsList').append(row);
  }
  const pending = p?.lines.filter(x => changed(x) && x.render_state !== 'ready').length || 0;
  $('batch').textContent = '生成 ' + pending + ' 句'; $('batch').disabled = !pending || !!activeJob || !modelReady || !!p?.source_issue;
  $('batch').title = p?.source_issue || '';
  $('versionRendered').disabled = !p?.rendered_available;
  $('exportVoice').disabled = $('exportMix').disabled = !p?.rendered_available || !p?.render_complete;
  if (!p?.assets.accompaniment) $('exportMix').disabled = true;
  ['importSource', 'importAccompaniment', 'importLyrics', 'exportLyrics'].forEach(id => $(id).disabled = !p);
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
function updateClock() { $('clock').textContent = fmt(+$('seek').value) + ' / ' + fmt(duration()); }
function drawWave(element, values, label = '尚未导入音频') {
  element.replaceChildren(); element.classList.toggle('empty', !values?.length);
  if (!values?.length) { element.textContent = label; return; }
  for (const amplitude of values) {
    const bar = document.createElement('i'); bar.style.height = Math.max(1, amplitude * 100) + '%'; element.append(bar);
  }
}
async function decodePeaks(asset, revision) {
  if (!asset) return null;
  if (peaks.has(asset.id)) return peaks.get(asset.id);
  const response = await fetch('/api/assets/' + asset.id);
  if (!response.ok || +(response.headers.get('content-length') || 0) > 80 * 1024 * 1024) return null;
  const buffer = await response.arrayBuffer();
  if (revision !== waveRevision) return null;
  const context = new AudioContext();
  try {
    const decoded = await context.decodeAudioData(buffer), channel = decoded.getChannelData(0);
    const bins = 1200, block = Math.ceil(channel.length / bins), result = [];
    for (let i = 0; i < bins; i++) {
      let max = 0;
      for (let j = i * block; j < Math.min((i + 1) * block, channel.length); j++) max = Math.max(max, Math.abs(channel[j]));
      result.push(max);
    }
    peaks.set(asset.id, result); return result;
  } finally { await context.close(); }
}
function reducePeaks(values, bins = 190) {
  if (!values?.length) return null;
  const block = Math.max(1, Math.ceil(values.length / bins)), result = [];
  for (let i = 0; i < values.length; i += block) result.push(Math.max(...values.slice(i, i + block)));
  return result;
}
function drawEditorWave() {
  const values = peaks.get(project()?.assets.source?.id);
  const start = +$('start').value, end = +$('end').value;
  const slice = values?.slice(Math.floor(start / duration() * values.length), Math.ceil(end / duration() * values.length));
  drawWave(document.querySelector('[data-wave="3"]'), reducePeaks(slice), '导入音频后显示当前句波形');
  drawWave(document.querySelector('[data-wave="4"]'), reducePeaks(values), '整首概览');
}
async function loadAudio(position = 0, resume = false) {
  pause(); const p = project(), revision = ++waveRevision;
  if (activeVoice === 'rendered' && !p?.rendered_available) activeVoice = 'source';
  document.querySelectorAll('[data-version]').forEach(b => b.classList.toggle('chosen', b.dataset.version === activeVoice));
  for (const [kind, audio, selector] of [['source', source, '1'], ['accompaniment', accompaniment, '2']]) {
    const asset = kind === 'source' && activeVoice === 'rendered' ? {id: p.composite.rendered, name: '改词演唱', duration: p.duration} : p?.assets[kind];
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
        drawWave(el, reducePeaks(values), '波形暂不可用，仍可尝试试听');
        if ($('edit').open) drawEditorWave();
      }
    }).catch(() => { if (revision === waveRevision) drawWave(el, null, '波形暂不可用'); });
  }
  $('voiceLabel').textContent = activeVoice === 'rendered' ? '改词演唱（未生成部分保留原唱）' : p?.assets.source?.name || '未导入原唱';
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
  $('restore').checked = false;
  $('regenerate').disabled = !!activeJob || !modelReady || !!project()?.source_issue;
  $('regenerate').title = project()?.source_issue || '';
  document.querySelectorAll('[data-mode]').forEach(b => b.classList.toggle('chosen', b.dataset.mode === line.mode));
  validate(); $('edit').showModal();
}
function validate() {
  const p = project(), a = +$('start').value, b = +$('end').value;
  const chars = Array.from($('newLyrics').value.replaceAll('<SP>', '').replace(/\s/g, ''));
  const n = Array.from(p.lines[selected].original.replaceAll('<SP>', '').replace(/\s/g, '')).length;
  const previous = p.lines[selected - 1], next = p.lines[selected + 1];
  const ok = $('start').value !== '' && $('end').value !== '' && Number.isFinite(a) && Number.isFinite(b)
    && a >= 0 && a < b && b <= duration() && (!previous || a >= previous.end) && (!next || b <= next.start) && chars.length > 0;
  $('save').disabled = !ok || busy;
  $('slotCount').textContent = chars.length + ' / ' + n;
  $('slots').replaceChildren();
  chars.forEach((char, i) => { const slot = document.createElement('div'), label = document.createElement('small'); label.textContent = '字 ' + (i + 1); slot.append(label, document.createTextNode(char)); $('slots').append(slot); });
  $('slotHint').textContent = chars.length === n ? '字数一致；实际旋律槽位与发音适配仍需模型分析。' : '字数发生变化，实际分配需结合音符和发音。';
  $('validation').textContent = ok ? (p.source_issue ? '当前为无演唱测试音，只能编辑与试听，不能生成。' : '保存后写入本机工程。') : '歌词不能为空，边界须位于音频内且不能与相邻句重叠。';
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
    const value = structuredClone(project()), line = value.lines[selected];
    Object.assign(line, {text: $('newLyrics').value, start: +$('start').value, end: +$('end').value,
      mode: document.querySelector('[data-mode].chosen').dataset.mode, restore: $('restore').checked});
    const saved = await api('projects/' + value.id, {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(value)});
    replaceProject(saved); loadAudio(); $('edit').close(); toast('歌词与边界已保存到本机。');
  });
};
$('closeEdit').onclick = () => { if (!busy) { pause(); $('edit').close(); } };
$('edit').addEventListener('cancel', event => { if (busy) event.preventDefault(); else pause(); });
$('edit').addEventListener('close', pause);
document.querySelectorAll('[data-mode]').forEach(b => b.onclick = () => document.querySelectorAll('[data-mode]').forEach(x => x.classList.toggle('chosen', x === b)));
$('regenerate').onclick = () => {
  if (!validate()) return;
  mutate(async () => {
    const value = structuredClone(project()), line = value.lines[selected];
    Object.assign(line, {text: $('newLyrics').value, start: +$('start').value, end: +$('end').value,
      mode: document.querySelector('[data-mode].chosen').dataset.mode, restore: false});
    const saved = await api('projects/' + value.id, {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(value)});
    replaceProject(saved); $('edit').close(); pause();
    await startRender([line.id]);
  });
};
$('batch').onclick = () => mutate(() => startRender(project().lines.filter(x => changed(x) && x.render_state !== 'ready').map(x => x.id)));

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
$('timeline').onclick = () => {
  if (!project()?.lines.length) { toast('请先导入 SRT 字幕或 LRC 歌词。'); return; }
  const i = project().lines.findIndex(x => +$('seek').value >= x.start && +$('seek').value < x.end);
  openEdit(i < 0 ? 0 : i);
};
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
    if (importKind === 'source' && p.lines.some(x => x.end > seconds + .001)) throw new Error('新音频比已有歌词短，请创建新工程导入，避免损坏时间轴。');
    const updated = await api('projects/' + p.id + '/assets/' + importKind, {method: 'POST', headers: {
      'Content-Type': 'application/octet-stream', 'X-File-Name': encodeURIComponent(file.name), 'X-Project-Revision': String(p.revision), 'X-Audio-Duration': String(seconds)}, body: file});
    replaceProject(updated); loadAudio(); toast('音频已复制到本机工程，可以试听。');
  });
};
$('importLyrics').onclick = () => { if (busy || !project()) return; if (!project().assets.source) { toast('请先导入原唱音频，以确定时间轴长度。'); return; } $('lyricsFile').value = ''; $('lyricsFile').click(); };
$('lyricsFile').onchange = () => {
  const file = $('lyricsFile').files[0]; if (!file) return;
  if (project().lines.length && !confirm('导入字幕或歌词会替换本工程的全部歌词编辑，是否继续？')) return;
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
    if (runtime.active_job) { activeJob = runtime.active_job; pollJob(); }

  } catch (error) {
    render(); document.querySelector('.badge').textContent = '服务未连接'; toast('请运行 python -m backend.app，再打开本机服务地址。');
  }
}
const STAGES = {starting:'启动模型进程', alignment:'原词逐字对齐', alignment_warning:'校验短音节边界', pitch:'提取旋律音高', notes:'提取音符与槽位', loading_singer:'加载演唱模型', generating:'生成演唱', composing:'拼接与混音', complete:'生成完成'};
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
    $('jobStage').textContent = STAGES[job.stage] || job.stage;
    $('jobDetail').textContent = (job.position ? ' · 第 ' + job.position + ' / ' + job.total + ' 句' : '') + ' · 模型进程独立运行';
    if (['succeeded','failed','cancelled','interrupted','stale'].includes(job.status)) {
      activeJob = null; panel.hidden = true;
      const p = project();
      if (p && p.id === job.project_id) {
        const latest = await api('projects/' + p.id); replaceProject(latest);
        if (job.status === 'succeeded' && latest.rendered_available) activeVoice = 'rendered';
        loadAudio();
      }
      render();
      toast(job.status === 'succeeded' ? '真实演唱已生成，可切换原唱/改词试听。' + (job.warnings.length ? job.warnings.join(' ') : '') : job.error || '生成已取消。');
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
  const position = source.currentTime || +$('seek').value, resume = !source.paused;
  activeVoice = mode; loadAudio(position, resume);
});
for (const [id, kind] of [['exportVoice','rendered'],['exportMix','mixed']]) {
  $(id).onclick = () => { const a = document.createElement('a'); a.href='/api/projects/'+project().id+'/exports/'+kind; a.download=kind+'.wav'; a.click(); };
}
$('runtimeInfo').onclick = async () => {
  try {
    const state = await api('runtime'); const root = $('runtimeDetails'); root.replaceChildren();
    const summary = document.createElement('p'); summary.textContent=state.ready?'模型文件已就绪，按需加载。':'模型环境不完整。';root.append(summary);
    for(const [name,ready] of Object.entries(state.checks)) {const p=document.createElement('p');p.textContent=(ready?'✓ ':'✕ ')+name;root.append(p);}
    for(const text of [state.limits, '权重目录：'+state.model_root,'推理代码：'+state.source_root]) {const p=document.createElement('p');p.textContent=text;root.append(p);}
    $('runtimeDialog').showModal();
  } catch(error) {toast(error.message);}
};
$('closeRuntime').onclick=()=>$('runtimeDialog').close();
init();
