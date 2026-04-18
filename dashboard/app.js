// ─── Utils ───
function $(id){return document.getElementById(id)}
function _e(s){if(!s)return'';const d=document.createElement('div');d.textContent=s;return d.innerHTML}

// ─── i18n ───
const UI={ko:{},en:{}};
let lang=localStorage.getItem('hub-lang')||'ko';
let _prevLang=null; // previous lang before reset

// Translation helper: t('key') or t('key', {name: 'Acme', count: 3})
function t(key, vars){
  let s=(UI[lang]&&UI[lang][key])||(UI.en&&UI.en[key])||key;
  if(vars){for(const k in vars){s=s.replace(new RegExp('\\{'+k+'\\}','g'),vars[k])}}
  return s;
}

// RTL language set (ISO 639-1 codes)
const RTL_LANGS=new Set(['he','ar','fa','ur','yi','ps','sd','ku','dv']);

// Apply translations to DOM elements with data-i18n and data-i18n-attr
function applyI18n(root){
  const scope=root||document;
  // text content
  scope.querySelectorAll('[data-i18n]').forEach(el=>{
    const key=el.getAttribute('data-i18n');
    const v=t(key);
    if(v&&v!==key)el.textContent=v;
  });
  // attributes (format: "attr1:key1;attr2:key2")
  scope.querySelectorAll('[data-i18n-attr]').forEach(el=>{
    const spec=el.getAttribute('data-i18n-attr');
    spec.split(';').forEach(pair=>{
      const [attr,key]=pair.split(':').map(s=>s.trim());
      if(!attr||!key)return;
      const v=t(key);
      if(v&&v!==key)el.setAttribute(attr,v);
    });
  });
  // <html> lang + dir (RTL for Hebrew/Arabic/Persian/Urdu etc)
  document.documentElement.lang=lang;
  document.documentElement.dir=RTL_LANGS.has(lang)?'rtl':'ltr';
}

// Map ISO 639-1 language code to BCP47 locale for Intl APIs
function _locale(){
  const map={ko:'ko-KR',en:'en-US',ja:'ja-JP',zh:'zh-CN',de:'de-DE',fr:'fr-FR',es:'es-ES',it:'it-IT',pt:'pt-BR',ru:'ru-RU',ar:'ar-SA',he:'he-IL',fa:'fa-IR',hi:'hi-IN',th:'th-TH',vi:'vi-VN',id:'id-ID',tr:'tr-TR',nl:'nl-NL'};
  return map[lang]||lang||'en-US';
}

async function loadLang(code){
  try{
    const r=await fetch(`/api/i18n/${code}`);
    if(r.ok){UI[code]=await r.json();return true}
  }catch(e){}
  return false;
}

async function checkLang(){
  const o=$('lang-overlay');
  const skip=$('lang-skip-btn');
  // Always try to load English as fallback
  if(!UI.en||!Object.keys(UI.en).length)await loadLang('en');
  if(!localStorage.getItem('hub-lang')){
    // New device — but if server already has companies, auto-detect lang from first company
    try{
      const companies=await(await fetch('/api/companies')).json();
      if(companies.length&&companies[0].lang){
        const autoLang=companies[0].lang;
        localStorage.setItem('hub-lang',autoLang);
        lang=autoLang;
        await loadLang(lang);
        o.style.display='none';
        applyI18n();
        return;
      }
    }catch(e){}
    // Truly first visit (no companies) — show language overlay
    o.style.display='flex';
    if(skip)skip.style.display='none';
    applyI18n();
    return;
  }
  o.style.display='none';
  await loadLang(lang);
  applyI18n();
}

