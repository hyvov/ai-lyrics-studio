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
    STAGES:{}, timelineDirty:false, activeVoice:'original', $: id => elements[id],
    project: () => context.projects[current], clearTimeout(){}, setTimeout(){throw Error('unexpected retry')},
    api: async path => { if(path==='jobs/job') return job; assert.equal(path,'projects/a'); if(scenario==='switch-during-fetch') current=1; return {id:'a', revision:3, rendered_available:true}; },
    loadAudio(){loads++}, render(){}, renderTimeline(){}, toast(){} };
  vm.createContext(context); vm.runInContext(poll, context); await context.pollJob();
  assert.equal(context.projects[0].revision,3); assert.equal(context.projects[1].revision,2);
  assert.equal(loads,scenario === 'current' ? 1 : 0);
  assert.equal(context.activeVoice,scenario === 'current' ? 'rendered' : 'original');
}
(async()=>{for(const scenario of ['current','already-switched','switch-during-fetch']) await check(scenario); console.log('Job refresh scenarios passed');})().catch(e=>{console.error(e);process.exit(1)});
