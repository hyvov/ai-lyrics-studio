const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('design/studio.js', 'utf8');
const poll = source.slice(source.indexOf('async function pollJob()'), source.indexOf("$('cancelJob').onclick"));
async function check(scenario) {
  const elements = new Proxy({}, {get: (o, id) => o[id] ||= {open:false}});
  let current = scenario === 'already-switched' ? 1 : 0, loads = 0;
  const job = {id:'job', project_id:'a', kind:'compose', status:'succeeded', stage:'complete', warnings:[]};
  const context = {projects:[{id:'a', revision:1}, {id:'b', revision:2}], activeJob:job, pollTimer:null,
    STAGES:{}, timelineDirty:false, activeVoice:'original', batchProjectId:null, batchQueue:[], $: id => elements[id],
    project: () => context.projects[current], clearTimeout(){}, setTimeout(){throw Error('unexpected retry')},
    api: async path => { if(path==='jobs/job') return job; assert.equal(path,'projects/a'); if(scenario==='switch-during-fetch') current=1; return {id:'a', revision:3, rendered_available:true}; },
    loadAudio(){loads++}, render(){}, renderTimeline(){}, toast(){} };
  vm.createContext(context); vm.runInContext(poll, context); await context.pollJob();
  assert.equal(context.projects[0].revision,3); assert.equal(context.projects[1].revision,2);
  assert.equal(loads,scenario === 'current' ? 1 : 0);
  assert.equal(context.activeVoice,scenario === 'current' ? 'rendered' : 'original');
}
async function checkMixedBatch() {
  const elements = new Proxy({}, {get: (o,id) => o[id] ||= {open:false}});
  const job={id:'job',project_id:'a',kind:'render',status:'succeeded',stage:'complete',warnings:[],segment_ids:['first']};
  let next=null;
  const context={projects:[{id:'a',revision:1},{id:'b',revision:2}],activeJob:job,pollTimer:null,
    STAGES:{},timelineDirty:false,activeVoice:'original',batchProjectId:'a',batchQueue:[['second']],$:id=>elements[id],
    project:()=>context.projects[1],clearTimeout(){},setTimeout(){throw Error('unexpected retry')},
    api:async path=>path==='jobs/job'?job:{id:'a',revision:3},
    startRender:async(ids,p)=>{next={ids,p}},loadAudio(){throw Error('wrong project')},render(){},renderTimeline(){},toast(){}};
  vm.createContext(context);vm.runInContext(poll,context);await context.pollJob();
  assert.equal(next.ids[0],'second');assert.equal(next.p.id,'a');assert.equal(next.p.revision,3);
}
(async()=>{for(const scenario of ['current','already-switched','switch-during-fetch']) await check(scenario); await checkMixedBatch();console.log('Job refresh and mixed batch scenarios passed');})().catch(e=>{console.error(e);process.exit(1)});