async function genLang(){
  const v=$('lang-input').value.trim();if(!v)return;
  const b=$('lang-btn'),s=$('lang-status');
  b.disabled=true;b.textContent='⏳...';s.textContent=t('lang.translating');s.style.color='#60a5fa';
  try{const r=await fetch('/api/i18n/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({language:v})});const d=await r.json();
    if(d.ok){
      s.textContent='✅';
      localStorage.setItem('hub-lang',d.lang_code);
      lang=d.lang_code;
      await loadLang(lang);
      applyI18n();
      setTimeout(()=>$('lang-overlay').style.display='none',500);
    }
    else{s.textContent='❌ '+d.error;s.style.color='#ef4444';b.disabled=false;b.textContent=t('lang.retry')}}
  catch(e){s.textContent='❌';b.disabled=false;b.textContent=t('lang.retry')}
}
function skipLang(){
  // Restore previous language
  const restore=_prevLang||'ko';
  localStorage.setItem('hub-lang',restore);lang=restore;
  $('lang-overlay').style.display='none';
  _prevLang=null;
}
function resetLang(){
  // Remember current lang so skip can restore it
  _prevLang=localStorage.getItem('hub-lang')||'ko';
  $('lang-overlay').style.display='flex';
  const skip=$('lang-skip-btn');
  if(skip)skip.style.display='';
}

// ─── Browser Notifications ───
let notifEnabled=localStorage.getItem('hub-notif')!=='off';
function initNotif(){
  if(!('Notification' in window))return;
  if(Notification.permission==='default'&&notifEnabled)Notification.requestPermission();
  updateNotifBtn();
}
function toggleNotif(){
  notifEnabled=!notifEnabled;
  localStorage.setItem('hub-notif',notifEnabled?'on':'off');
  if(notifEnabled&&'Notification' in window&&Notification.permission==='default')Notification.requestPermission();
  updateNotifBtn();
}
function updateNotifBtn(){
  const b=$('notif-toggle');if(!b)return;
  b.style.opacity=notifEnabled?'1':'0.4';
  b.title=notifEnabled?t('notif.on'):t('notif.off');
}
function notify(title,body){
  if(!notifEnabled||!document.hidden)return;
  if(!('Notification' in window)||Notification.permission!=='granted')return;
  try{const n=new Notification(title,{body,icon:'🏢'});n.onclick=()=>{window.focus();n.close()};setTimeout(()=>n.close(),5000)}catch(e){}
}

// ─── State ───
let cos=[],cur=null,lastLen=0,approvals=[],tasks=[],costs=null;
let selectedAgent=null;
const thinking={};
const agentLog={};

// ─── Core ───
async function load(){
  try{cos=(await(await fetch('/api/companies')).json())}catch(e){cos=[]}
  renderTabs();
  if(!cos.length){$('empty').style.display='block';$('dashboard').style.display='none';$('bottom-bar').style.display='none';$('drawer-btns').style.display='none'}
  else{$('empty').style.display='none';$('dashboard').style.display='flex';$('bottom-bar').style.display='flex';$('drawer-btns').style.display='';if(!cur||!cos.find(c=>c.id===cur))sel(cos[0].id);else refresh()}
}
function renderTabs(){$('tabs').innerHTML=cos.map(c=>`<div class="tab ${c.id===cur?'active':''}" onclick="sel('${c.id}')">${_e(c.name)}<span class="x" onclick="event.stopPropagation();delCo('${c.id}','${_e(c.name)}')">✕</span></div>`).join('')}
async function sel(id){cur=id;lastLen=0;selectedAgent=null;Object.keys(agentLog).forEach(k=>delete agentLog[k]);_threadCollapsed={};_planTasks=[];_planCollapsed=new Set();_planUserExpanded=new Set();renderTabs();await refresh()}

let chatMessages=[];
async function refresh(){
  if(!cur)return;
  const c=await(await fetch(`/api/company/${cur}`)).json();
  // Update cos array with latest company data (agents, etc.)
  const idx=cos.findIndex(x=>x.id===cur);
  if(idx>=0)cos[idx]=c;
  chatMessages=c.chat||[];
  chatMessages.forEach(m=>{
    if(m.type==='agent'&&m.from){
      const ag=(c.agents||[]).find(a=>a.name===m.from);
      if(ag)pushSpeech(ag.id,m.text||'',m.from,ag.emoji||'🤖',m.time||'')
    }
  });
  lastLen=chatMessages.length;
  renderIconGrid();renderChat();extras();
}

function pushSpeech(aid,text,name,emoji,time){
  if(!agentLog[aid])agentLog[aid]=[];
  const last=agentLog[aid][agentLog[aid].length-1];
  if(last&&last.raw===text)return;
  const mentions=(text.match(/@(\w+)/g)||[]).map(t=>t.slice(1));
  agentLog[aid].push({raw:text,mentions,time:time||now(),name,emoji});
  if(agentLog[aid].length>10)agentLog[aid].shift();
}
function now(){return new Date().toLocaleTimeString(_locale(),{hour:'2-digit',minute:'2-digit'})}

async function extras(){
  if(!cur)return;
  try{const[b,c,a]=await Promise.all([fetch(`/api/board-tasks/${cur}`),fetch(`/api/costs/${cur}`),fetch(`/api/approvals/${cur}?status=pending`)]);
    tasks=await b.json();costs=await c.json();approvals=await a.json();
    renderBanner();renderStats();updateAprBadge()}catch(e){}
}

// ─── Agent Icon Grid ───
function getAgentStatus(a){
  const aid=a.id;
  const isThinking=!!thinking[cur+':'+aid];
  if(isThinking)return'thinking';
  if(a.status==='working')return'working';
  if(a.status==='registering')return'registering';
  if(a.status==='active')return'active';
  return'idle';
}

function renderIconGrid(){
  const el=$('icon-grid');if(!el)return;
  const c=cos.find(x=>x.id===cur);
  if(!c||!c.agents){el.innerHTML='';return}
  el.innerHTML=c.agents.map(a=>{
    const st=getAgentStatus(a);
    const busy=st==='working'||st==='thinking';
    const cost=a.cost?a.cost.total_cost:0;
    const costStr=cost>0?`$${cost.toFixed(4)}`:'';
    const canFire=a.id!=='ceo';
    return`<div class="agent-icon s-${st}" onclick="selectAgent('${a.id}')" ondblclick="event.preventDefault();openPersona('${a.id}')" oncontextmenu="event.preventDefault();${canFire?`fireAgent('${a.id}')`:''}" title="${_e(a.name)} — dblclick: persona, right-click: dismiss">
      <div class="ai-circle">${_e(a.emoji||'🤖')}<span class="ai-dot"></span></div>
      <span class="ai-name">${_e(a.name)}</span>
      ${costStr?`<span class="ai-cost">${costStr}</span>`:''}
      ${busy?`<button class="ai-stop" onclick="event.stopPropagation();stopAgent('${a.id}')" title="Stop">■</button>`:''}
    </div>`;
  }).join('');
}

function selectAgent(aid){
  const c=cos.find(x=>x.id===cur);
  const a=c?(c.agents||[]).find(x=>x.id===aid):null;
  if(a)directMsg(a.name);
}

// Double-click to edit persona
let _personaAid=null;
async function openPersona(aid){
  if(!cur)return;
  _personaAid=aid;
  const c=cos.find(x=>x.id===cur);
  const a=c?(c.agents||[]).find(x=>x.id===aid):null;
  $('persona-title').textContent=`🧠 ${a?(a.emoji+' '+a.name):'Agent'} Persona`;
  try{const r=await fetch(`/api/agent-persona/${cur}/${aid}`);const d=await r.json();$('persona-text').value=d.persona||''}catch(e){$('persona-text').value=''}
  $('persona-modal').classList.add('show');
}
async function savePersona(){
  if(!cur||!_personaAid)return;
  const persona=$('persona-text').value.trim();
  try{await fetch(`/api/agent-persona/${cur}/${_personaAid}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({persona})});
    toast('✅ Persona saved');$('persona-modal').classList.remove('show')}catch(e){toast(t('toast.generic_error'))}
}

async function stopAgent(aid){
  if(!cur)return;
  try{
    const r=await fetch(`/api/agent-stop/${cur}/${aid}`,{method:'POST'});
    const d=await r.json();
    if(d.ok)toast('⏹ '+aid+' stopped');
  }catch(e){}
  refresh();
}

async function fireAgent(aid){
  if(!cur)return;
  const c=cos.find(x=>x.id===cur);
  const a=c?(c.agents||[]).find(x=>x.id===aid):null;
  if(!a)return;
  if(aid==='ceo'){toast('CEO cannot be dismissed');return}
  const reason=prompt(`Dismiss ${a.emoji} ${a.name}? Enter reason:`);
  if(!reason)return;
  // Create dismissal approval directly via API (not through CEO)
  try{
    const r=await fetch(`/api/agent-fire/${cur}/${aid}`,{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({reason})});
    const d=await r.json();
    if(d.ok)toast(`🔥 ${a.name} dismissal submitted for approval`);
    else toast(d.error||t('toast.generic_error'));
  }catch(e){toast(t('toast.generic_error'))}
}

// ─── Markdown → HTML ───
function _md(raw){
  if(!raw)return'';
  // Escape HTML first
  let t=_e(raw);
  // Code blocks (``` ... ```)
  t=t.replace(/```(\w*)\n([\s\S]*?)```/g,(m,lang,code)=>`<pre style="background:#0f172a;padding:8px 10px;border-radius:6px;overflow-x:auto;font-size:10px;margin:4px 0"><code>${code.trim()}</code></pre>`);
  // Inline code
  t=t.replace(/`([^`]+)`/g,'<code style="background:#0f172a;padding:1px 4px;border-radius:3px;font-size:10px">$1</code>');
  // Headings (## → h4, ### → h5)
  t=t.replace(/^### (.+)$/gm,'<div style="font-size:11px;font-weight:700;color:#94a3b8;margin:6px 0 2px">$1</div>');
  t=t.replace(/^## (.+)$/gm,'<div style="font-size:12px;font-weight:700;color:#f1f5f9;margin:8px 0 3px">$1</div>');
  t=t.replace(/^# (.+)$/gm,'<div style="font-size:13px;font-weight:800;color:#f1f5f9;margin:8px 0 4px">$1</div>');
  // Bold & italic
  t=t.replace(/\*\*\*(.+?)\*\*\*/g,'<strong><em>$1</em></strong>');
  t=t.replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>');
  t=t.replace(/\*(.+?)\*/g,'<em>$1</em>');
  // Strikethrough
  t=t.replace(/~~(.+?)~~/g,'<s style="color:var(--dim)">$1</s>');
  // Links (block javascript: protocol to prevent XSS)
  t=t.replace(/\[([^\]]+)\]\(([^)]+)\)/g,(m,text,url)=>{
    if(/^javascript:/i.test(url.trim()))return _e(text);
    return`<a href="${url.replace(/"/g,'&quot;')}" target="_blank" rel="noopener" style="color:#60a5fa;text-decoration:underline">${text}</a>`;
  });
  // Images (![alt](url)) — only allow http(s) and relative URLs
  t=t.replace(/!\[([^\]]*)\]\(([^)]+)\)/g,(m,alt,url)=>{
    if(/^javascript:/i.test(url.trim()))return'';
    return`<img src="${url.replace(/"/g,'&quot;')}" alt="${alt.replace(/"/g,'&quot;')}" style="max-width:200px;border-radius:6px;margin:4px 0;cursor:pointer" onclick="window.open(this.src)">`;
  });
  // Image URLs in text (only http/https — safe by pattern)
  t=t.replace(/(https?:\/\/\S+\.(png|jpg|jpeg|gif|webp))/gi,'<img src="$1" style="max-width:200px;border-radius:6px;margin:4px 0;cursor:pointer" onclick="window.open(this.src)">');
  // Unordered list items
  t=t.replace(/^[-•]\s+(.+)$/gm,'<div style="padding-left:12px">• $1</div>');
  // Ordered list items
  t=t.replace(/^(\d+)[.)]\s+(.+)$/gm,'<div style="padding-left:12px">$1. $2</div>');
  // Blockquote
  t=t.replace(/^&gt;\s?(.+)$/gm,'<div style="border-left:3px solid #374151;padding-left:8px;color:#94a3b8;margin:2px 0">$1</div>');
  // Horizontal rule
  t=t.replace(/^---$/gm,'<hr style="border:none;border-top:1px solid #1e3a5f;margin:6px 0">');
  // Simple table (| col | col |)
  t=t.replace(/((?:^\|.+\|$\n?)+)/gm,(match)=>{
    const rows=match.trim().split('\n').filter(r=>r.trim());
    if(rows.length<2)return match;
    let html='<table style="border-collapse:collapse;font-size:10px;margin:4px 0;width:100%">';
    rows.forEach((row,i)=>{
      const cells=row.split('|').filter(c=>c.trim());
      if(cells.every(c=>/^[-:]+$/.test(c.trim())))return; // separator row
      const tag=i===0?'th':'td';
      const style=i===0?'font-weight:600;color:#94a3b8;border-bottom:1px solid #334155;padding:3px 6px':'color:var(--text);padding:3px 6px;border-bottom:1px solid #1e293b';
      html+='<tr>'+cells.map(c=>`<${tag} style="${style}">${c.trim()}</${tag}>`).join('')+'</tr>';
    });
    html+='</table>';
    return html;
  });
  // @mentions
  t=t.replace(/@(\w+)/g,'<span style="color:#a5b4fc;font-weight:600">@$1</span>');
  // Line breaks (preserve \n as <br> for non-block content)
  t=t.replace(/\n/g,'<br>');
  // Clean up excessive <br> after block elements
  t=t.replace(/(<\/div>)<br>/g,'$1');
  t=t.replace(/(<\/pre>)<br>/g,'$1');
  t=t.replace(/(<\/table>)<br>/g,'$1');
  t=t.replace(/(<hr[^>]*>)<br>/g,'$1');
  return t;
}

// ─── Chat ───
let _threadCollapsed={};

function _renderMsg(m, agentMap){
  const isUser=m.type==='user'||m.type==='master';
  const agent=m.from?agentMap[m.from]:null;
  const emoji=isUser?'👤':(agent?agent.emoji||'🤖':'🤖');
  const name=isUser?t('chat.me'):(m.from||t('chat.system'));
  const cls=isUser?'cm-user':'cm-agent';
  let textHtml=_md(m.text||'');
  let delegHtml='';
  if(!isUser&&m.text){
    const mentions=(m.text.match(/@([A-Za-z]\w*)/g)||[]).map(x=>x.slice(1));
    const unique=[...new Set(mentions)].filter(n=>n!==m.from&&n.toLowerCase()!=='master');
    if(unique.length){
      delegHtml='<div class="cm-delegation">'+unique.map(n=>{
        const tgt=agentMap[n];
        return`<span class="cm-deleg-tag">${tgt?tgt.emoji+' ':''}${_e(n)}</span>`;
      }).join('')+'</div>';
    }
  }
  return`<div class="chat-msg ${cls}">
    <span class="cm-avatar">${_e(emoji)}</span>
    <div class="cm-body">
      <div class="cm-name">${_e(name)}</div>
      <div class="cm-text">${textHtml}</div>
      ${delegHtml}
      <div class="cm-time">${m.time||''}</div>
    </div>
  </div>`;
}

function renderChat(){
  const el=$('chat-area');if(!el)return;
  if(!chatMessages.length){el.innerHTML=`<div class="chat-empty">${_e(t('chat.empty'))}</div>`;return}
  const c=cos.find(x=>x.id===cur);
  const agentMap={};
  (c?.agents||[]).forEach(a=>{agentMap[a.name]=a});
  const msgs=chatMessages.slice(-50);

  // Group into threads: user msg → direct response → delegated responses
  // A thread starts with a user/master message, all following agent messages
  // until the next user message belong to the same thread
  const threads=[];
  let current=null;
  for(const m of msgs){
    const isUser=m.type==='user'||m.type==='master';
    if(isUser||!current){
      current={root:m,replies:[],id:threads.length};
      threads.push(current);
      if(!isUser)current.root=null,current.replies.push(m);
    }else{
      current.replies.push(m);
    }
  }

  let html='';
  threads.forEach(th=>{
    // Root message (user command or first orphan agent msg)
    if(th.root)html+=_renderMsg(th.root,agentMap);
    // Replies
    if(th.replies.length>0){
      const tid='thread-'+th.id;
      const collapsed=_threadCollapsed[tid]&&th.replies.length>2;
      const showCount=collapsed?1:th.replies.length;
      const hiddenCount=th.replies.length-showCount;
      if(th.replies.length>2){
        const label=collapsed?`▶ ${th.replies.length} replies`:`▼ collapse`;
        html+=`<button class="thread-toggle" onclick="_threadCollapsed['${tid}']=!_threadCollapsed['${tid}'];renderChat()">${label}</button>`;
      }
      html+='<div class="chat-thread">';
      const visible=collapsed?th.replies.slice(-1):th.replies;
      visible.forEach(m=>html+=_renderMsg(m,agentMap));
      html+='</div>';
    }
  });
  el.innerHTML=html;
  el.scrollTop=el.scrollHeight;
}

// ─── File Upload ───
async function uploadFile(input){
  if(!input.files.length||!cur)return;
  const file=input.files[0];
  const formData=new FormData();
  formData.append('file',file);
  toast(t('toast.upload_start')+' '+file.name);
  try{
    const r=await fetch(`/api/upload/${cur}`,{method:'POST',body:formData});
    const d=await r.json();
    if(d.ok){
      toast(t('toast.upload_success')+' '+file.name);
      // Send chat message with file reference
      const isImage=/\.(png|jpg|jpeg|gif|webp)$/i.test(file.name);
      const msg=isImage
        ?`[파일 첨부] ${file.name}\n이미지: /api/file/${cur}/${d.path}`
        :`[파일 첨부] ${file.name} (${(file.size/1024).toFixed(1)}KB)`;
      await fetch(`/api/chat/${cur}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:msg})});
      refresh();
    }else toast(t('toast.upload_fail'));
  }catch(e){toast(t('toast.upload_error'))}
  input.value='';
}

function showTaskDetail(taskId){
  document.querySelectorAll('.detail-popup').forEach(el=>el.remove());
  const t=(tasks||[]).find(x=>x.id===taskId);if(!t)return;
  const agent=t.agent_id?((cos.find(x=>x.id===cur)?.agents||[]).find(a=>a.id===t.agent_id)):null;
  const popup=document.createElement('div');
  popup.className='detail-popup';
  popup.innerHTML=`
    <button class="dp-close" onclick="this.parentElement.remove()">✕</button>
    <h4>${t.status==='완료'?'✅':'🔄'} ${_e(t.title)}</h4>
    ${agent?`<div style="font-size:10px;color:#a5b4fc;margin-bottom:6px">${agent.emoji||'🤖'} ${_e(agent.name)} · ${_e(agent.role||'')}</div>`:''}
    <div style="display:inline-block;padding:2px 8px;border-radius:8px;font-size:9px;font-weight:600;margin-bottom:8px;${t.status==='완료'?'background:#064e3b;color:#6ee7b7':t.status==='진행중'?'background:#1c1917;color:#fbbf24':'background:#1e293b;color:var(--dim)'}">${_e(t.status||'대기')}</div>
    <div class="dp-content">${_e(t.result||t.detail||t.description||window.t('popup.no_result'))}</div>
  `;
  document.body.appendChild(popup);
  const closer=e=>{if(!popup.contains(e.target)){popup.remove();document.removeEventListener('click',closer)}};
  setTimeout(()=>document.addEventListener('click',closer),100);
}

// ─── Command Bar ───
function directMsg(name){const i=$('cmd-input');i.value='@'+name+' ';i.focus()}
function _setCmdBarLock(locked,msg){
  const inp=$('cmd-input'),btn=$('cmd-send-btn');
  if(locked){
    if(inp){inp.disabled=true;inp.placeholder=msg||t('cmd.bar_locked');inp.style.opacity='.4'}
    if(btn){btn.disabled=true;btn.style.opacity='.4'}
  }else{
    if(inp){inp.disabled=false;inp.placeholder=t('cmd.placeholder');inp.style.opacity=''}
    if(btn){btn.disabled=false;btn.style.opacity=''}
  }
}
async function send(){
  const i=$('cmd-input'),txt=i.value.trim();if(!txt||!cur)return;
  i.value='';
  // Immediately show user message in chat
  chatMessages.push({type:'user',text:txt,time:now(),from:''});
  renderChat();
  try{await fetch(`/api/chat/${cur}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:txt})})}catch(e){toast(t('toast.send_fail'))}
}

