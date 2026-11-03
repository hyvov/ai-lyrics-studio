const $ = id => document.getElementById(id);
const source = new Audio(), accompaniment = new Audio();
source.preload = accompaniment.preload = 'metadata';
let projects = [], current = 0, selected = 0, token = '', busy = false;
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
    const sub = document.createElement('small'); sub.textContent = item.lines.length + ' 句 · ' + (item.assets.source ? '已导入音频' : '待导入');
    title.append(sub); button.append(cover, title); button.onclick = () => switchProject(i);
    $('projects').append(button);
    const option = document.createElement('option'); option.value = i; option.textContent = item.name; option.selected = i === current;
    $('projectSelect').append(option);
  });
  $('counts').replaceChildren();
  if (p) [p.lines.length + ' 句歌词', p.lines.filter(changed).length + ' 处改词'].forEach(text => {
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
    const state = document.createElement('div'); state.className = 'state'; state.textContent = changed(line) ? '待生成' : '';
    const edit = document.createElement('button'); edit.className = 'edit-button'; edit.textContent = '♧ 改词'; edit.onclick = () => openEdit(i);
    copy.append(words, original, state); row.append(copy, edit); $('lyricsList').append(row);
  }
  const pending = p?.lines.filter(changed).length || 0;
  $('batch').textContent = '生成 ' + pending + ' 句'; $('batch').disabled = !pending;
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
async function loadAudio() {
  pause(); const p = project(), revision = ++waveRevision;
  for (const [kind, audio, selector] of [['source', source, '1'], ['accompaniment', accompaniment, '2']]) {
    const asset = p?.assets[kind];
    if (asset) audio.src = '/api/assets/' + asset.id;
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
  $('voiceLabel').textContent = p?.assets.source?.name || '未导入原唱';
  document.querySelector('.track:nth-child(2) small').textContent = p?.assets.accompaniment?.name || '未导入伴奏';
  $('seek').value = 0; updateClock();
}
function switchProject(i) {
  if (busy) { toast('当前操作尚未完成。'); return; }
  current = i; selected = 0; render(); loadAudio();
}
function openEdit(i) {
  if (busy) return;
  const line = project()?.lines[i]; if (!line) return;
  selected = i; render(); pause();
  $('editMeta').textContent = '第 ' + (i + 1) + ' 句 · ' + fmt(line.start) + ' — ' + fmt(line.end);
  $('sourceText').textContent = $('original').textContent = line.original;
  $('newLyrics').value = line.text;
  for (const id of ['start', 'end', 'startRange', 'endRange']) { $(id).max = duration(); $(id).value = id.startsWith('start') ? line.start : line.end; }
  $('restore').checked = line.restore;
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
  $('validation').textContent = ok ? '保存后写入本机工程。' : '歌词不能为空，边界须位于音频内且不能与相邻句重叠。';
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
    replaceProject(saved); pause(); $('edit').close(); toast('歌词与边界已保存到本机。');
  });
};
$('closeEdit').onclick = () => { if (!busy) { pause(); $('edit').close(); } };
$('edit').addEventListener('cancel', event => { if (busy) event.preventDefault(); else pause(); });
$('edit').addEventListener('close', pause);
document.querySelectorAll('[data-mode]').forEach(b => b.onclick = () => document.querySelectorAll('[data-mode]').forEach(x => x.classList.toggle('chosen', x === b)));
$('regenerate').onclick = $('batch').onclick = () => toast('唱歌模型尚未接入，当前可导入、编辑、试听与导出歌词。');
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
  if (!project()?.lines.length) { toast('请先导入 LRC 歌词。'); return; }
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
$('importLyrics').onclick = () => { if (busy || !project()) return; if (!project().assets.source) { toast('请先导入原唱音频，以确定时间轴长度。'); return; } $('lrcFile').value = ''; $('lrcFile').click(); };
$('lrcFile').onchange = () => {
  const file = $('lrcFile').files[0]; if (!file) return;
  if (project().lines.length && !confirm('导入 LRC 会替换本工程的全部歌词编辑，是否继续？')) return;
  mutate(async () => {
    if (file.size > 2 * 1024 * 1024) throw new Error('LRC 文件不能超过 2 MB。');
    const p = project();
    const updated = await api('projects/' + p.id + '/lyrics', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({revision: p.revision, text: await file.text()})});
    selected = 0; replaceProject(updated); toast('LRC 时间轴已导入，可逐句修改。');
  });
};
$('exportLyrics').onclick = () => {
  if (!project()?.lines.length) { toast('请先导入歌词。'); return; }
  const a = document.createElement('a'); a.href = '/api/projects/' + project().id + '/lyrics.lrc'; a.download = 'lyrics.lrc'; a.click();
};
async function init() {
  try {
    const session = await api('session'); token = session.token;
    projects = await api('projects'); render(); loadAudio(); $('saveState').textContent = '本机服务已连接';
  } catch (error) {
    render(); document.querySelector('.badge').textContent = '服务未连接'; toast('请运行 python -m backend.app，再打开本机服务地址。');
  }
}
init();
