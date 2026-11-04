const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const src=fs.readFileSync('design/studio.js','utf8');
const code=src.slice(src.indexOf('async function api('),src.indexOf('async function mutate('));
function response(status,data){return {status,ok:status>=200&&status<300,json:async()=>data,clone(){return response(status,data)}}}
(async()=>{
  const calls=[]; const ctx={token:'expired',fetch:async(path,options)=>{calls.push([path,options]);if(path==='/api/session')return response(200,{token:'fresh'});return options.headers['X-Studio-Token']==='expired'?response(403,{error:'会话已失效，请刷新页面。'}):response(200,{saved:true})}};
  vm.createContext(ctx);vm.runInContext(code,ctx);
  assert.equal((await ctx.api('projects/1',{method:'PUT',body:'lyrics'})).saved,true);
  assert.equal(calls.length,3);assert.equal(calls[2][1].body,'lyrics');assert.equal(calls[2][1].headers['X-Studio-Token'],'fresh');
  let count=0;ctx.fetch=async()=>{count++;return response(403,{error:'来源无效，请从应用页面操作。'})};
  await assert.rejects(ctx.api('projects/1',{method:'PUT'}),/来源无效/);assert.equal(count,1);
  console.log('Session recovery and forbidden origin handling passed');
})().catch(e=>{console.error(e);process.exit(1)});