// ─── Approvals ───
let _aprIdx=0,_aprSkipped=false;
function renderBanner(){
  const pend=approvals.filter(a=>a.status==='pending'&&(a.detail||a.title));
  const normalEl=$('cmd-normal'),aprEl=$('cmd-approval'),infoEl=$('apr-info'),commentEl=$('apr-comment');
  if(!pend.length||_aprSkipped){
    if(normalEl)normalEl.style.display='flex';
    if(aprEl)aprEl.style.display='none';
    return;
  }
  if(normalEl)normalEl.style.display='none';
  if(aprEl)aprEl.style.display='flex';
  _aprIdx=Math.max(0,Math.min(_aprIdx,pend.length-1));
  const a=pend[_aprIdx];
  if(infoEl)infoEl.innerHTML=`📋 ${_e((a.title||a.detail||t('approval.default')).substring(0,40))}<span class="apr-from">${_e(a.from_agent||'')} · ${pend.length>1?(_aprIdx+1)+'/'+pend.length:''}</span>`;
  if(commentEl)commentEl.value='';
}
function aprNav(dir){
  const pend=approvals.filter(a=>a.status==='pending'&&(a.detail||a.title));
  _aprIdx=(_aprIdx+dir+pend.length)%pend.length;
  renderBanner();
}
let _aprSkipTimer=null;
function aprSkip(){
  _aprSkipped=true;renderBanner();
  if(_aprSkipTimer)clearTimeout(_aprSkipTimer);
  _aprSkipTimer=setTimeout(()=>{_aprSkipped=false;_aprSkipTimer=null;renderBanner()},30000);
}
let _aprResolving=false;
async function aprResolve(res){
  if(_aprResolving)return; // prevent double-click race
  const pend=approvals.filter(a=>a.status==='pending'&&(a.detail||a.title));
  if(!pend.length)return;
  _aprResolving=true;
  const a=pend[_aprIdx];
  const comment=($('apr-comment')?.value||'').trim();
  try{
    const r=await fetch(`/api/approval-${res==='rejected'?'reject':'approve'}/${cur}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({approval_id:a.id,response:comment,resolution:res})});
    const d=await r.json();
    if(d.error){toast('❌ '+d.error);return}
    toast(res==='approved'?t('toast.approved'):t('toast.rejected'));
    approvals=approvals.filter(x=>x.id!==a.id);
    _aprIdx=0;
    renderBanner();updateAprBadge();
    if(curDrawer==='approvals'){
      const pend=approvals.filter(a=>a.status==='pending'&&(a.detail||a.title));
      if(!pend.length)closeDrawer(); else renderDrawerApprovals($('drawer-body'));
    }
    fetch(`/api/approvals/${cur}?status=pending`).then(r=>r.json()).then(d=>{approvals=d;renderBanner();updateAprBadge();if(curDrawer==='approvals'){if(!d.length)closeDrawer();else renderDrawerApprovals($('drawer-body'))}}).catch(()=>{});
  }catch(e){toast('❌ '+e.message)}
  finally{_aprResolving=false}
}
function resolve(id,res,comment){
  const idx=approvals.findIndex(a=>a.id===id);
  if(idx>=0){_aprIdx=idx;$('apr-comment').value=comment||'';aprResolve(res)}
}
function updateAprBadge(){
  const b=$('apr-badge');if(!b)return;
  const cnt=approvals.filter(a=>a.status==='pending').length;
  if(cnt>0){b.style.display='flex';b.textContent=cnt}else{b.style.display='none'}
}
function renderStats(){
  const s=$('hdr-stats');if(!s)return;let h='';
  if(costs&&costs.total_cost>0)h+=`<span style="background:#1c1917;padding:2px 6px;border-radius:8px">$${costs.total_cost.toFixed(4)}</span>`;
  if(tasks.length){const d=tasks.filter(t=>t.status==='완료').length;h+=` <span style="background:#0f2744;padding:2px 6px;border-radius:8px">${d}/${tasks.length}</span>`}
  s.innerHTML=h;
}

// ─── Side Drawer ───
let curDrawer=null;
function toggleDrawer(name){
  if(curDrawer===name){closeDrawer();return}
  curDrawer=name;
  document.querySelectorAll('.drawer-toggle button').forEach(b=>b.classList.remove('active'));
  const btn=$('dt-'+name);if(btn)btn.classList.add('active');
  const body=$('drawer-body'),title=$('drawer-title'),dl=$('dl-link');
  dl.style.display=name==='files'?'':'none';
  if(name==='tasks'){title.textContent=t('drawer.title_tasks');renderDrawerTasks(body)}
  else if(name==='approvals'){title.textContent=t('drawer.title_approvals');renderDrawerApprovals(body)}
  else if(name==='plan'){openPlan();return}
  else if(name==='files'){title.textContent=t('drawer.title_files');renderDrawerFiles(body)}
  else if(name==='comms'){title.textContent='💬 Agent Comms';renderDrawerComms(body)}
  $('drawer').classList.add('open');
}
function openDrawerWith(name,titleText,html){
  curDrawer=name;
  $('drawer-title').textContent=titleText;
  $('drawer-body').innerHTML=html;
  $('dl-link').style.display='none';
  $('drawer').classList.add('open');
}
function closeDrawer(){curDrawer=null;$('drawer').classList.remove('open');document.querySelectorAll('.drawer-toggle button').forEach(b=>b.classList.remove('active'))}

// Mobile menu: cycle through drawers
let _mobileMenuIdx=0;
const _mobileMenuItems=['tasks','approvals','files','comms'];
function toggleMobileMenu(){
  const name=_mobileMenuItems[_mobileMenuIdx%_mobileMenuItems.length];
  _mobileMenuIdx++;
  if(curDrawer===name){closeDrawer();return}
  toggleDrawer(name);
}

function renderDrawerTasks(el){
  if(!tasks.length){el.innerHTML=`<div style="color:var(--dim);text-align:center;padding:20px;font-size:11px">${_e(t('drawer.no_tasks'))}</div>`;return}
  el.innerHTML=tasks.map(t=>{const cls=t.status==='완료'?'done':t.status==='진행중'?'progress':'';
    return`<div class="task-item ${cls}"><span>${t.status==='완료'?'✅':t.status==='진행중'?'🔄':'⬜'}</span><span style="flex:1">${_e(t.title)}</span><span style="color:var(--dim);font-size:8px">${_e(t.agent_id||'')}</span></div>`}).join('');
}
function renderDrawerApprovals(el){
  const pend=approvals.filter(a=>a.status==='pending'&&(a.detail||a.title));
  if(!pend.length){el.innerHTML=`<div style="color:var(--dim);text-align:center;padding:20px;font-size:11px">${_e(t('drawer.no_approvals'))}</div>`;return}
  el.innerHTML=pend.map(a=>`<div class="appr-card">
    <div style="font-size:11px;font-weight:600;color:var(--text)">${_e(a.title||a.approval_type||t('approval.default'))}</div>
    <div style="font-size:9px;color:var(--dim);margin:2px 0">${_e(a.from_agent||'')} · ${a.time||''}</div>
    <div style="font-size:10px;color:var(--text);white-space:pre-wrap;max-height:80px;overflow-y:auto;margin:4px 0">${_e((a.detail||'').substring(0,300))}</div>
    <input id="apr-comment-${a.id}" placeholder="${_e(t('approval.comment_ph'))}" style="width:100%;background:#0f172a;border:1px solid #334155;border-radius:6px;padding:5px 8px;color:var(--text);font-size:10px;outline:none;margin:4px 0;box-sizing:border-box">
    <div style="display:flex;gap:4px">
      <button onclick="resolve('${a.id}','approved',document.getElementById('apr-comment-${a.id}')?.value)" style="background:#064e3b;color:#6ee7b7">✅ 승인</button>
      <button onclick="resolve('${a.id}','rejected',document.getElementById('apr-comment-${a.id}')?.value)" style="background:#7f1d1d;color:#fca5a5">❌ 반려</button>
    </div>
  </div>`).join('');
}
function renderDrawerFiles(el){
  fetch(`/api/deliverables/${cur}`).then(r=>r.json()).then(files=>{
    if(!files.length){el.innerHTML=`<div style="color:var(--dim);text-align:center;padding:20px;font-size:11px">${_e(t('drawer.no_files'))}</div>`;return}
    el.innerHTML=files.slice(0,30).map(f=>{
      const ext=f.path.split('.').pop().toLowerCase();
      const isImg=['png','jpg','jpeg','gif','webp','svg'].includes(ext);
      const isText=['md','txt','json','csv','html','css','js','py'].includes(ext);
      const icon=isImg?'🖼️':ext==='md'?'📝':ext==='json'?'📋':ext==='py'?'🐍':'📄';
      const url=`/api/file/${cur}/${f.path}`;
      let preview='';
      if(isImg)preview=`<img src="${url}" style="max-width:100%;max-height:120px;border-radius:4px;margin-top:4px;display:block;cursor:pointer" onclick="event.stopPropagation();window.open('${url}','_blank')">`;
      const previewBtn=isText?`<button onclick="event.stopPropagation();previewFile('${url}','${ext}')" style="background:none;border:1px solid #374151;color:var(--dim);border-radius:4px;padding:1px 5px;font-size:8px;cursor:pointer;flex-shrink:0">preview</button>`:'';
      return`<div class="file-item" style="flex-direction:column;align-items:flex-start" onclick="window.open('${url}','_blank')">
        <div style="display:flex;align-items:center;gap:6px;width:100%"><span>${icon}</span><span style="flex:1;color:#60a5fa;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${_e(f.path)}</span>${previewBtn}<span style="color:var(--dim);font-size:8px">${f.modified||''}</span></div>
        ${preview}
      </div>`}).join('');
  }).catch(()=>{el.innerHTML=`<div style="color:var(--dim);font-size:10px">${_e(t('drawer.load_fail'))}</div>`});
  fetch(`/api/newspaper/${cur}`).then(r=>r.json()).then(d=>{
    if(d.newspaper)el.innerHTML+=`<div style="margin-top:12px;font-size:10px;font-weight:600;color:var(--dim);margin-bottom:4px">${_e(t('drawer.report'))}</div><div style="font-size:10px;color:var(--text);white-space:pre-wrap;line-height:1.5;background:var(--card);border-radius:6px;padding:8px">${_e(d.newspaper)}</div>`;
  }).catch(()=>{});
}

async function previewFile(url,ext){
  try{
    const r=await fetch(url);
    const raw=await r.text();
    document.querySelectorAll('.detail-popup').forEach(el=>el.remove());
    const popup=document.createElement('div');
    popup.className='detail-popup';
    popup.style.width='500px';popup.style.maxHeight='70vh';
    let body='';
    if(ext==='md'){
      body=_md(raw);
    }else if(ext==='json'){
      try{body=`<pre style="font-size:10px;overflow:auto;background:#0f172a;padding:8px;border-radius:6px">${_e(JSON.stringify(JSON.parse(raw),null,2))}</pre>`}
      catch(e){body=`<pre style="font-size:10px">${_e(raw)}</pre>`}
    }else{
      body=`<pre style="font-size:10px;overflow:auto;background:#0f172a;padding:8px;border-radius:6px">${_e(raw.substring(0,5000))}</pre>`;
    }
    popup.innerHTML=`<button class="dp-close" onclick="this.parentElement.remove()">✕</button><div style="font-size:11px;line-height:1.6;color:var(--text);overflow-y:auto;max-height:60vh">${body}</div>`;
    document.body.appendChild(popup);
    const closer=e=>{if(!popup.contains(e.target)){popup.remove();document.removeEventListener('click',closer)}};
    setTimeout(()=>document.addEventListener('click',closer),100);
  }catch(e){toast(t('drawer.load_fail'))}
}

// ─── Multi-Company Dashboard ───
function openDashboard(){
  $('dash-overlay').classList.add('show');
  renderDashboard();
}
function closeDashboard(){$('dash-overlay').classList.remove('show')}
async function renderDashboard(){
  const el=$('dash-content');if(!el)return;
  if(!cos.length){el.innerHTML='<div style="color:var(--dim);text-align:center;padding:40px">No companies</div>';return}
  // Fetch costs for each company
  const data=await Promise.all(cos.map(async c=>{
    let cost={total_cost:0},taskCount=0,doneCount=0;
    try{cost=await(await fetch(`/api/costs/${c.id}`)).json()}catch(e){}
    try{const ts=await(await fetch(`/api/board-tasks/${c.id}`)).json();taskCount=ts.length;doneCount=ts.filter(x=>x.status==='완료').length}catch(e){}
    return{...c,cost,taskCount,doneCount};
  }));
  el.innerHTML=`<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px">`+
    data.map(c=>{
      const agents=c.agents||[];
      const pct=c.taskCount?Math.round(c.doneCount/c.taskCount*100):0;
      const costVal=c.cost?.total_cost||0;
      return`<div style="background:var(--card);border-radius:10px;padding:14px;border:1px solid var(--border);cursor:pointer" onclick="closeDashboard();sel('${c.id}')">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
          <span style="font-size:20px">🏢</span>
          <div>
            <div style="font-size:13px;font-weight:700;color:#f1f5f9">${_e(c.name)}</div>
            <div style="font-size:9px;color:var(--dim)">${_e(c.topic||'')}</div>
          </div>
        </div>
        <div style="display:flex;gap:12px;margin-bottom:8px">
          <div style="text-align:center">
            <div style="font-size:16px;font-weight:800;color:var(--accent)">${agents.length}</div>
            <div style="font-size:8px;color:var(--dim)">Agents</div>
          </div>
          <div style="text-align:center">
            <div style="font-size:16px;font-weight:800;color:var(--green)">${c.doneCount}/${c.taskCount}</div>
            <div style="font-size:8px;color:var(--dim)">Tasks</div>
          </div>
          <div style="text-align:center">
            <div style="font-size:16px;font-weight:800;color:var(--yellow)">$${costVal.toFixed(3)}</div>
            <div style="font-size:8px;color:var(--dim)">Cost</div>
          </div>
        </div>
        <div style="height:4px;background:#0f172a;border-radius:2px;overflow:hidden">
          <div style="height:100%;width:${pct}%;background:linear-gradient(90deg,var(--accent),var(--green));border-radius:2px"></div>
        </div>
        <div style="display:flex;gap:3px;margin-top:8px;flex-wrap:wrap">
          ${agents.map(a=>`<span style="font-size:14px" title="${_e(a.name)}">${_e(a.emoji||'🤖')}</span>`).join('')}
        </div>
      </div>`;
    }).join('')+'</div>';
}

// ─── Agent Comms (drawer) ───
function renderDrawerComms(el){
  if(!chatMessages.length){el.innerHTML='<div style="color:var(--dim);text-align:center;padding:20px;font-size:11px">No agent communications yet</div>';return}
  const c=cos.find(x=>x.id===cur);
  const agentMap={};
  (c?.agents||[]).forEach(a=>{agentMap[a.name]=a});
  // Build conversation pairs: request(@mention) → response(from mentioned agent)
  const agentMsgs=chatMessages.filter(m=>m.type==='agent'&&m.from);
  const pairs=[];
  for(let i=0;i<agentMsgs.length;i++){
    const m=agentMsgs[i];
    const mentions=(m.text||'').match(/@([A-Za-z]\w*)/g)||[];
    const targets=[...new Set(mentions.map(x=>x.slice(1)))].filter(n=>agentMap[n]&&n!==m.from);
    if(!targets.length)continue;
    // Find the response: next message FROM one of the targets
    let response=null;
    for(let j=i+1;j<Math.min(i+10,agentMsgs.length);j++){
      if(targets.includes(agentMsgs[j].from)){response=agentMsgs[j];break}
    }
    pairs.push({request:m,targets,response});
  }
  if(!pairs.length){el.innerHTML='<div style="color:var(--dim);text-align:center;padding:20px;font-size:11px">No agent-to-agent conversations yet</div>';return}
  el.innerHTML=pairs.slice(-20).map(p=>{
    const fromAg=agentMap[p.request.from];
    const arrow=p.targets.map(n=>{const ag=agentMap[n];return`${ag?ag.emoji:''} ${n}`}).join(', ');
    const reqSnip=_e((p.request.text||'').replace(/@\w+/g,'').trim().substring(0,100));
    let html=`<div style="padding:8px;border-bottom:1px solid #1e293b">
      <div style="display:flex;align-items:center;gap:4px;margin-bottom:4px">
        <span style="font-size:14px">${fromAg?fromAg.emoji:'🤖'}</span>
        <span style="font-weight:700;color:#a5b4fc;font-size:10px">${_e(p.request.from)}</span>
        <span style="color:var(--dim);font-size:10px">→</span>
        <span style="color:#c4b5fd;font-size:10px">${arrow}</span>
        <span style="margin-left:auto;color:var(--dim);font-size:8px">${p.request.time||''}</span>
      </div>
      <div style="font-size:10px;color:#94a3b8;line-height:1.4;padding:4px 0 4px 20px;border-left:2px solid rgba(139,92,246,.3)">${reqSnip}...</div>`;
    if(p.response){
      const respAg=agentMap[p.response.from];
      const respSnip=_e((p.response.text||'').replace(/@\w+/g,'').trim().substring(0,100));
      html+=`<div style="display:flex;align-items:center;gap:4px;margin:4px 0 2px 20px">
        <span style="color:var(--dim);font-size:9px">↩</span>
        <span style="font-size:12px">${respAg?respAg.emoji:'🤖'}</span>
        <span style="font-weight:600;color:var(--green);font-size:10px">${_e(p.response.from)}</span>
        <span style="color:var(--dim);font-size:8px">${p.response.time||''}</span>
      </div>
      <div style="font-size:10px;color:var(--text);line-height:1.4;padding:4px 0 4px 20px;border-left:2px solid rgba(0,255,136,.3)">${respSnip}...</div>`;
    }else{
      html+=`<div style="font-size:9px;color:var(--dim);padding:2px 0 0 20px">⏳ awaiting response...</div>`;
    }
    html+=`</div>`;
    return html;
  }).join('');
}

// ─── Plan (Visual Overlay) ───
let _planTasks=[],_planAddPar=null,_planCollapsed=new Set(),_planUserExpanded=new Set();

// Category detection (reused from old fog map logic)
// Order matters: more specific first; 'plan' is the fallback.
// Keywords stay as-is (content detection); labels come from i18n at render time.
const PLAN_CAT={
  design:{labelKey:'cat.design',icon:'🎨',color:'#ec4899',kw:['디자인','UI','UX','시안','로고','이미지','design','logo','wireframe','mockup','프로토']},
  market:{labelKey:'cat.market',icon:'📢',color:'#22c55e',kw:['마케팅','홍보','SNS','광고','SEO','콘텐츠','브랜딩','marketing','branding','content','ads','seo','고객','캠페인']},
  dev:{labelKey:'cat.dev',icon:'💻',color:'#3b82f6',kw:['코딩','개발','API','서버','프론트','백엔드','DB','배포','frontend','backend','deploy','server','database','coding','api','버그','테스트','구현']},
  ops:{labelKey:'cat.ops',icon:'⚙️',color:'#f59e0b',kw:['운영','인사','재무','예산','비용','법률','채용','HR','finance','budget','legal','policy','급여']},
  plan:{labelKey:'cat.plan',icon:'📊',color:'#8b5cf6',kw:['기획','전략','분석','리서치','조사','계획','planning','strategy','research','analysis','보고','정리']},
};
function _detectCat(title){
  if(!title)return'plan';
  const t=title.toLowerCase();
  for(const[cat,m]of Object.entries(PLAN_CAT)){
    if(m.kw.some(k=>t.includes(k.toLowerCase())))return cat;
  }
  return'plan';
}

function openPlan(){
  $('plan-overlay').classList.add('show');
  fetchPlanTasks();
}
function closePlan(){$('plan-overlay').classList.remove('show')}

async function fetchPlanTasks(){
  if(!cur)return;
  try{const r=await fetch(`/api/plan-tasks/${cur}`);_planTasks=await r.json();renderPlan()}catch(e){}
}

function renderPlan(){
  const co=cos.find(c=>c.id===cur);
  const agents=(co?.agents||[]);

  // Merge board tasks into view (as virtual plan items under categories)
  const boardItems=(tasks||[]).map(t=>({
    id:'board-'+t.id, title:t.title, agent_id:t.agent_id,
    status:t.status==='완료'?'done':t.status==='진행중'?'in-progress':'todo',
    parent_id:null, _board:true, _boardId:t.id, result:t.result||t.detail||''
  }));

  // Combined: plan tasks + board tasks (dedup by title+agent to avoid hiding same-named tasks from different agents)
  const planKeys=new Set(_planTasks.map(t=>(t.title||'')+'|'+(t.agent_id||'')));
  const extraBoard=boardItems.filter(b=>!planKeys.has((b.title||'')+'|'+(b.agent_id||'')));
  const allItems=[..._planTasks,...extraBoard];

  const total=allItems.length;
  const done=allItems.filter(t=>t.status==='done').length;
  const progress=allItems.filter(t=>t.status==='in-progress').length;
  const pct=total?Math.round(done/total*100):0;
  const r=28,circ=2*Math.PI*r;

  // Summary
  $('plan-summary').innerHTML=`
    <div class="ps-ring">
      <svg viewBox="0 0 64 64"><circle cx="32" cy="32" r="${r}" fill="none" stroke="#1e293b" stroke-width="5"/>
      <circle cx="32" cy="32" r="${r}" fill="none" stroke="url(#pg)" stroke-width="5" stroke-dasharray="${circ}" stroke-dashoffset="${circ-(circ*pct/100)}" stroke-linecap="round"/>
      <defs><linearGradient id="pg" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#3b82f6"/><stop offset="100%" stop-color="#22c55e"/></linearGradient></defs></svg>
      <span class="ps-ring-pct">${pct}%</span>
    </div>
    <div class="ps-stat"><span class="ps-num" style="color:var(--text)">${total}</span><span class="ps-label">${_e(t('plan.total'))}</span></div>
    <div class="ps-stat"><span class="ps-num" style="color:var(--green)">${done}</span><span class="ps-label">${_e(t('plan.done'))}</span></div>
    <div class="ps-stat"><span class="ps-num" style="color:var(--yellow)">${progress}</span><span class="ps-label">${_e(t('plan.in_progress'))}</span></div>
    <div class="ps-stat"><span class="ps-num" style="color:var(--dim)">${total-done-progress}</span><span class="ps-label">${_e(t('plan.waiting'))}</span></div>
  `;

  // Agent progress bars
  const agentStats={};
  allItems.forEach(t=>{
    const aid=t.agent_id||'_none';
    if(!agentStats[aid])agentStats[aid]={total:0,done:0};
    agentStats[aid].total++;
    if(t.status==='done')agentStats[aid].done++;
  });
  $('plan-agents').innerHTML=Object.entries(agentStats).map(([aid,s])=>{
    const ag=agents.find(a=>a.id===aid);
    const name=ag?`${ag.emoji||''} ${ag.name}`:(aid==='_none'?t('plan.unassigned'):aid);
    const p=s.total?Math.round(s.done/s.total*100):0;
    const color=p===100?'var(--green)':p>0?'var(--accent)':'#374151';
    return`<div class="pa-bar"><div class="pa-name"><span>${name}</span><span style="margin-left:auto;font-size:8px;color:${color}">${s.done}/${s.total}</span></div><div class="pa-track"><div class="pa-fill" style="width:${p}%;background:${color}"></div></div></div>`;
  }).join('');

  // Body: group by category
  const body=$('plan-body');
  if(!allItems.length){
    body.innerHTML=`<div style="text-align:center;padding:40px;color:var(--dim)">
      <div style="font-size:32px;margin-bottom:8px">📋</div>
      <div style="font-size:13px;margin-bottom:4px">${_e(t('plan.no_plan'))}</div>
      <div style="font-size:11px">${_e(t('plan.auto_hint'))}</div>
    </div>
    <div class="plan-add-top"><input id="pai-root" placeholder="${_e(t('plan.new_placeholder'))}" onkeydown="if(event.key==='Enter')planAddSubmit('')"><button onclick="planAddSubmit('')">${_e(t('plan.add'))}</button></div>`;
    return;
  }

  // Categorize root-level items
  const catGroups={};
  // First: plan tree roots
  const planRoots=_planTasks.filter(t=>!t.parent_id);
  planRoots.forEach(t=>{
    const cat=_detectCat(t.title);
    if(!catGroups[cat])catGroups[cat]=[];
    catGroups[cat].push(t);
  });
  // Then: board tasks not in plan
  extraBoard.forEach(t=>{
    const cat=_detectCat(t.title);
    if(!catGroups[cat])catGroups[cat]=[];
    catGroups[cat].push(t);
  });

  let h=`<div class="plan-add-top"><input id="pai-root" placeholder="${_e(t('plan.new_placeholder'))}" onkeydown="if(event.key==='Enter')planAddSubmit('')"><button onclick="planAddSubmit('')">${_e(t('plan.add'))}</button></div>`;

  // Render each category
  for(const[cat,items]of Object.entries(catGroups)){
    const meta=PLAN_CAT[cat]||PLAN_CAT.plan;
    const catDone=items.filter(i=>i.status==='done').length;
    const catTotal=items.length;
    const catPct=catTotal?Math.round(catDone/catTotal*100):0;
    const catId='cat-'+cat;
    const allDone=catDone===catTotal&&catTotal>0;
    // Auto-collapse done categories unless user explicitly expanded
    const collapsed=allDone&&!_planUserExpanded.has(catId)?true:_planCollapsed.has(catId);

    h+=`<div style="margin-bottom:12px">`;
    h+=`<div style="display:flex;align-items:center;gap:8px;padding:6px 8px;cursor:pointer;border-radius:8px;background:${allDone?'rgba(0,255,136,.05)':'rgba('+_hexToRgb(meta.color)+',.08)'};border:1px solid ${allDone?'rgba(0,255,136,.15)':'rgba('+_hexToRgb(meta.color)+',.2)'}" onclick="planToggleCat('${catId}')">`;
    h+=`<span style="font-size:14px">${meta.icon}</span>`;
    h+=`<span style="font-size:12px;font-weight:700;color:${allDone?'var(--green)':meta.color};flex:1">${_e(t(meta.labelKey))}</span>`;
    h+=`<span style="font-size:9px;color:var(--dim)">${catDone}/${catTotal}</span>`;
    h+=`<div style="width:50px;height:4px;background:#0f172a;border-radius:2px;overflow:hidden"><div style="height:100%;width:${catPct}%;background:${allDone?'var(--green)':meta.color};border-radius:2px"></div></div>`;
    h+=`<span style="font-size:10px;color:var(--dim)">${collapsed?'▶':'▼'}</span>`;
    h+=`</div>`;

    if(!collapsed){
      h+=`<div style="padding-left:12px;margin-top:4px">`;
      items.forEach(t=>{h+=_buildNode(t,co,allItems)});
      h+=`</div>`;
    }
    h+=`</div>`;
  }

  body.innerHTML=h;
}

function _hexToRgb(hex){
  const m=hex.match(/^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i);
  return m?`${parseInt(m[1],16)},${parseInt(m[2],16)},${parseInt(m[3],16)}`:'59,130,246';
}

function planToggleCat(catId){
  // Track user intent for done categories
  if(_planCollapsed.has(catId)){_planCollapsed.delete(catId);_planUserExpanded.add(catId)}
  else{_planCollapsed.add(catId);_planUserExpanded.delete(catId)}
  renderPlan();
}

function _buildNode(t,co,allItems){
  const s=t.status||'todo';
  const kids=(allItems||_planTasks).filter(c=>c.parent_id===t.id);
  const hasKids=kids.length>0;
  // Auto-collapse done items with children, unless user expanded
  const isDone=s==='done';
  const collapsed=(isDone&&hasKids&&!_planUserExpanded.has(t.id))?true:_planCollapsed.has(t.id);
  const chkCls=s==='done'?'c-done':s==='in-progress'?'c-progress':s==='blocked'?'c-blocked':'';
  const chkIcon=s==='done'?'✓':s==='in-progress'?'↻':s==='blocked'?'!':'';
  const titleCls=s==='done'?'t-done':s==='in-progress'?'t-progress':'';
  const ag=co?(co.agents||[]).find(a=>a.id===t.agent_id):null;
  const agTag=ag?`<span class="pn-agent">${ag.emoji||''} ${ag.name}</span>`:(t.agent_id?`<span class="pn-agent">${t.agent_id}</span>`:'');
  const isBoard=!!t._board;

  let h=`<div class="pn">`;
  h+=`<div class="pn-card"${isDone?' style="opacity:.6"':''}>`;
  h+=`<span class="pn-toggle ${hasKids?(collapsed?'':'rotated'):'empty'}" onclick="event.stopPropagation();planToggle('${t.id}')">${hasKids?'▶':''}</span>`;
  if(!isBoard){
    h+=`<div class="pn-chk ${chkCls}" onclick="event.stopPropagation();planCycle('${t.id}')" title="${_e(window.t('plan.cycle_status'))}">${chkIcon}</div>`;
  }else{
    h+=`<div class="pn-chk ${chkCls}" style="cursor:default">${chkIcon}</div>`;
  }
  h+=`<span class="pn-title ${titleCls}">${_e(t.title)}</span>`;
  h+=agTag;
  if(!isBoard){
    h+=`<div class="pn-acts" onclick="event.stopPropagation()"><button onclick="planAddStart('${t.id}')">＋ 하위</button><button onclick="planDel('${t.id}')">✕</button></div>`;
  }
  h+=`</div>`;

  if(hasKids){
    const doneKids=kids.filter(k=>k.status==='done').length;
    const p=Math.round(doneKids/kids.length*100);
    const color=p===100?'var(--green)':p>0?'linear-gradient(90deg,#3b82f6,#60a5fa)':'#1e293b';
    h+=`<div class="pn-bar"><div class="pn-bar-fill" style="width:${p}%;background:${color}"></div></div>`;
  }

  if(_planAddPar===t.id){
    const opts=co?(co.agents||[]).map(a=>`<option value="${_e(a.id)}">${a.emoji} ${_e(a.name)}</option>`).join(''):'';
    h+=`<div class="pn-add"><input id="pai-${t.id}" placeholder="${_e(window.t('plan.sub_ph'))}" onkeydown="if(event.key==='Enter')planAddSubmit('${t.id}');if(event.key==='Escape')planAddCancel()"><select id="paa-${t.id}"><option value="">${_e(window.t('plan.assignee'))}</option>${opts}</select><button onclick="planAddSubmit('${t.id}')" style="background:var(--accent);color:white">↵</button><button onclick="planAddCancel()" style="background:var(--card);color:var(--dim)">✕</button></div>`;
  }

  if(hasKids&&!collapsed){
    h+=`<div class="pn-kids">`;
    kids.forEach(k=>{h+=_buildNode(k,co,allItems)});
    h+=`</div>`;
  }
  h+=`</div>`;
  return h;
}

function planToggle(id){
  if(_planCollapsed.has(id)){_planCollapsed.delete(id);_planUserExpanded.add(id)}
  else{_planCollapsed.add(id);_planUserExpanded.delete(id)}
  renderPlan();
}
function planAddStart(pid){_planAddPar=pid||'root';renderPlan();setTimeout(()=>{const el=document.getElementById('pai-'+(pid||'root'));if(el)el.focus()},50)}
function planAddCancel(){_planAddPar=null;renderPlan()}
async function planAddSubmit(pid){
  const k=pid||'root',inp=document.getElementById('pai-'+k),sel=document.getElementById('paa-'+k);
  const title=(inp?.value||'').trim();if(!title){planAddCancel();return}
  _planAddPar=null;
  try{const r=await fetch(`/api/plan-task-add/${cur}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title,parent_id:pid||null,agent_id:sel?.value||'',status:'todo'})});
    const d=await r.json();if(d.ok){_planTasks.push(d.task);renderPlan()}else toast(t('toast.add_fail'))}catch(e){toast(t('toast.generic_error'))}
}
async function planCycle(id){
  const node=_planTasks.find(x=>x.id===id);if(!node)return;
  const nxt={todo:'in-progress','in-progress':'done',done:'todo',blocked:'todo'};
  node.status=nxt[node.status]||'todo';renderPlan();
  try{await fetch(`/api/plan-task-update/${cur}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id,status:t.status})})}catch(e){}
}
async function planDel(id){
  const node=_planTasks.find(x=>x.id===id);
  if(!node||!confirm(t('plan.delete_confirm',{title:node.title})))return;
  try{const r=await fetch(`/api/plan-task-delete/${cur}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id})});
    const d=await r.json();if(d.ok){const rm=new Set();const col=i=>{rm.add(i);_planTasks.filter(x=>x.parent_id===i).forEach(x=>col(x.id))};col(id);_planTasks=_planTasks.filter(x=>!rm.has(x.id));renderPlan()}else toast(t('toast.delete_fail'))}catch(e){toast(t('toast.delete_error'))}
}

// ─── CRUD ───
function openCreate(){$('create-modal').classList.add('show')}
async function createCo(){
  const coName=$('c-name').value.trim(),coTopic=$('c-topic').value.trim();if(!coName)return;
  const btn=document.querySelector('#create-modal .btn-primary');
  if(btn){btn.disabled=true;btn.textContent='⏳ CEO...'}
  const coLang=(cos.length&&cos[0].lang)?cos[0].lang:lang;
  try{
    const r=await(await fetch('/api/companies',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:coName,topic:coTopic,lang:coLang})})).json();
    if(r.ok){
      $('create-modal').classList.remove('show');$('c-name').value='';$('c-topic').value='';
      const agentCount=(r.company.agents||[]).length;
      cur=r.company.id;load();
      _setCmdBarLock(true,'⏳ CEO...');
      toast(`🏢 ${coName}`);
      _watchAgentReady(r.company.id,agentCount);
    }else toast(t('toast.create_fail'));
  }catch(e){
    toast(t('toast.create_error'));
  }finally{
    // ALWAYS restore button — never leave it permanently locked
    if(btn){btn.disabled=false;btn.textContent=t('create_button_label')}
  }
}
function _watchAgentReady(cid,total){
  let _interval=setInterval(async()=>{
    try{
      const c=await(await fetch(`/api/company/${cid}`)).json();
      if(!c||!c.agents){clearInterval(_interval);return}
      const ready=c.agents.filter(a=>a.status==='active').length;
      const registering=c.agents.filter(a=>a.status==='registering');
      if(ready>=total){
        clearInterval(_interval);
        toast(t('toast.all_ready'));
        _setCmdBarLock(false);
        renderIconGrid();
      }else{
        const names=registering.map(a=>a.emoji+' '+a.name).join(', ');
        toast(`⏳ ${ready}/${total} — ${names}`);
      }
    }catch(e){clearInterval(_interval)}
  },5000);
  setTimeout(()=>{clearInterval(_interval);_setCmdBarLock(false)},180000);
}
async function delCo(id,name){
  if(!confirm(`"${name}" — ${t('toast.delete_confirm')}`))return;
  toast('🗑️...');
  try{
    const r=await fetch('/api/company/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id})});
    const d=await r.json();
    if(d.ok){toast(t('toast.delete_success'));cur=null;load()}
    else toast(t('toast.delete_fail'));
  }catch(e){toast(t('toast.delete_error'))}
}
function fp(n,r,e){$('a-name').value=n;$('a-role').value=r;$('a-emoji').value=e}
function fpKey(n,roleKey,e){fp(n,t(roleKey),e)}
async function openAgent(){
  const s=$('a-parent'),c=cos.find(x=>x.id===cur);s.innerHTML='<option value="">리더 직속</option>';
  if(c)(c.agents||[]).forEach(a=>s.innerHTML+=`<option value="${_e(a.id)}">${a.emoji} ${_e(a.name)}</option>`);
  const m=$('a-model');m.innerHTML='<option value="">기본</option>';
  try{const r=await(await fetch('/api/models')).json();(r.models||[]).forEach(x=>m.innerHTML+=`<option value="${x.id}">${x.name}</option>`)}catch(e){}
  $('agent-modal').classList.add('show');
}
async function addAgent(){
  const n=$('a-name').value.trim(),r=$('a-role').value.trim();if(!n||!r)return alert(t('modal.agent_validation'));
  toast('⏳ '+n);
  try{const res=await(await fetch(`/api/agent-add/${cur}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:n,role:r,emoji:$('a-emoji').value,parent_agent:$('a-parent').value,model:$('a-model').value})})).json();
    if(res.ok){toast(t('toast.agent_added',{name:n}));$('agent-modal').classList.remove('show');refresh()}}catch(e){toast(t('toast.generic_error'))}
}
function dl(){if(cur)window.open('/api/download/'+cur)}

