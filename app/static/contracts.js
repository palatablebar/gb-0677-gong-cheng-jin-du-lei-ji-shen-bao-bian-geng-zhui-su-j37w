function post(url,data){return fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data||{})}).then(async response=>{const body=await response.json().catch(()=>({}));if(!response.ok)throw new Error(body.message||'请求失败');return body;});}
function flash(button,text,bad){const scope=button.closest('.panel')||document;const node=scope.querySelector('.notice')||document.getElementById('notice');if(node){node.textContent=text;node.style.color=bad?'var(--bad)':'';}}
document.querySelectorAll('.sign').forEach(button=>button.addEventListener('click',async()=>{
  button.disabled=true;flash(button,'签认中…');
  try{await post('/api/runs/'+certificate.id+'/contract/sign',{version:certificate.version,contract_key:button.dataset.contract});window.location.reload();}
  catch(error){button.disabled=false;flash(button,error.message,true);}
}));
document.querySelectorAll('.restore').forEach(button=>button.addEventListener('click',async()=>{
  button.disabled=true;flash(button,'恢复中…');
  try{const data={};if(button.dataset.revision)data.revision=Number(button.dataset.revision);
    await post('/api/contracts/'+encodeURIComponent(button.dataset.contract)+'/restore',data);window.location.reload();}
  catch(error){button.disabled=false;flash(button,error.message,true);}
}));
