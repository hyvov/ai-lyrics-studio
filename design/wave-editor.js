/* Interactive waveform editor. Times are absolute seconds throughout. */
class WaveEditor {
  constructor(host, {draw, onChange, onSeek}) {
    this.host = host; this.draw = draw; this.onChange = onChange; this.onSeek = onSeek;
    this.regions = []; this.nodes = []; this.undo = []; this.redo = [];
    host.classList.add('wave-editor');
    const tools = document.createElement('div'); tools.className = 'wave-tools';
    const button = (name, action) => { const b = document.createElement('button'); b.textContent = name; b.type = 'button'; b.onclick = action; tools.append(b); return b; };
    button('＋ 放大', () => this.zoom(1.6)); button('− 缩小', () => this.zoom(1/1.6));
    button('适合整句', () => { this.view = [...this.domain]; this.layout(); });
    this.undoButton = button('撤销', () => this.history(-1));
    this.redoButton = button('重做', () => this.history(1));
    this.readout = document.createElement('span'); tools.append(this.readout);
    this.ruler = document.createElement('div'); this.ruler.className = 'wave-ruler';
    this.stage = document.createElement('div'); this.stage.className = 'wave-stage';
    this.wave = document.createElement('div'); this.wave.className = 'wave peach';
    this.regionsLayer = document.createElement('div'); this.regionsLayer.className = 'wave-regions';
    this.playhead = document.createElement('div'); this.playhead.className = 'wave-playhead';
    this.stage.append(this.wave, this.regionsLayer, this.playhead);
    this.pan = document.createElement('input'); this.pan.type = 'range'; this.pan.min = 0; this.pan.max = 1000; this.pan.step = 1;
    this.pan.setAttribute('aria-label', '波形视窗位置');
    this.pan.oninput = () => { const span = this.view[1]-this.view[0], start = this.domain[0] + (this.domain[1]-this.domain[0]-span)*this.pan.value/1000; this.view = [start, start+span]; this.layout(); };
    const hint = document.createElement('small'); hint.textContent = '拖两侧竖线改边界 · 拖文字块整体移动 · 点击空白定位 · 滚轮缩放 · Shift+滚轮平移 · 方向键微调 1ms';
    host.append(tools, this.ruler, this.stage, this.pan, hint);
    this.stage.onpointerdown = e => this.begin(e);
    this.stage.onpointermove = e => this.move(e);
    this.stage.onpointerup = e => this.end(e, false);
    this.stage.onpointercancel = e => this.end(e, true);
    this.stage.addEventListener('wheel', e => {
      e.preventDefault();
      if (e.shiftKey || Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
        const span = this.view[1]-this.view[0];
        const start = this.clamp(this.view[0] + (e.deltaX || e.deltaY) / this.stage.clientWidth * span, this.domain[0], this.domain[1]-span);
        this.view = [start, start+span]; this.layout();
      } else this.zoom(Math.exp(-e.deltaY*.003), this.timeAt(e.clientX));
    }, {passive:false});
    new ResizeObserver(() => this.layout()).observe(this.stage);
  }
  clamp(v,a,b) { return Math.max(a, Math.min(b,v)); }
  setData({key, values, duration, domain, regions, disabled = false, minDuration = .02, coupleAdjacent = false}) {
    if (key !== this.key) { this.key=key; this.domain=[...domain]; this.view=[...domain]; this.undo=[]; this.redo=[]; }
    this.values=values; this.duration=duration; this.disabled=disabled; this.minDuration=minDuration; this.coupleAdjacent=coupleAdjacent;
    this.regions=structuredClone(regions);
    if (this.nodes.length !== regions.length) {
      this.regionsLayer.replaceChildren(); this.nodes=[];
      regions.forEach((r,i) => {
        const box=document.createElement('div'); box.className='wave-region'; box.dataset.index=i; box.dataset.edge='move';
        const label=document.createElement('span'); label.className='wave-region-label'; label.textContent=r.text; box.append(label);
        for (const edge of ['start','end']) {
          const h=document.createElement('button'); h.type='button'; h.className='wave-handle '+edge;
          h.dataset.index=i; h.dataset.edge=edge; h.setAttribute('role','slider');
          h.setAttribute('aria-label', r.text+' '+edge+' 拖动边界');
          h.onkeydown=e=>{
            if (!['ArrowLeft','ArrowRight'].includes(e.key)||this.disabled) return;
            e.preventDefault(); this.remember(); this.adjust(i,edge,this.regions[i][edge]+(e.key==='ArrowRight'?1:-1)*(e.shiftKey ? .01 : .001));
            this.onChange(structuredClone(this.regions)); this.geometry();
          }; box.append(h);
        }
        this.regionsLayer.append(box); this.nodes.push(box);
      });
    }
    this.layout();
  }
  timeAt(x) { const rect=this.stage.getBoundingClientRect(); return this.view[0]+this.clamp((x-rect.left)/rect.width,0,1)*(this.view[1]-this.view[0]); }
  zoom(factor, anchor=(this.view[0]+this.view[1])/2) {
    if (!this.view) return;
    const old=this.view[1]-this.view[0], full=this.domain[1]-this.domain[0];
    const span=this.clamp(old/factor,Math.min(.1,full),full);
    const start=this.clamp(anchor-(anchor-this.view[0])/old*span,this.domain[0],this.domain[1]-span);
    this.view=[start,start+span]; this.layout();
  }
  remember() { this.undo.push(structuredClone(this.regions)); if(this.undo.length>50)this.undo.shift(); this.redo=[]; }
  history(direction) {
    const from=direction<0?this.undo:this.redo, to=direction<0?this.redo:this.undo;
    if(!from.length||this.disabled)return;
    to.push(structuredClone(this.regions)); this.regions=from.pop(); this.onChange(structuredClone(this.regions)); this.geometry();
  }
  adjust(index,edge,time,origin) {
    const r=this.regions[index], prev=this.regions[index-1], next=this.regions[index+1];
    const min=prev?prev.end:this.domain[0], max=next?next.start:this.domain[1];
    if(edge==='move') {
      const length=origin.end-origin.start, start=this.clamp(time,min,max-length);
      r.start=+start.toFixed(3);r.end=+(start+length).toFixed(3);
    } else if (this.coupleAdjacent && edge==='start' && prev) {
      r.start=+this.clamp(time,prev.start+this.minDuration,r.end-this.minDuration).toFixed(3);
      if(r.start<prev.end)prev.end=r.start;
    } else if (this.coupleAdjacent && edge==='end' && next) {
      r.end=+this.clamp(time,r.start+this.minDuration,next.end-this.minDuration).toFixed(3);
      if(r.end>next.start)next.start=r.end;
    } else r[edge]=+this.clamp(time,edge==='start'?min:r.start+this.minDuration,edge==='end'?max:r.end-this.minDuration).toFixed(3);
  }
  begin(e) {
    if(e.button!==0)return;
    const handle=e.target.closest('[data-edge]');
    if(handle && this.disabled)return;
    e.preventDefault(); this.stage.setPointerCapture(e.pointerId);
    if(!handle) { this.drag={seek:true};this.onSeek(this.timeAt(e.clientX));return; }
    const i=+handle.dataset.index;
    this.remember(); this.drag={i,edge:handle.dataset.edge,anchor:this.timeAt(e.clientX),origin:structuredClone(this.regions[i]),before:structuredClone(this.regions)};
    this.host.classList.add('dragging');
  }
  move(e) {
    if(!this.drag)return;
    if(this.drag.seek){this.onSeek(this.timeAt(e.clientX));return;}
    const {i,edge,anchor,origin}=this.drag, time=this.timeAt(e.clientX);
    // Recalculate from the pre-drag snapshot so reversing a drag restores neighbors.
    this.regions=structuredClone(this.drag.before);
    this.adjust(i,edge,edge==='move'?origin.start+time-anchor:time,origin);
    this.onChange(structuredClone(this.regions)); this.geometry();
  }
  end(e,cancel) {
    if(!this.drag)return;
    if(cancel&&!this.drag.seek){this.regions=this.drag.before;this.undo.pop();this.onChange(structuredClone(this.regions));}
    this.drag=null;this.host.classList.remove('dragging');this.geometry();
    if(this.stage.hasPointerCapture(e.pointerId))this.stage.releasePointerCapture(e.pointerId);
  }
  layout() {
    if(!this.view||!this.stage.clientWidth)return;
    const [a,b]=this.view, n=this.values?.length||0;
    const slice=this.values?.slice(Math.floor(a/this.duration*n),Math.ceil(b/this.duration*n));
    this.draw(this.wave,slice,'正在读取原唱波形…');
    this.readout.textContent=a.toFixed(3)+' — '+b.toFixed(3)+' s';
    this.ruler.replaceChildren();
    const raw=(b-a)/Math.max(2,Math.floor(this.stage.clientWidth/85)), magnitude=10**Math.floor(Math.log10(raw));
    const step=[1,2,5,10].map(x=>x*magnitude).find(x=>x>=raw);
    for(let t=Math.ceil(a/step)*step;t<=b;t+=step){const tick=document.createElement('span');tick.style.left=(t-a)/(b-a)*100+'%';tick.textContent=t.toFixed(step<1?2:1)+'s';this.ruler.append(tick);}
    const full=this.domain[1]-this.domain[0], span=b-a;
    this.pan.disabled=this.disabled||span>=full-.00001;this.pan.value=span>=full?0:(a-this.domain[0])/(full-span)*1000;
    this.geometry();
    this.setPlayhead(this.position || 0);
  }
  geometry() {
    if(!this.view)return;
    const [a,b]=this.view;
    this.regions.forEach((r,i)=>{
      const box=this.nodes[i];box.style.left=(r.start-a)/(b-a)*100+'%';box.style.width=(r.end-r.start)/(b-a)*100+'%';
      box.querySelector('.wave-region-label').textContent=r.text;
      box.title=r.text+' '+r.start.toFixed(3)+' — '+r.end.toFixed(3)+' s';
      for(const h of box.querySelectorAll('.wave-handle')){
        h.setAttribute('aria-label',r.text+' '+h.dataset.edge+' 拖动边界');
        h.setAttribute('aria-valuenow',r[h.dataset.edge].toFixed(3));h.disabled=this.disabled;
      }
    });
    this.undoButton.disabled=this.disabled||!this.undo.length;this.redoButton.disabled=this.disabled||!this.redo.length;
  }
  setPlayhead(time) {
    this.position=time;
    if(!this.view)return;
    this.playhead.style.left=(time-this.view[0])/(this.view[1]-this.view[0])*100+'%';
    this.playhead.hidden=time<this.view[0]||time>this.view[1];
  }
}