// ─── Outsourcing ───
function openOutsource(){
  if(!cur){toast(t('toast.need_company'));return}
  if(cos.length<2){toast(t('toast.need_second_company'));return}
  const sel=$('os-company');
  sel.innerHTML=cos.filter(c=>c.id!==cur).map(c=>`<option value="${c.id}">${_e(c.name)}</option>`).join('');
  sel.onchange=()=>{
    const tid=sel.value;const tc=cos.find(c=>c.id===tid);
    const as=$('os-agent');as.innerHTML='<option value="">리더 자동선택</option>';
    if(tc)(tc.agents||[]).forEach(a=>as.innerHTML+=`<option value="${_e(a.id)}">${a.emoji} ${_e(a.name)} (${_e(a.role||'')})</option>`);
  };
  sel.onchange();
  $('os-text').value='';
  $('outsource-modal').classList.add('show');
}
async function sendOutsource(){
  const to_cid=$('os-company').value;
  const to_agent=$('os-agent').value;
  const text=$('os-text').value.trim();
  if(!text){toast(t('toast.enter_request'));return}
  try{
    const r=await(await fetch('/api/cross-nudge',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({from_cid:cur,to_cid,from_agent:'',to_agent,text})})).json();
    if(r.ok){toast(`${t('toast.outsource_sent')}: ${r.to}`);$('outsource-modal').classList.remove('show')}
    else toast('❌ '+(r.error||''))
  }catch(e){toast('❌ '+e.message)}
}

