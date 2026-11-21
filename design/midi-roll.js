/* One event per analyzed source note, including silence. Times are project seconds. */
class MidiRoll {
  constructor(host, onChange) {
    this.host = host;
    this.onChange = onChange;
    this.plane = host.querySelector('.midi-plane');
    this.field = id => host.querySelector('#' + id);
    this.plane.onpointerdown = e => this.beginDrag(e);
    this.plane.onpointermove = e => this.moveDrag(e);
    this.plane.onpointerup = this.plane.onpointercancel = () => {
      const moved = this.drag?.moved;
      this.drag = null;
      if (moved) this.commit();
    };
    this.plane.onclick = e => {
      const note = e.target.closest('[data-note]');
      if (note && !e.detail) this.select(Number(note.dataset.note));
    };
    for (const [id, offset] of [['midiStart', -1], ['midiEnd', 0]]) {
      const input = this.field(id);
      input.onchange = input.onblur = e => {
        const i = this.selected + offset;
        if (!Number.isFinite(e.target.valueAsNumber)) { this.inspect(); return; }
        if (i < 0 || Math.abs(e.target.valueAsNumber - this.ends[i]) < .0005) return;
        this.setEdge(i, e.target.valueAsNumber);
        this.commit();
      };
    }
    this.field('midiPitch').onchange = this.field('midiPitch').onblur = e => {
      const value = e.target.value === '' ? null : e.target.valueAsNumber;
      if (value === this.pitches[this.selected]) return;
      if (value === null || Number.isInteger(value) && value >= 1 && value <= 127) {
        this.pitches[this.selected] = value;
        this.commit();
      } else this.inspect();
    };
    this.field('midiLyric').onchange = e => {
      this.assignment = this.layout().slice();
      this.assignment[this.selected] = e.target.value === '' ? null : Number(e.target.value);
      if (this.assignment[this.selected] === null) this.pitches[this.selected] = null;
      this.commit();
    };
    this.field('midiReset').onclick = () => {
      this.restore(null);
      this.onChange();
      this.draw();
    };
  }
  load(notes, line, saved) {
    this.notes = notes;
    this.line = line;
    this.drag = null;
    this.selected = 0;
    this.words = [];
    this.signature = '';
    this.restore(saved);
    this.host.hidden = false;
  }
  restore(saved) {
    this.ends = this.notes.map((n, i) => saved?.events[i].end ??
      (i === this.notes.length-1 ? this.line.end : Math.round(n.end * 1000000) / 1000000));
    this.pitches = this.notes.map((_, i) => saved?.events[i].pitch ?? null);
    this.assignment = saved?.assignment?.slice() ?? null;
    this.edited = !!saved;
  }
  update(words, unitMap, controls) {
    const signature = JSON.stringify([words, unitMap, controls]);
    if (this.signature === signature) return;
    if (this.signature && JSON.stringify(this.words) !== JSON.stringify(words)) this.assignment = null;
    this.signature = signature;
    this.words = words;
    let source = -1;
    const sourceIndices = this.notes.map(n => {
      if (n.type === 2) source++;
      return n.text === '<SP>' ? null : source;
    });
    this.auto = sourceIndices.map(i => i === null ? null : unitMap?.[i] ?? null);
    this.controls = controls;
    this.gapError = '';
    words.forEach((word, index) => {
      if (!/^[a-z]/i.test(word)) return;
      const sources = unitMap?.filter(v => v === index).length || 0;
      const control = controls.find(c => c.index === index);
      const next = this.auto.lastIndexOf(index) + 1, gap = this.notes[next+1];
      const duration = gap && gap.end-gap.start;
      if (!sources || !gap || this.auto[next] == null || /^[a-z]/i.test(words[this.auto[next]]) ||
          gap.text !== '<SP>' || duration < .02-1e-6) {
        if (control?.gap_policy === 'move') this.gapError = word + ' 后没有可借用的相邻空拍。';
        return;
      }
      if (control?.gap_policy === 'move' || (control?.gap_policy !== 'keep' && sources >= 2 && duration >= .25-1e-6)) {
        this.auto[next+1] = this.auto[next];
        this.auto[next] = index;
      }
    });
    this.draw();
  }
  layout() { return this.assignment ?? this.auto; }
  start(i) { return i ? this.ends[i-1] : this.line.start; }
  pitch(i) {
    if (this.layout()[i] == null) return 0;
    if (this.pitches[i] || this.notes[i].pitch) return this.pitches[i] || this.notes[i].pitch;
    for (let d = 1; d < this.notes.length; d++) {
      const pitch = this.notes[i-d]?.pitch || this.notes[i+d]?.pitch;
      if (pitch) return pitch;
    }
    return 60;
  }
  name(pitch) {
    return ['C','C♯','D','D♯','E','F','F♯','G','G♯','A','A♯','B'][pitch%12] + (Math.floor(pitch/12)-1);
  }
  error() {
    const slots = this.layout();
    if (!this.assignment && this.gapError) return this.gapError;
    let last = -1;
    for (let i = 0; i < slots.length; i++) {
      const target = slots[i];
      if (this.edited && this.ends[i]-this.start(i) < .02-1e-7) return '每个音符至少需要 20 毫秒，请调整边界。';
      if (target === null) {
        if (this.pitches[i] !== null) return '空拍的音高须恢复自动。';
        continue;
      }
      if (this.assignment && (target < last || target > last+1 || (target === last && slots[i-1] == null)))
        return '请按歌词顺序连续分配音符，同一个字词不能跨过空拍。';
      last = target;
    }
    if (this.assignment && last !== this.words.length-1) return '还有新词未分配音符。';
    for (const c of this.controls) {
      const count = slots.filter(i => i === c.index).length;
      if (c.slots.length && c.slots.length !== count) return c.word + ' 占用 ' + count + ' 个音符，请填写对应组数的音素或留空。';
    }
    return '';
  }
  value(sourceId, text) {
    if (!this.edited) return null;
    const {original, start, end} = this.line;
    const events = this.notes.map((n, i) => ({text:n.text, type:n.type, source_duration:n.end-n.start,
      end:this.ends[i], pitch:this.pitches[i]}));
    const value = {source_id:sourceId, original, start, end, events};
    if (this.assignment) Object.assign(value, {lyrics:text, assignment:this.assignment});
    return value;
  }
  select(index) {
    this.selected = index;
    for (const button of this.plane.children) button.setAttribute('aria-pressed', Number(button.dataset.note) === index);
    this.inspect();
  }
  inspect() {
    const i = this.selected, n = this.notes[i], target = this.layout()[i], pitch = this.pitch(i);
    this.field('midiSelection').textContent = '音符 ' + (i+1) + ' · 原唱 ' + (n.text === '<SP>' ? '空拍' : n.text);
    this.field('midiStart').value = this.start(i).toFixed(3);
    this.field('midiStart').disabled = !i;
    this.field('midiEnd').value = this.ends[i].toFixed(3);
    this.field('midiEnd').disabled = i === this.notes.length-1;
    const input = this.field('midiPitch');
    input.value = this.pitches[i] ?? '';
    input.placeholder = pitch ? pitch + ' · ' + this.name(pitch) : '空拍';
    input.disabled = target === null && this.pitches[i] === null;
    const select = this.field('midiLyric');
    select.replaceChildren(new Option('空拍', ''), ...this.words.map((w,j) => new Option((j+1)+' · '+w, String(j))));
    select.value = target ?? '';
    this.field('midiReset').disabled = !this.edited;
  }
  draw() {
    if (!this.notes || !this.auto) return;
    const layout = this.layout(), pitches = this.notes.map((_,i) => this.pitch(i));
    if (!this.drag) {
      const voiced = [...pitches,...this.notes.map(n => n.pitch)].filter(p => p > 0);
      this.low = Math.max(1, Math.min(...(voiced.length ? voiced : [60])) - 2);
      this.high = Math.min(127, Math.max(this.low+6, ...voiced.map(p => p+2)));
    }
    const low = this.low, high = this.high, rows = high-low+2;
    const inner = this.host.querySelector('.midi-roll-inner');
    inner.style.minWidth = Math.max(520, Math.min(5000,this.notes.length*36))+'px';
    inner.style.setProperty('--midi-rows', rows);
    const keys = this.host.querySelector('.midi-keys');
    keys.replaceChildren();
    for (let p=high; p>=low-1; p--) {
      const key = document.createElement('span');
      key.textContent = p<low ? '空拍' : this.name(p);
      keys.append(key);
    }
    const span = this.line.end-this.line.start;
    const ruler = this.host.querySelector('.midi-ruler');
    ruler.replaceChildren();
    for (let tick=0; tick<=4; tick++) {
      const label = document.createElement('span');
      label.textContent = (this.line.start+tick*span/4).toFixed(2)+' s';
      ruler.append(label);
    }
    const buttons = this.notes.map((n, i) => {
      const b = document.createElement('button'), target = layout[i], pitch = pitches[i];
      const word = this.words[target] ?? '空拍';
      const parts = this.controls.find(c => c.index === target)?.slots;
      const phone = parts?.[layout.slice(0,i).filter(v => v === target).length];
      b.type = 'button'; b.dataset.note = i;
      b.className = 'roll-note' + (pitch ? '' : ' roll-rest');
      b.setAttribute('aria-pressed', i === this.selected);
      b.title = (i+1)+' · '+word+' · '+(pitch ? this.name(pitch) : '空拍')+' · '+this.start(i).toFixed(3)+'–'+this.ends[i].toFixed(3)+' s';
      b.setAttribute('aria-label', b.title);
      b.style.left = ((this.start(i)-this.line.start)/span*100)+'%';
      b.style.width = ((this.ends[i]-this.start(i))/span*100)+'%';
      b.style.top = ((pitch ? high-pitch : rows-1)*22+1)+'px';
      const label = document.createElement('span'); label.textContent = phone ? word+' · '+phone : word;
      b.append(label);
      for (const side of ['start','end']) {
        if (side === 'start' ? i === 0 : i === this.notes.length-1) continue;
        const edge = document.createElement('i'); edge.dataset.edge = side; b.append(edge);
      }
      return b;
    });
    this.plane.replaceChildren(...buttons);
    this.inspect();
  }
  setEdge(i, value) {
    if (i < 0 || i >= this.ends.length-1) return;
    const min = this.start(i)+.02, max = this.ends[i+1]-.02;
    if (min <= max) this.ends[i] = Math.max(min, Math.min(max, Math.round(value*1000)/1000));
  }
  commit() {
    this.edited = true;
    this.draw();
    this.onChange();
  }
  beginDrag(e) {
    const button = e.target.closest('[data-note]');
    if (!button || e.button !== 0) return;
    if (document.activeElement instanceof HTMLInputElement) document.activeElement.blur();
    const i = Number(button.dataset.note);
    this.select(i);
    this.drag = {i, edge:e.target.dataset.edge, x:e.clientX, y:e.clientY, pitch:this.pitch(i), override:this.pitches[i],
      start:this.start(i), end:this.ends[i], prev:i>0 ? this.start(i-1) : null, next:this.ends[i+1]};
    this.plane.setPointerCapture(e.pointerId);
  }
  moveDrag(e) {
    if (!this.drag) return;
    const d = this.drag, dx = e.clientX-d.x, dy = e.clientY-d.y;
    if (!d.moved && Math.abs(dx)<3 && Math.abs(dy)<3) return;
    d.moved = true;
    const seconds = dx / this.plane.clientWidth * (this.line.end-this.line.start);
    if (d.edge) {
      const left = d.edge === 'start';
      this.setEdge(left ? d.i-1 : d.i, (left ? d.start : d.end) + seconds);
    } else {
      if (d.prev !== null && d.next !== undefined) {
        const delta = Math.max(d.prev+.02-d.start, Math.min(d.next-.02-d.end, Math.round(seconds*1000)/1000));
        this.ends[d.i-1] = d.start+delta;
        this.ends[d.i] = d.end+delta;
      }
      if (d.pitch) this.pitches[d.i] = Math.abs(dy)<11 ? d.override : Math.max(this.low, Math.min(this.high, d.pitch-Math.round(dy/22)));
    }
    const span = this.line.end-this.line.start;
    for (let i=Math.max(0,d.i-1); i<=Math.min(this.notes.length-1,d.i+1); i++) {
      const note = this.plane.children[i], pitch = this.pitch(i);
      note.style.left = (this.start(i)-this.line.start)/span*100+'%';
      note.style.width = (this.ends[i]-this.start(i))/span*100+'%';
      note.style.top = ((pitch ? this.high-pitch : this.high-this.low+1)*22+1)+'px';
    }
    this.field('midiStart').value = this.start(d.i).toFixed(3);
    this.field('midiEnd').value = this.ends[d.i].toFixed(3);
    this.field('midiPitch').value = this.pitches[d.i] ?? '';
  }
}