// ─── Toast ───
function toast(m){const el=document.createElement('div');el.className='toast';el.textContent=m;let s=$('toasts');if(!s){s=document.createElement('div');s.id='toasts';s.className='toasts';document.body.appendChild(s)}s.appendChild(el);setTimeout(()=>el.remove(),3000)}

// ─── Search ───
let _st=null;
function debounceSearch(q){clearTimeout(_st);if(!q||q.length<2){$('search-results').style.display='none';return}_st=setTimeout(()=>doSearch(q),350)}
async function doSearch(q){
  const el=$('search-results');el.style.display='block';el.innerHTML='<div style="padding:8px;font-size:10px;color:var(--dim)">검색 중...</div>';
  try{const r=await(await fetch(`/api/search?q=${encodeURIComponent(q)}`)).json();
    if(!r.length){el.innerHTML='<div style="padding:8px;font-size:10px;color:var(--dim)">결과 없음</div>';return}
    el.innerHTML=r.slice(0,8).map(x=>`<div style="padding:6px 10px;border-bottom:1px solid #1e293b;cursor:pointer;font-size:10px" onclick="sel('${x.company_id}');$('search-results').style.display='none'"><span style="color:#60a5fa">${_e(x.from||'')}</span> ${_e((x.text||'').substring(0,60))}</div>`).join('')}
  catch(e){el.innerHTML='<div style="padding:8px;color:var(--red);font-size:10px">오류</div>'}
}
document.addEventListener('click',e=>{if(!e.target.closest('#search')&&!e.target.closest('#search-results'))$('search-results').style.display='none'});

// ─── SSE ───
let sseTimer=null;
let _sseInstance=null;
function connectSSE(){
  if(sseTimer)clearTimeout(sseTimer);
  if(_sseInstance){try{_sseInstance.close()}catch(e){}} // cleanup previous
  const es=new EventSource('/api/sse');
  _sseInstance=es;
  es.addEventListener('init',e=>{try{cos=JSON.parse(e.data);renderTabs()}catch(e){}});
  es.addEventListener('agent_thinking',e=>{try{const d=JSON.parse(e.data);
    thinking[d.cid+':'+d.agent_id]={};
    if(d.cid===cur){renderIconGrid()}
  }catch(e){}});
  es.addEventListener('agent_done',e=>{try{const d=JSON.parse(e.data);
    notify(t('notif.done_title'),(d.agent_name||d.agent_id));
    delete thinking[d.cid+':'+d.agent_id];
    if(d.cid===cur){
      renderIconGrid();
      extras();
      if(curDrawer==='tasks')renderDrawerTasks($('drawer-body'));
      if(curDrawer==='plan')fetchPlanTasks();
    }
  }catch(e){}});
  es.addEventListener('approval',e=>{try{notify(t('notif.approval_title'),t('notif.approval_body'));if(cur)fetch(`/api/approvals/${cur}?status=pending`).then(r=>r.json()).then(d=>{approvals=d;renderBanner();updateAprBadge()}).catch(()=>{})}catch(e){}});
  es.addEventListener('company_update',e=>{try{const d=JSON.parse(e.data);
    if(d.deleted){cos=cos.filter(c=>c.id!==d.id);renderTabs();if(cur===d.id){cur=cos.length?cos[0].id:null;cur?refresh():load()}return}
    if(!d.company||!d.id)return;const i=cos.findIndex(c=>c.id===d.id);if(i>=0)cos[i]=d.company;else cos.push(d.company);renderTabs();
    if(d.id!==cur)return;
    const c=d.company,chat=c.chat||[];
    chat.slice(lastLen).forEach(m=>{
      if(m.type==='agent'&&m.from){
        const ag=(c.agents||[]).find(a=>a.name===m.from);
        if(ag)pushSpeech(ag.id,m.text||'',m.from,ag.emoji||'🤖',m.time||'');
        if((m.text||'').includes('외주')||(m.text||'').includes('outsourc'))notify(t('notif.outsource_title'),m.from+': '+(m.text||'').substring(0,60));
      }
    });
    chatMessages=chat;
    lastLen=chat.length;
    renderIconGrid();renderChat();
  }catch(e){}});
  es.onerror=()=>{es.close();_sseBackoff=Math.min((_sseBackoff||3)*2,60);sseTimer=setTimeout(connectSSE,_sseBackoff*1000)};
  es.onopen=()=>{_sseBackoff=3}; // reset on successful connection
}

// ─── Init ───
document.addEventListener('DOMContentLoaded',async()=>{
  await checkLang();
  initNotif();
  load();
  connectSSE();
});

// ─── PWA Service Worker + Install Prompt ───
if('serviceWorker' in navigator){
  navigator.serviceWorker.register('/sw.js').catch(()=>{});
}

let _installPrompt=null;
window.addEventListener('beforeinstallprompt',e=>{
  e.preventDefault();
  _installPrompt=e;
  showInstallBtn();
});

function showInstallBtn(){
  // Don't show if already installed (standalone mode)
  if(window.matchMedia('(display-mode:standalone)').matches)return;
  const existing=$('pwa-install');
  if(existing)return;
  const btn=document.createElement('button');
  btn.id='pwa-install';
  btn.textContent='📲 Install App';
  btn.style.cssText='position:fixed;bottom:90px;right:12px;z-index:999;background:linear-gradient(135deg,#7c3aed,#3b82f6);color:#fff;border:none;border-radius:12px;padding:8px 14px;font-size:11px;font-weight:600;cursor:pointer;box-shadow:0 4px 16px rgba(59,130,246,.4);animation:fadeIn .3s';
  btn.onclick=async()=>{
    if(_installPrompt){
      _installPrompt.prompt();
      const r=await _installPrompt.userChoice;
      if(r.outcome==='accepted')btn.remove();
      _installPrompt=null;
    }
  };
  document.body.appendChild(btn);
  // Auto-hide after 30s
  setTimeout(()=>{if(btn.parentElement)btn.remove()},30000);
}

// iOS Safari: detect and show manual instruction
window.addEventListener('DOMContentLoaded',()=>{
  const isIOS=/iPhone|iPad|iPod/.test(navigator.userAgent);
  const isSafari=/Safari/.test(navigator.userAgent)&&!/Chrome/.test(navigator.userAgent);
  const isStandalone=window.matchMedia('(display-mode:standalone)').matches||window.navigator.standalone;
  if(isIOS&&isSafari&&!isStandalone){
    // Show only once per session
    if(sessionStorage.getItem('ios-install-shown'))return;
    sessionStorage.setItem('ios-install-shown','1');
    setTimeout(()=>{
      const tip=document.createElement('div');
      tip.style.cssText='position:fixed;bottom:90px;left:12px;right:12px;z-index:999;background:#1e293b;border:1px solid #334155;border-radius:12px;padding:12px 16px;font-size:11px;color:var(--text);box-shadow:0 4px 24px rgba(0,0,0,.5);animation:fadeIn .3s';
      tip.innerHTML='📲 <b>Install as App:</b> Tap <span style="font-size:14px">□↑</span> (Share) → "Add to Home Screen"<button onclick="this.parentElement.remove()" style="float:right;background:none;border:none;color:var(--dim);cursor:pointer;font-size:14px">✕</button>';
      document.body.appendChild(tip);
      setTimeout(()=>{if(tip.parentElement)tip.remove()},15000);
    },3000);
  }
});
