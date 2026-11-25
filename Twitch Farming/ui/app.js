const $=id=>document.getElementById(id);
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let appState=null;
let pyApi=null;
let apiReady=false;
let apiReadyPromise=null;
let startupCompleteUi=false;
let initialStateReady=false;
let farmActionPending=false;
let currentPage="dashboard";
let lastLogId=0;
let channelMode="add";
let editingChannel="";
let channelCache="";
let channelSearch="";
let pageHistory=["dashboard"];
let pageHistoryIndex=0;
let windowMaximized=false;
let polling=false;
let toastTimer=null;
let channelPreviewTimer=null;
let channelPreviewSequence=0;
let channelDraftTokens=[];
let channelPreviewCache={};
let channelPreviewPending=new Map();
let pendingDeleteChannel="";
let draggedChannelCard=null;
let channelDropPending=false;
let logsLoaded=false;
let logsLoading=false;
let pendingLogRows=[];
let pollErrorShown=false;
let tutorialShown=false;
let appInitialized=false;
let followersPollTimer=null;
let followersLoadSequence=0;
let dropsPollTimer=null;
let dropsLoadSequence=0;
let logEntries=[];
let logFilter="important";
let logSearch="";
let tutorialStep=0;
const tutorialSteps=[
  {title:"Добавление каналов",text:"Нажми + и добавь Twitch-каналы по логину или ссылке. Аватары, имена и статус трансляции загрузятся автоматически.",target:".channels-panel"},
  {title:"Автоматизация",text:"Drops, бонусы, рейды, прогнозы и IRC-чат включаются в разделе «Настройки» бокового меню.",target:"#nav-settings"},
  {title:"Запуск и логи",text:"Нажми «Запустить». Во время сбора эта же кнопка станет кнопкой «Стоп». Награды и изменения баланса появятся в ленте событий.",targets:["#farm-toggle",".feed-grid .feed-panel:last-child"]}
];

function esc(value){
  return String(value==null?"":value).replace(/[&<>"']/g,char=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
}

function notify(message){
  const box=$("toast");
  box.textContent=String(message||"");
  box.classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer=setTimeout(()=>box.classList.remove("visible"),2600);
}

function api(){
  if(!apiReady||!pyApi) throw new Error("Связь с приложением ещё не готова.");
  return pyApi;
}

function withTimeout(promise,milliseconds,message){
  return Promise.race([
    promise,
    new Promise((resolve,reject)=>window.setTimeout(()=>reject(new Error(message)),milliseconds))
  ]);
}

function setStartupMessage(message){
  const node=$("startup-description");
  if(node) node.textContent=String(message||"");
}

async function waitForBridge(){
  setStartupMessage("Подключение встроенного браузера...");
  if(!apiReadyPromise) apiReadyPromise=window.__twitchPywebviewReadyPromise;
  if(!apiReadyPromise) throw new Error("Событие готовности PyWebView не зарегистрировано.");
  await withTimeout(apiReadyPromise,120000,"WebView2 не запустился за 120 секунд. Проверь Microsoft Edge WebView2 Runtime.");
  const bridge=window.pywebview&&window.pywebview.api;
  if(!bridge||typeof bridge!=="object") throw new Error("PyWebView сообщил о готовности, но Python API не передан в окно.");
  const required=["get_auth_state","get_state","get_startup_state","record_frontend_timing","mark_frontend_ready","restart_app","begin_session_restore","login","reorder_channels","get_followed_channels_state","refresh_followed_channels","add_followed_channel","get_drops_inventory_state","refresh_drops_inventory","add_drop_channel_to_farm","clear_avatar_cache","clear_currency_cache","logout","clear_logs","minimize_window","toggle_maximize_window","close_window"];
  const missing=required.filter(name=>typeof bridge[name]!=="function");
  if(missing.length) throw new Error("Python API не содержит методов: "+missing.join(", "));
  pyApi=bridge;
  apiReady=true;
  try{
    const navigation=performance.getEntriesByType("navigation")[0]||{};
    const timing=bridge.record_frontend_timing({
      dom_ready_ms:Number(navigation.domContentLoadedEventEnd||0),
      load_ms:Number(navigation.loadEventEnd||0),
      bridge_ready_ms:Number(performance.now()||0)
    });
    if(timing&&typeof timing.catch==="function") timing.catch(error=>console.warn("Startup timing report failed:",error));
  }catch(error){
    console.warn("Startup timing report failed:",error);
  }
  try{
    const ready=bridge.mark_frontend_ready();
    if(ready&&typeof ready.catch==="function") ready.catch(error=>console.warn("Frontend ready signal failed:",error));
  }catch(error){
    console.warn("Frontend ready signal failed:",error);
  }
  return pyApi;
}

function setPage(page,remember=true){
  if(remember&&page!==currentPage){
    pageHistory=pageHistory.slice(0,pageHistoryIndex+1);
    pageHistory.push(page);
    pageHistoryIndex=pageHistory.length-1;
  }
  currentPage=page;
  document.querySelectorAll(".page").forEach(item=>{
    const visible=item.id==="page-"+page||(page==="channel"&&item.id==="page-dashboard");
    item.classList.toggle("active",visible);
  });
  document.querySelectorAll(".nav-button").forEach(item=>item.classList.remove("active"));
  const navMap={dashboard:"nav-dashboard",settings:"nav-settings",bets:"nav-predictions",logs:"nav-events",followers:"nav-followers",drops:"nav-drops-inventory",channel:"nav-dashboard"};
  const active=$(navMap[page]||"nav-dashboard");
  if(active) active.classList.add("active");
  updateReward();
  if(page==="logs"){if(logsLoaded) renderLogs();else loadLogs();}
  if(page==="followers") loadFollowedChannels(false);
  if(page==="drops") loadDropsInventory(false);
  if(page==="settings"&&appState) renderSettingsSummary(appState);
}

function navigateHistory(direction){
  const next=pageHistoryIndex+direction;
  if(next<0||next>=pageHistory.length) return;
  pageHistoryIndex=next;
  setPage(pageHistory[next],false);
}

function focusOption(key,navId){
  setPage("settings");
  document.querySelectorAll(".nav-button").forEach(item=>item.classList.remove("active"));
  if(navId&&$(navId)) $(navId).classList.add("active");
  const row=$("row-"+key);
  if(row){
    document.querySelectorAll(".option-row").forEach(item=>item.classList.remove("feature-focus"));
    row.classList.add("feature-focus");
    row.scrollIntoView({block:"center",behavior:"smooth"});
    setTimeout(()=>row.classList.remove("feature-focus"),1700);
  }
}

function setWindowMaximized(value){
  windowMaximized=Boolean(value);
  const button=$("window-maximize");
  if(button){
    button.innerHTML="<svg><use href='"+(windowMaximized?"#i-restore":"#i-maximize")+"'></use></svg>";
    button.title=windowMaximized?"Восстановить":"Развернуть";
  }
}
window.setWindowMaximized=setWindowMaximized;

async function minimizeWindow(){
  try{await api().minimize_window();}catch(error){notify(error.message||"Не удалось свернуть окно.");}
}
async function toggleMaximizeWindow(){
  try{
    const result=await api().toggle_maximize_window();
    setWindowMaximized(Boolean(result.maximized));
  }catch(error){notify(error.message||"Не удалось изменить размер окна.");}
}
async function closeWindow(){
  try{await api().close_window();}catch(error){notify(error.message||"Не удалось закрыть окно.");}
}

function setFeedback(id,message,error){
  const node=$(id);
  node.textContent=message||"";
  node.style.color=error?"#ff8797":"#e5b866";
}

function filterChannelCards(){
  const root=$("channel-list");
  const query=channelSearch.trim().toLowerCase();
  const cards=root.querySelectorAll(".channel-card");
  let visible=0;
  cards.forEach(card=>{
    const match=!query||String(card.dataset.search||"").includes(query);
    card.classList.toggle("search-hidden",!match);
    if(match) visible++;
  });
  let empty=root.querySelector(".channel-search-empty");
  if(query&&cards.length&&!visible){
    if(!empty){
      empty=document.createElement("div");
      empty.className="channel-search-empty";
      empty.textContent="Каналы не найдены";
      root.appendChild(empty);
    }
  }else if(empty) empty.remove();
}

function renderChannels(channels,online,running,profiles,watchStatuses,balances,currencies){
  const signature=JSON.stringify([channels,online,running,profiles,watchStatuses,balances,currencies]);
  if(signature===channelCache) return;
  channelCache=signature;
  const root=$("channel-list");
  $("channel-count").textContent=String(channels.length);
  if(!channels.length){
    root.innerHTML="<div class='empty-channels'><div class='empty-channel-icon'><svg><use href='#i-channel'></use></svg></div><strong>Пока нет каналов</strong><span>Добавь первый Twitch-канал кнопкой +</span></div>";
    $("add-channel").disabled=running;
    return;
  }
  const orderedChannels=channels.slice();
  root.innerHTML=orderedChannels.map(login=>{
    const profile=profiles&&profiles[login]?profiles[login]:{};
    const live=online.includes(login)||Boolean(profile.is_live);
    const loaded=Boolean(profile.loaded)||live;
    const status=live?"В ЭФИРЕ":profile.error?"ОШИБКА":loaded?"ОФФЛАЙН":"ПРОВЕРКА";
    const displayName=profile.display_name||login;
    const avatar=typeof profile.avatar_url==="string"&&profile.avatar_url.startsWith("https://")?profile.avatar_url:"";
    const points=balances&&Object.prototype.hasOwnProperty.call(balances,login)?Number(balances[login]||0):null;
    const currency=currencies&&currencies[login]?currencies[login]:{};
    const pointsName=String(currency.name||"").trim()||"баллов";
    const pointsIconUrl=String(currency.icon_url||"");
    const pointsIcon=pointsIconUrl.startsWith("https://")?"<img class='channel-points-icon-image' loading='lazy' src='"+esc(pointsIconUrl)+"' alt='' onerror='this.remove()'>":"<svg class='channel-points-icon'><use href='#i-diamond'></use></svg>";
    const viewers=Number(profile.viewer_count||0);
    const watch=watchStatuses&&watchStatuses[login]?watchStatuses[login]:null;
    const watchFresh=watch&&Date.now()/1000-Number(watch.updated_at||0)<120;
    const watchLabels={selected:"Выбран",waiting:"В очереди",request:"Запрос…",accepted:"Принят",error:"Ошибка"};
    const watchMarkup=watchFresh&&watchLabels[watch.state]?"<span class='channel-watch-state "+esc(watch.state)+"'>"+esc(watchLabels[watch.state]+(watch.detail?" · "+watch.detail:""))+"</span>":"";
    const initial=esc(displayName.charAt(0).toUpperCase()||"?");
    const avatarMarkup="<div class='channel-avatar "+(avatar?"":"avatar-fallback")+"'>"+(avatar?"<img loading='lazy' src='"+esc(avatar)+"' alt='' onerror='this.parentElement.classList.add(\"avatar-fallback\");this.remove()'>":"")+"<span class='avatar-initial'>"+initial+"</span></div>";
    const pointsText=points===null?"—":points.toLocaleString("ru-RU");
    const liveClass=live?"is-live":profile.error?"is-error":loaded?"is-offline":"is-pending";
    const extra=live&&viewers?"<span class='channel-viewers'>"+viewers.toLocaleString("ru-RU")+" зрителей</span>":watchMarkup;
    const searchKey=esc((login+" "+displayName).toLowerCase());
    return "<article class='channel-card' data-channel='"+esc(login)+"' data-search='"+searchKey+"'><button class='channel-drag-handle' type='button' draggable='true' "+(running?"disabled":"")+" title='Перетащить канал' aria-label='Перетащить канал'>⋮⋮</button><div class='channel-card-top'><span class='channel-live-pill "+liveClass+"'><i class='channel-status-dot "+(live?"is-live":"")+"'></i>"+status+"</span><div class='channel-actions'><button class='channel-icon-button' data-action='open' data-channel='"+esc(login)+"' title='Открыть канал в браузере'><svg><use href='#i-external'></use></svg></button><button class='channel-icon-button' data-action='edit' data-channel='"+esc(login)+"' "+(running?"disabled":"")+" title='Изменить'><svg><use href='#i-edit'></use></svg></button><button class='channel-icon-button remove' data-action='remove' data-channel='"+esc(login)+"' "+(running?"disabled":"")+" title='Удалить'><svg><use href='#i-trash'></use></svg></button></div></div>"+avatarMarkup+"<div class='channel-info'><div class='channel-name'>"+esc(displayName)+"</div><div class='channel-login'>@"+esc(login)+"</div><div class='channel-card-bottom'>"+pointsIcon+"<strong>"+pointsText+"</strong><span>"+esc(pointsName)+"</span></div><div class='channel-extra'>"+extra+"</div></div></article>";
  }).join("");
  $("add-channel").disabled=running;
  filterChannelCards();
}


function renderFollowedChannels(state){
  const list=Array.isArray(state&&state.channels)?state.channels.slice().sort((a,b)=>{
    const liveA=Boolean(a&&a.is_live);
    const liveB=Boolean(b&&b.is_live);
    if(liveA!==liveB) return liveA?-1:1;
    if(!liveA) return 0;
    const viewersA=Math.max(0,Number(a.viewer_count)||0);
    const viewersB=Math.max(0,Number(b.viewer_count)||0);
    return viewersB-viewersA;
  }):[];
  const tracked=new Set(appState&&Array.isArray(appState.channels)?appState.channels:[]);
  const running=Boolean(appState&&appState.session_running);
  $("followers-count").textContent=String(list.length);
  $("followers-subtitle").textContent="Каналы, на которые подписан @"+String(appState&&(appState.authenticated_username||appState.username)||"Twitch");
  const updated=Number(state&&state.updated_at||0);
  $("followers-updated").textContent=updated?"Обновлено "+new Date(updated*1000).toLocaleTimeString("ru-RU",{hour:"2-digit",minute:"2-digit"}):"";
  const status=$("followers-status");
  status.classList.toggle("is-error",Boolean(state&&state.error));
  if(state&&state.loading){
    status.innerHTML="Загружаю подписки Twitch… Найдено: "+Number(state.found_count||0);
  }else if(state&&state.error){
    status.innerHTML="<span>"+esc(state.error)+"</span>"+(state.requires_reauth?"<div class='followers-status-actions'><button class='button button-primary' data-followers-action='reauth'>Переподключить Twitch</button></div>":"");
  }else if(state&&state.loaded){
    status.textContent=list.length?"Найдено подписок: "+list.length+". Нажми «Добавить», чтобы включить канал в автоматический сбор.":"У этого аккаунта пока нет подписок.";
  }else{
    status.textContent="Список подписок ещё не загружен. Нажми «Обновить».";
  }
  const root=$("followers-list");
  if(!list.length){
    root.innerHTML=state&&state.loading?"<div class='followers-empty'><strong>Загрузка подписок</strong><span>Получаю список каналов из Twitch…</span></div>":state&&state.error?"":"<div class='followers-empty'><strong>Подписок пока нет</strong><span>Здесь появятся каналы, на которые подписан текущий аккаунт.</span></div>";
    return;
  }
  root.innerHTML=list.map(item=>{
    const login=String(item.login||"").toLowerCase();
    const name=String(item.display_name||login);
    const avatar=String(item.avatar_url||"");
    const initial=esc(name.charAt(0).toUpperCase()||"?");
    const image=avatar.startsWith("https://")?"<img loading='lazy' src='"+esc(avatar)+"' alt='' onerror='this.remove()'>":"";
    const avatarMarkup=image||initial;
    const live=Boolean(item.is_live);
    const followed=String(item.followed_at||"");
    const followedTimestamp=Date.parse(followed);
    const followedTime=followed&&Number.isFinite(followedTimestamp)?timeAgo(followedTimestamp/1000):"";
    const meta=live?"<span class='follower-live'><i class='follower-live-dot'></i>В ЭФИРЕ"+(Number(item.viewer_count||0)?" · "+Number(item.viewer_count).toLocaleString("ru-RU")+" зрителей":"")+"</span>":followedTime?"Подписка · "+esc(followedTime):"Подписка Twitch";
    const added=tracked.has(login);
    const disabled=added||running;
    const buttonText=added?"Добавлен":running?"Сбор запущен":"Добавить";
    return "<article class='follower-card' data-login='"+esc(login)+"'><div class='follower-avatar'>"+avatarMarkup+"</div><div class='follower-info'><strong class='follower-name'>"+esc(name)+"</strong><span class='follower-login'>@"+esc(login)+"</span><div class='follower-meta'>"+meta+"</div></div><button class='button "+(added?"button-soft":"button-primary")+" follower-add' data-followers-action='add' data-login='"+esc(login)+"' "+(disabled?"disabled":"")+">"+buttonText+"</button></article>";
  }).join("");
}

function stopFollowedPolling(){
  if(followersPollTimer!==null){
    clearInterval(followersPollTimer);
    followersPollTimer=null;
  }
}

function watchFollowedChannels(sequence){
  stopFollowedPolling();
  followersPollTimer=setInterval(async()=>{
    if(sequence!==followersLoadSequence){
      stopFollowedPolling();
      return;
    }
    try{
      const state=await api().get_followed_channels_state();
      if(currentPage==="followers") renderFollowedChannels(state);
      if(!state.loading) stopFollowedPolling();
    }catch(error){
      stopFollowedPolling();
      if(currentPage==="followers") notify(error.message||"Не удалось обновить фолловеров.");
    }
  },900);
}

async function loadFollowedChannels(force=false){
  if(!apiReady||!pyApi) return;
  if(!appState||!appState.authenticated){
    $("followers-status").textContent="Сначала подключи аккаунт Twitch.";
    return;
  }
  const sequence=++followersLoadSequence;
  const refresh=$("refresh-followers");
  if(refresh&&force){
    refresh.disabled=true;
    refresh.textContent="Загрузка…";
  }
  try{
    let state=await withTimeout(api().get_followed_channels_state(),10000,"Twitch API не ответил.");
    renderFollowedChannels(state);
    if(state.loading){
      watchFollowedChannels(sequence);
      return;
    }
    if(force||!state.loaded){
      const result=await withTimeout(api().refresh_followed_channels(Boolean(force)),10000,"Не удалось запустить загрузку подписок.");
      if(!result.ok){
        notify(result.message||"Не удалось загрузить фолловеров.");
        return;
      }
      state=await api().get_followed_channels_state();
      renderFollowedChannels(state);
      if(state.loading) watchFollowedChannels(sequence);
    }
  }catch(error){
    $("followers-status").textContent=error.message||"Не удалось загрузить фолловеров.";
    $("followers-status").classList.add("is-error");
  }finally{
    if(refresh){
      refresh.disabled=false;
      refresh.innerHTML="<svg><use href='#i-refresh'></use></svg>Обновить";
    }
  }
}

async function addFollowedChannel(login,button){
  if(!login||!apiReady) return;
  if(button) button.disabled=true;
  try{
    const result=await withTimeout(api().add_followed_channel(login),15000,"Twitch не ответил на добавление канала.");
    if(!result.ok){
      notify(result.message||"Не удалось добавить канал.");
      if(button) button.disabled=false;
      return;
    }
    notify(result.message||"Канал добавлен в автоматический сбор.");
    await poll();
    const state=await api().get_followed_channels_state();
    renderFollowedChannels(state);
  }catch(error){
    notify(error.message||"Не удалось добавить канал.");
    if(button) button.disabled=false;
  }
}


function timeAgo(timestamp){
  const seconds=Math.max(0,Math.floor(Date.now()/1000-Number(timestamp||0)));
  if(seconds<60) return "только что";
  const minutes=Math.floor(seconds/60);
  if(minutes<60) return minutes+" мин. назад";
  const hours=Math.floor(minutes/60);
  if(hours<24) return hours+" ч. назад";
  return Math.floor(hours/24)+" дн. назад";
}

function renderRewards(rewards){
  const root=$("reward-list");
  const items=Array.isArray(rewards)?rewards.slice(-30).reverse():[];
  $("reward-count").textContent=String(items.length);
  const icons={gem:"i-diamond",gift:"i-gift",star:"i-star"};
  const html=items.length?items.map(item=>{
    const icon=icons[item.kind]||"i-diamond";
    return "<div class='feed-item'><div class='feed-item-icon reward-"+esc(item.kind||"gem")+"'><svg><use href='#"+icon+"'></use></svg></div><div class='feed-item-copy'><strong>"+esc(item.title||"Награда")+"</strong><span>"+esc(item.description||"Награда Twitch")+"</span></div><time>"+timeAgo(item.timestamp)+"</time></div>";
  }).join(""):"<div class='feed-empty'>Награды появятся после начисления Channel Points.</div>";
  if(root.dataset.signature!==html){
    root.dataset.signature=html;
    root.innerHTML=html;
  }
}

function renderEvents(events){
  const root=$("event-list");
  const items=Array.isArray(events)?events.slice(-40).reverse():[];
  $("event-count").textContent=String(items.length);
  const html=items.length?items.map(line=>{
    const match=String(line).match(/^(\d{2}:\d{2}:\d{2})\s+(.+)$/);
    const stamp=match?match[1]:"";
    const message=match?match[2]:String(line);
    const type=/ошиб|предупрежд/i.test(message)?"event-warning":/эфире|online/i.test(message)?"event-live":"event-normal";
    const icon=type==="event-warning"?"i-settings":type==="event-live"?"i-channel":"i-star";
    return "<div class='feed-item'><div class='feed-item-icon "+type+"'><svg><use href='#"+icon+"'></use></svg></div><div class='feed-item-copy'><strong>"+esc(message)+"</strong><span>Событие Twitch</span></div><time>"+esc(stamp)+"</time></div>";
  }).join(""):"<div class='feed-empty'>События появятся после запуска фарма.</div>";
  if(root.dataset.signature!==html){
    root.dataset.signature=html;
    root.innerHTML=html;
  }
}

function updateReward(){
  const node=$("reward-banner");
  if(node) node.classList.remove("visible");
}

function filteredLogEntries(){
  const query=String(logSearch||"").trim().toLowerCase();
  return logEntries.filter(item=>{
    const line=String(item.line||"");
    if(query&&!line.toLowerCase().includes(query)) return false;
    if(logFilter==="points") return /\+\s*[\d,]+/.test(line)&&/Reason:\s*(?:WATCH|CLAIM|WATCH_STREAK|RAID|PREDICTION|REFUND)/i.test(line);
    if(logFilter==="important") return /\[(?:INFO|WARNING|ERROR|CRITICAL)\]/i.test(line);
    return true;
  });
}

function renderLogs(){
  const box=$("log-view");
  if(!box) return;
  const rows=filteredLogEntries();
  $("log-count").textContent=rows.length+" / 500";
  if(!rows.length){
    box.innerHTML="<div class='log-empty'>"+(logEntries.length?"По заданным фильтрам ничего не найдено.":"Журнал пока пуст.")+"</div>";
    return;
  }
  box.innerHTML=rows.map(item=>{
    const line=String(item.line||"");
    let html=esc(line);
    html=html.replace(/\[(INFO|WARNING|ERROR|CRITICAL|DEBUG)\]/g,(match,level)=>"<span class='log-level log-level-"+level.toLowerCase()+"'>["+level+"]</span>");
    html=html.replace(/(\+\s*[\d,]+)/g,"<span class='log-points'>$1</span>");
    html=html.replace(/(Reason:\s*(?:WATCH|CLAIM|WATCH_STREAK|RAID|PREDICTION|REFUND))/g,"<span class='log-reason'>$1</span>");
    return "<div class='log-line'>"+html+"</div>";
  }).join("");
  if(currentPage==="logs") box.scrollTop=box.scrollHeight;
}
function appendLogRows(rows){
  if(!rows||!rows.length) return;
  if(logsLoading){
    pendingLogRows.push(...rows);
    return;
  }
  let changed=false;
  for(const row of rows){
    if(row.id>lastLogId){
      logEntries.push({id:Number(row.id),line:String(row.line||"")});
      lastLogId=Math.max(lastLogId,Number(row.id)||0);
      changed=true;
    }
  }
  if(logEntries.length>500) logEntries=logEntries.slice(-500);
  if(changed&&currentPage==="logs") renderLogs();
}

function renderAccountSidebar(state){
  const profile=state.account_profile||(appState&&appState.account_profile)||{};
  const username=state.authenticated_username||state.username||(appState&&appState.authenticated_username)||"";
  const display=profile.display_name||username||"Twitch";
  const initial=String(display).charAt(0).toUpperCase()||"T";
  $("account-display-name").textContent=display;
  $("account-login").textContent=username?"@"+username:"Аккаунт не подключён";
  const farmRunning=Boolean(state.session_running||(appState&&appState.session_running));
  const restoring=!state.authenticated&&(Boolean(state.restore_pending)||state.worker_state==="restoring");
  const statusText=state.authenticated?(farmRunning?"РАБОТАЕТ":"TWITCH ПОДКЛЮЧЁН"):restoring?"ПРОВЕРКА СЕССИИ":"НЕ ПОДКЛЮЧЁН";
  const statusColor=state.authenticated?"#42d6a4":restoring?"#f0b65a":"#8792a8";
  $("account-state").textContent=statusText;
  $("account-state").style.color=statusColor;
  $("account-state-dot").style.background=statusColor;
  const connectButton=$("account-connect-button");
  const connectMode=state.authenticated?"refresh":"add";
  if(connectButton.dataset.mode!==connectMode){connectButton.dataset.mode=connectMode;connectButton.innerHTML='<span class="ui-icon icon-'+connectMode+'"></span>';}
  $("account-connect-button").title=state.authenticated?"Переподключить Twitch":"Подключить Twitch";
  $("account-connect-button").disabled=Boolean(state.worker_alive);
  const avatar=$("account-avatar");
  const avatarUrl=typeof profile.avatar_url==="string"&&profile.avatar_url.startsWith("https://")?profile.avatar_url:"";
  if(avatar.dataset.url!==avatarUrl){
    avatar.dataset.url=avatarUrl;
    avatar.textContent=initial;
    if(avatarUrl){
      const image=document.createElement("img");
      image.src=avatarUrl;
      image.alt="";
      image.onerror=()=>{avatar.replaceChildren(document.createTextNode(initial));};
      avatar.replaceChildren(image);
    }
  }else if(!avatarUrl&&!avatar.querySelector("img")){
    avatar.textContent=initial;
  }
}

function applyAuthState(state){
  const authenticated=Boolean(state.authenticated);
  const restoring=!authenticated&&(Boolean(state.restore_pending)||state.worker_state==="restoring");
  $("login-gate").classList.toggle("visible",!authenticated&&!restoring);
  $("app-shell").classList.toggle("auth-hidden",!authenticated);
  $("gate-login-button").disabled=Boolean(state.worker_alive);
  $("gate-status").textContent=authenticated
    ?"Аккаунт подключён. Загружаю рабочую панель…"
    :state.auth_error
      ?"Ошибка авторизации: "+state.auth_error
      :state.auth_status&&state.auth_status.includes("ОЖИДАЕТСЯ ПОДТВЕРЖДЕНИЕ")
        ?"Код отправлен. Подтверди вход в открытом браузере Twitch."
        :state.worker_alive
          ?"Подключение к Twitch… ("+Number(state.elapsed||0)+" сек.)"
          :state.auth_status&&state.auth_status.includes("НЕ УДАЛАСЬ")
            ?"Не удалось подключиться. "+(state.auth_error||"Повтори вход.")
            :"Введи логин Twitch.";
  if(state.username&&document.activeElement!==$("gate-username")) $("gate-username").value=state.username;
  renderAccountSidebar(state);
}

function applyState(state,initial){
  appState=state;
  cacheChannelProfiles(state);
  applyAuthState(state);
  const authenticated=Boolean(state.authenticated);
  $("gate-login-button").disabled=state.auth_status.includes("ПОДКЛЮЧЕНИЕ")||state.auth_status.includes("ПРОВЕРКА")||state.auth_status.includes("ОЖИДАЕТСЯ ПОДТВЕРЖДЕНИЕ");
  if(document.activeElement!==$("gate-username")) $("gate-username").value=state.username||"";
  if(!authenticated){
    const waiting=state.auth_status.includes("ОЖИДАЕТСЯ ПОДТВЕРЖДЕНИЕ");
    $("gate-status").textContent=state.auth_code&&!state.auth_code.includes("Сессия")?"Код Twitch: "+state.auth_code+". Подтверди вход в открывшемся браузере.":waiting?"Ожидается подтверждение входа в браузере Twitch…":state.auth_status.includes("ПОДКЛЮЧЕНИЕ")?"Выполняется подключение к Twitch…":state.auth_status.includes("ПРОВЕРКА")?"Проверяю сохранённую сессию Twitch…":state.auth_status.includes("НЕ УДАЛАСЬ")?"Не удалось подключиться. Проверь браузер и попробуй ещё раз.":"Введи логин и подключи аккаунт, чтобы открыть приложение.";
  }
  updateFarmToggleButton(Boolean(state.session_running));
  $("session-label").textContent=state.session_running?"ФАРМ РАБОТАЕТ":String(state.status_text||"ОСТАНОВЛЕН").replace(/[●]/g,"").trim();
  $("session-dot").classList.toggle("is-running",Boolean(state.session_running));
  $("points-value").textContent=Number(state.gained_total||0).toLocaleString("ru-RU");
  $("online-value").textContent=String((state.online_channels||[]).length);
  $("events-value").textContent=String(state.event_total||0);
  renderChannels(state.channels||[],state.online_channels||[],state.session_running,state.channel_profiles||{},state.watch_status||{},state.channel_balances||{},state.channel_currency||{});
  renderRewards(state.rewards||[]);
  renderEvents(state.events||[]);
  if(initial){
    const options=state.options||{};
    Object.keys(options).forEach(key=>{
      const checkbox=$("opt-"+key);
      if(checkbox) checkbox.checked=Boolean(options[key]);
    });
    const appOptions=state.app_settings||{};
    Object.keys(appOptions).forEach(key=>{
      const checkbox=$("opt-"+key);
      if(checkbox) checkbox.checked=Boolean(appOptions[key]);
    });
    fillPreset(state.selected_preset,state.bet_settings,state.custom_presets);
  }
  $("drops-badge").textContent=String(Number(state.drops_active_campaigns||0));
  $("drops-badge").classList.toggle("visible",Number(state.drops_active_campaigns||0)>0);
  if(currentPage==="settings") renderSettingsSummary(state);
  if(authenticated&&!state.tutorial_done&&!tutorialShown&&currentPage==="dashboard") showTutorial();
  if(state.tutorial_done&&tutorialShown) hideTutorial(false);
  appendLogRows(state.logs||[]);
  updateReward();
}

async function poll(){
  if(polling||!apiReady||!pyApi) return;
  polling=true;
  try{
    const authState=await api().get_auth_state();
    applyAuthState(authState);
    if(authState.authenticated){
      const firstLoad=!appInitialized;
      const state=await api().get_state(lastLogId);
      state.authenticated=true;
      state.authenticated_username=authState.username;
      state.auth_status=authState.auth_status;
      state.auth_error="";
      applyState(state,firstLoad);
      if(firstLoad){
        appInitialized=true;
        setPage("dashboard");
      }
    }
    pollErrorShown=false;
  }catch(error){
    console.error("Twitch Farming API polling failed:",error);
    const message=error&&error.message?error.message:String(error);
    $("gate-status").textContent="Ошибка связи с приложением: "+message;
    if(!pollErrorShown){
      notify("Не удалось обновить состояние приложения: "+message);
      pollErrorShown=true;
    }
  }finally{
    polling=false;
  }
}

async function loadLogs(){
  if(logsLoaded||logsLoading) return;
  logsLoading=true;
  const box=$("log-view");
  box.textContent="Загружаю журнал…";
  try{
    const result=await api().get_log_snapshot();
    const rows=Array.isArray(result.rows)?result.rows:(result.lines||[]).map((line,index)=>({id:index+1,line}));
    logEntries=rows.slice(-500).map(item=>({id:Number(item.id)||0,line:String(item.line||"")}));
    const last=Number(result.last_log_id||0);
    lastLogId=Math.max(lastLogId,last);
    logsLoaded=true;
    renderLogs();
    box.scrollTop=box.scrollHeight;
    await poll();
  }catch(error){
    box.textContent="Не удалось загрузить журнал.";
    notify("Не удалось загрузить журнал.");
  }finally{
    logsLoading=false;
    if(pendingLogRows.length){
      const queued=pendingLogRows;
      pendingLogRows=[];
      appendLogRows(queued);
    }
  }
}

function collectOptions(){
  const result={};
  ["claim_drops","watch_streak","claim_moments","follow_raid","make_predictions","chat"].forEach(key=>{
    result[key]=$("opt-"+key).checked;
  });
  return result;
}

function collectBet(){
  const result={
    strategy:$("bet-strategy").value,
    percentage:Number($("bet-percentage").value),
    percentage_gap:Number($("bet-percentage_gap").value),
    max_points:Number($("bet-max_points").value),
    minimum_points:Number($("bet-minimum_points").value),
    delay:Number($("bet-delay").value),
    delay_mode:$("bet-delay_mode").value,
    filter_enabled:$("bet-filter_enabled").checked,
    filter_by:$("bet-filter_by").value,
    filter_condition:$("bet-filter_condition").value,
    filter_value:Number($("bet-filter_value").value),
    stealth_mode:$("bet-stealth_mode").checked
  };
  if(!Number.isFinite(result.percentage)||!Number.isFinite(result.percentage_gap)||!Number.isInteger(result.max_points)||!Number.isInteger(result.minimum_points)||!Number.isFinite(result.delay)||!Number.isFinite(result.filter_value)){
    throw new Error("Проверь числовые поля прогнозов.");
  }
  if(result.percentage<0||result.percentage>100||result.percentage_gap<0||result.percentage_gap>100||result.max_points<0||result.minimum_points<0||result.delay<0){
    throw new Error("Проценты: 0–100. Очки и задержка не могут быть отрицательными.");
  }
  if(result.delay_mode==="PERCENTAGE"&&result.delay>1) throw new Error("Для PERCENTAGE введи значение от 0 до 1.");
  return result;
}

function collectPayload(){
  const presets=appState&&appState.custom_presets?appState.custom_presets:{};
  return {
    username:String((appState&&appState.authenticated_username)||(appState&&appState.username)||"").trim().toLowerCase(),
    channels:appState&&appState.channels?appState.channels:[],
    settings:collectOptions(),
    app_settings:{
      minimize_to_tray:$("opt-minimize_to_tray").checked,
      auto_start_farm:$("opt-auto_start_farm").checked
    },
    bet_settings:collectBet(),
    custom_presets:presets,
    selected_preset:$("settings-preset-select").value||$("preset-select").value||"Оптимальный"
  };
}

async function saveSettings(){
  if(!ensureStartupReady()) return;
  try{
    const result=await api().save_settings(collectPayload());
    notify(result.message||"Настройки сохранены.");
    await poll();
  }catch(error){
    notify(error.message||"Не удалось сохранить настройки.");
  }
}

function updateFarmToggleButton(running){
  const button=$("farm-toggle");
  if(!button) return;
  button.classList.toggle("button-primary",!running);
  button.classList.toggle("button-start",!running);
  button.classList.toggle("button-stop",running);
  button.disabled=!startupCompleteUi||!initialStateReady||farmActionPending;
  button.innerHTML=running?"<svg><use href='#i-stop'></use></svg>Стоп":"<svg><use href='#i-play'></use></svg>Запустить";
  button.title=running?"Остановить сбор":"Запустить сбор";
}

function ensureStartupReady(){
  if(startupCompleteUi&&initialStateReady) return true;
  notify("Инициализация ещё выполняется. Меню доступно, операции станут доступны после загрузки.");
  return false;
}

async function toggleFarm(){
  if(!ensureStartupReady()||farmActionPending) return;
  farmActionPending=true;
  const running=Boolean(appState&&appState.session_running);
  updateFarmToggleButton(running);
  try{
    const result=running?await api().stop_farm():await api().start_farm(collectPayload());
    notify(result.message||"");
    await poll();
  }catch(error){
    notify(error.message||(running?"Не удалось остановить фарм.":"Не удалось запустить фарм."));
  }finally{
    farmActionPending=false;
    updateFarmToggleButton(Boolean(appState&&appState.session_running));
  }
}

async function loginUser(username){
  if(!ensureStartupReady()) return;
  username=String(username||"").trim().toLowerCase();
  if(!username){$("gate-status").textContent="Сначала введи логин Twitch.";return;}
  try{
    $("gate-status").textContent="Подключение к Twitch…";
    $("gate-login-button").disabled=true;
    await api().save_settings({username:username});
    const result=await api().login(username);
    $("gate-status").textContent=result.message||"Ожидается авторизация Twitch.";
    await poll();
  }catch(error){
    $("gate-status").textContent=error.message||"Ошибка авторизации.";
    $("gate-login-button").disabled=false;
  }
}

async function gateLogin(){
  return loginUser($("gate-username").value);
}

function normalizeChannelToken(value){
  let login=String(value||"").trim();
  login=login.replace(/^(?:https?:\/\/)?(?:www\.)?twitch\.tv\//i,"");
  login=login.split(/[/?#]/,1)[0].replace(/^@/,"").trim().toLowerCase();
  return /^[a-z0-9_]{1,25}$/.test(login)?login:"";
}

function channelTokens(value){
  return [...new Set(String(value||"").split(/[\s,;]+/).map(normalizeChannelToken).filter(Boolean))].slice(0,100);
}

function cacheChannelProfiles(state){
  const profiles=state&&state.channel_profiles||{};
  Object.keys(profiles).forEach(login=>{
    const profile=profiles[login]||{};
    if(!profile.loaded&&!profile.is_live) return;
    if(channelPreviewCache[login]) return;
    channelPreviewCache[login]={
      login,
      loaded:Boolean(profile.loaded||profile.is_live),
      display_name:String(profile.display_name||login),
      avatar_url:String(profile.avatar_url||""),
      is_live:Boolean(profile.is_live),
      viewer_count:Number(profile.viewer_count||0),
      error:String(profile.error||"")
    };
  });
}

function renderChannelChips(){
  const root=$("channel-chips");
  if(channelMode!=="add"||!channelDraftTokens.length){root.innerHTML="";return;}
  root.innerHTML=channelDraftTokens.map(login=>"<span class='channel-chip'>@"+esc(login)+"<button type='button' data-remove-channel='"+esc(login)+"' title='Убрать'>×</button></span>").join("");
}

function previewStatus(profile){
  return profile&&profile.error?"НЕ НАЙДЕН":profile&&profile.loaded?(profile.is_live?"В ЭФИРЕ":"ОФФЛАЙН"):"ПРОВЕРКА";
}

function renderChannelPreviewCards(items){
  const root=$("channel-preview-list");
  if(!items||!items.length){root.innerHTML="";return;}
  root.innerHTML=items.map(item=>{
    const login=String(item.login||"");
    const display=String(item.display_name||login);
    const avatar=String(item.avatar_url||"");
    const live=Boolean(item.is_live);
    const status=previewStatus(item);
    const statusClass=item.error?"is-error":item.loaded?(live?"is-live":"is-offline"):"is-pending";
    const image=avatar?"<img src='"+esc(avatar)+"' alt='' loading='lazy' onerror='this.remove();this.parentElement.classList.add(&quot;avatar-fallback&quot;)'>":"";
    return "<article class='channel-preview-card'><div class='channel-preview-avatar "+(avatar?"":"avatar-fallback")+"'>"+image+"<span>"+esc(display.charAt(0).toUpperCase()||"?")+"</span></div><div class='channel-preview-copy'><strong>"+esc(display)+"</strong><span>@"+esc(login)+"</span><div class='channel-preview-meta'><span class='channel-live-pill "+statusClass+"'><i class='channel-status-dot "+(live?"is-live":"") +"'></i>"+status+"</span>"+(Number(item.viewer_count||0)>0?"<small>"+Number(item.viewer_count).toLocaleString("ru-RU")+" зрителей</small>":"")+"</div></div></article>";
  }).join("");
}

function requestChannelPreviews(logins){
  const unique=[...new Set(logins)].filter(Boolean);
  const waiting=unique.map(login=>channelPreviewPending.get(login)).filter(Boolean);
  const missing=unique.filter(login=>!channelPreviewCache[login]&&!channelPreviewPending.has(login));
  if(!missing.length) return Promise.all(waiting);
  const request=Promise.resolve().then(async()=>{
    try{
      const result=await api().preview_channels(missing.join("\n"));
      const returned=new Map((result.channels||[]).map(item=>[String(item.login||"").toLowerCase(),item]));
      missing.forEach(login=>{
        const profile=returned.get(login);
        channelPreviewCache[login]=profile?{
          login,
          loaded:Boolean(profile.loaded),
          display_name:String(profile.display_name||login),
          avatar_url:String(profile.avatar_url||""),
          is_live:Boolean(profile.is_live),
          viewer_count:Number(profile.viewer_count||0),
          error:String(profile.error||"")
        }:{
          login,
          loaded:true,
          display_name:login,
          avatar_url:"",
          is_live:false,
          viewer_count:0,
          error:"Не удалось получить данные канала"
        };
      });
    }catch(error){
      missing.forEach(login=>{
        channelPreviewCache[login]={
          login,
          loaded:true,
          display_name:login,
          avatar_url:"",
          is_live:false,
          viewer_count:0,
          error:String(error&&error.message||error||"Ошибка запроса").slice(0,160)
        };
      });
    }
    if(channelMode==="add"&&currentPage==="channel") renderChannelPreviewCards(channelDraftTokens.map(login=>channelPreviewCache[login]||{login,loaded:false}));
  });
  missing.forEach(login=>channelPreviewPending.set(login,request));
  const cleanup=()=>missing.forEach(login=>{if(channelPreviewPending.get(login)===request) channelPreviewPending.delete(login);});
  request.then(cleanup,cleanup);
  return Promise.all([...waiting,request]);
}

function addChannelTokens(values){
  const current=new Set(channelDraftTokens);
  const existing=new Set(appState&&appState.channels||[]);
  (Array.isArray(values)?values:channelTokens(values)).forEach(value=>{
    const login=normalizeChannelToken(value);
    if(!login||current.has(login)||existing.has(login)) return;
    current.add(login);
    channelDraftTokens.push(login);
  });
  renderChannelChips();
  updateChannelPreview();
}

function updateChannelPreview(){
  renderChannelChips();
  if(channelMode!=="add") return;
  clearTimeout(channelPreviewTimer);
  if(!channelDraftTokens.length){$("channel-preview-list").innerHTML="";return;}
  renderChannelPreviewCards(channelDraftTokens.map(login=>channelPreviewCache[login]||{login,loaded:false}));
  channelPreviewTimer=setTimeout(()=>requestChannelPreviews(channelDraftTokens),160);
}

function renderEditChannelPreview(login){
  const profile=appState&&appState.channel_profiles&&appState.channel_profiles[login]?appState.channel_profiles[login]:{};
  const currency=appState&&appState.channel_currency&&appState.channel_currency[login]?appState.channel_currency[login]:{};
  const balances=appState&&appState.channel_balances||{};
  const hasBalance=Object.prototype.hasOwnProperty.call(balances,login);
  const points=hasBalance?Number(balances[login]||0).toLocaleString("ru-RU"):"—";
  const display=String(profile.display_name||login);
  const avatar=String(profile.avatar_url||"");
  const live=Boolean(profile.is_live||(appState&&Array.isArray(appState.online_channels)&&appState.online_channels.includes(login)));
  const icon=String(currency.icon_url||"");
  const pointsIcon=icon.startsWith("https://")?"<img class='channel-points-icon-image' src='"+esc(icon)+"' alt='' onerror='this.remove()'>":"<svg class='channel-points-icon'><use href='#i-diamond'></use></svg>";
  const status=profile.loaded?(live?"В ЭФИРЕ":"ОФФЛАЙН"):(live?"В ЭФИРЕ":"СТАТУС НЕ ПРОВЕРЕН");
  const statusClass=live?"is-live":"is-offline";
  const image=avatar?"<img src='"+esc(avatar)+"' alt='' loading='lazy' onerror='this.remove();this.parentElement.classList.add(&quot;avatar-fallback&quot;)'>":"";
  $("channel-preview-list").innerHTML="<article class='channel-preview-card edit-preview'><div class='channel-preview-avatar "+(avatar?"":"avatar-fallback")+"'>"+image+"<span>"+esc(display.charAt(0).toUpperCase()||"?")+"</span></div><div class='channel-preview-copy'><strong>"+esc(display)+"</strong><span>@"+esc(login)+"</span><div class='channel-preview-meta'><span class='channel-live-pill "+statusClass+"'><i class='channel-status-dot "+(live?"is-live":"") +"'></i>"+status+"</span><span class='channel-balance'>"+pointsIcon+"<b>"+points+"</b><small>"+esc(String(currency.name||"баллов"))+"</small></span></div></div></article>";
}

function openChannelEditor(mode,oldChannel){
  channelMode=mode;
  editingChannel=oldChannel||"";
  channelDraftTokens=[];
  $("channel-page-title").textContent=mode==="add"?"Добавить каналы":"Изменить канал";
  $("channel-page-subtitle").textContent=mode==="add"?"Добавляй каналы по одному":"Настройки выбранного Twitch-канала";
  $("channel-input").value=oldChannel||"";
  $("channel-input").placeholder=mode==="add"?"Введи логин Twitch...":"Логин или twitch.tv/channel";
  $("channel-remove").style.display=mode==="edit"?"inline-flex":"none";
  $("save-channel").textContent=mode==="add"?"Добавить каналы":"Сохранить";
  $("channel-input-hint").textContent=mode==="add"?"Нажми Enter, Пробел или запятую, чтобы добавить ник в список.":"Введи логин или ссылку на канал Twitch";
  $("channel-chips").innerHTML="";
  $("channel-preview-list").innerHTML="";
  setFeedback("channel-feedback","");
  setPage("channel");
  if(mode==="edit") renderEditChannelPreview(oldChannel);
  else updateChannelPreview();
  $("channel-input").focus();
  if(mode==="edit") $("channel-input").select();
}

async function saveChannel(){
  if(!ensureStartupReady()) return;
  const raw=$("channel-input").value.trim();
  const tokens=channelMode==="add"?[...new Set([...channelDraftTokens,...channelTokens(raw)])]:[];
  if(channelMode==="add"&&!tokens.length){setFeedback("channel-feedback","Добавь хотя бы один ник в список.",true);return;}
  try{
    if(channelMode==="edit"){
      const newLogin=normalizeChannelToken(raw);
      const currentLogin=normalizeChannelToken(editingChannel);
      if(!newLogin){
        setFeedback("channel-feedback","Укажи корректный логин или ссылку Twitch.",true);
        return;
      }
      if(newLogin===currentLogin){
        setPage("dashboard");
        return;
      }
    }
    if(channelMode==="add") await requestChannelPreviews(tokens);
    const previews=channelMode==="add"?tokens.map(login=>channelPreviewCache[login]).filter(Boolean):[];
    const result=await api().save_channel_editor(channelMode,editingChannel,channelMode==="add"?tokens.join("\n"):raw,previews);
    if(!result.ok){setFeedback("channel-feedback",result.message,true);return;}
    notify(result.message||"Канал сохранён.");
    channelCache="";
    channelPreviewSequence++;
    await poll();
    setPage("dashboard");
  }catch(error){
    setFeedback("channel-feedback",error.message||"Не удалось сохранить канал.",true);
  }
}

async function refreshChannelBranding(){
  if(!ensureStartupReady()) return;
  const button=$("refresh-channel-branding");
  const original=button.innerHTML;
  button.disabled=true;
  button.textContent="Проверка...";
  try{
    const result=await api().refresh_channel_points_branding();
    notify(result.message||"Проверка оформления запущена.");
    if(!result.ok) setFeedback("branding-feedback",result.message,true);
    else setFeedback("branding-feedback","Значки и названия проверяются для всех каналов.",false);
  }catch(error){
    setFeedback("branding-feedback",error.message||"Не удалось проверить оформление.",true);
  }finally{
    button.disabled=false;
    button.innerHTML=original;
  }
}

function openDeleteConfirmation(login){
  pendingDeleteChannel=String(login||"");
  if(!pendingDeleteChannel) return;
  const profile=appState&&appState.channel_profiles&&appState.channel_profiles[pendingDeleteChannel]?appState.channel_profiles[pendingDeleteChannel]:{};
  const display=String(profile.display_name||pendingDeleteChannel);
  const avatar=String(profile.avatar_url||"");
  const avatarHtml=avatar?"<img src='"+esc(avatar)+"' alt='' onerror='this.remove();this.parentElement.classList.add(&quot;avatar-fallback&quot;)'>":"";
  $("delete-confirm-channel").innerHTML="<div class='delete-confirm-avatar "+(avatar?"":"avatar-fallback")+"'>"+avatarHtml+"<span>"+esc(display.charAt(0).toUpperCase()||"?")+"</span></div><div class='delete-confirm-copy'><strong>"+esc(display)+"</strong><span>@"+esc(pendingDeleteChannel)+"</span></div>";
  $("delete-confirm-overlay").classList.add("visible");
  $("delete-confirm-button").disabled=false;
  $("delete-confirm-button").textContent="Удалить";
}

function closeDeleteConfirmation(){
  pendingDeleteChannel="";
  $("delete-confirm-overlay").classList.remove("visible");
  $("delete-confirm-button").disabled=false;
  $("delete-confirm-button").textContent="Удалить";
}

function removeChannel(login){
  if(!ensureStartupReady()) return;
  openDeleteConfirmation(login);
}

async function confirmDeleteChannel(){
  const login=pendingDeleteChannel;
  if(!login) return;
  const button=$("delete-confirm-button");
  button.disabled=true;
  button.textContent="Удаление...";
  try{
    const result=await api().remove_channel(login);
    if(!result.ok){notify(result.message||"Не удалось удалить канал.");button.disabled=false;button.textContent="Удалить";return;}
    closeDeleteConfirmation();
    channelCache="";
    await poll();
    if(currentPage==="channel"&&editingChannel===login) setPage("dashboard");
    notify("Канал удалён.");
  }catch(error){
    notify(error.message||"Не удалось удалить канал.");
    button.disabled=false;
    button.textContent="Удалить";
  }
}

function fillSelect(id,values){
  const select=$(id);
  select.innerHTML=values.map(value=>'<option value="'+esc(value)+'">'+esc(value)+'</option>').join("");
}

const betDefaults={
  strategy:"SMART",
  percentage:5,
  percentage_gap:20,
  max_points:50000,
  minimum_points:0,
  stealth_mode:false,
  delay:6,
  delay_mode:"FROM_END",
  filter_enabled:false,
  filter_by:"TOTAL_USERS",
  filter_condition:"GT",
  filter_value:200
};

function setupBetControls(){
  fillSelect("bet-strategy",["SMART","MOST_VOTED","HIGH_ODDS","PERCENTAGE","SMART_MONEY","NUMBER_1","NUMBER_2","NUMBER_3","NUMBER_4","NUMBER_5","NUMBER_6","NUMBER_7","NUMBER_8"]);
  fillSelect("bet-filter_by",["TOTAL_USERS","DECISION_USERS","TOTAL_POINTS","DECISION_POINTS","ODDS","ODDS_PERCENTAGE","PERCENTAGE_USERS","TOP_POINTS"]);
}

function fillPreset(name,data,presets){
  const values=name==="Оптимальный"?betDefaults:(presets&&presets[name])||data||betDefaults;
  $("bet-strategy").value=values.strategy||"SMART";
  $("bet-percentage").value=values.percentage??5;
  $("bet-percentage_gap").value=values.percentage_gap??20;
  $("bet-max_points").value=values.max_points??50000;
  $("bet-minimum_points").value=values.minimum_points??0;
  $("bet-delay").value=values.delay??6;
  $("bet-delay_mode").value=values.delay_mode||"FROM_END";
  $("bet-filter_by").value=values.filter_by||"TOTAL_USERS";
  $("bet-filter_condition").value=values.filter_condition||"GT";
  $("bet-filter_value").value=values.filter_value??200;
  $("bet-filter_enabled").checked=Boolean(values.filter_enabled);
  $("bet-stealth_mode").checked=Boolean(values.stealth_mode);
  const names=["Оптимальный",...Object.keys(presets||{}).sort()];
  $("preset-select").innerHTML=names.map(item=>'<option value="'+esc(item)+'">'+esc(item)+'</option>').join("");
  $("preset-select").value=names.includes(name)?name:"Оптимальный";
  const settingsPreset=$("settings-preset-select");
  if(settingsPreset){
    settingsPreset.innerHTML=names.map(item=>'<option value="'+esc(item)+'">'+esc(item)+'</option>').join("");
    settingsPreset.value=$("preset-select").value;
  }
  $("open-bets").textContent="⚙　Прогнозы · "+$("preset-select").value;
}

function openBetSettings(){
  if(appState) fillPreset(appState.selected_preset,appState.bet_settings,appState.custom_presets);
  setFeedback("bet-feedback","Настройки применяются при следующем запуске сбора.");
  setPage("bets");
}

async function applyBets(){
  if(!ensureStartupReady()) return;
  try{
    const values=collectBet();
    const result=await api().apply_bet_settings(values,$("preset-select").value);
    setFeedback("bet-feedback",result.message||"Параметры применены.");
    await poll();
    $("open-bets").textContent="⚙　Прогнозы · "+$("preset-select").value;
  }catch(error){
    setFeedback("bet-feedback",error.message||"Проверь настройки.",true);
  }
}

async function savePreset(){
  if(!ensureStartupReady()) return;
  try{
    const values=collectBet();
    const result=await api().save_preset($("preset-name").value,values);
    setFeedback("bet-feedback",result.message||"",!result.ok);
    if(result.ok){
      await poll();
      fillPreset(appState.selected_preset,appState.bet_settings,appState.custom_presets);
      $("preset-name").value="";
    }
  }catch(error){
    setFeedback("bet-feedback",error.message||"Не удалось сохранить пресет.",true);
  }
}

async function deletePreset(){
  if(!ensureStartupReady()) return;
  try{
    const result=await api().delete_preset($("preset-select").value);
    setFeedback("bet-feedback",result.message||"",!result.ok);
    if(result.ok){
      await poll();
      fillPreset("Оптимальный",appState.bet_settings,appState.custom_presets);
    }
  }catch(error){
    setFeedback("bet-feedback",error.message||"Не удалось удалить пресет.",true);
  }
}

async function openFolder(){
  try{
    const result=await api().open_app_folder();
    if(!result.ok) notify(result.message||"Не удалось открыть папку.");
  }catch(error){
    notify(error.message||"Не удалось открыть папку.");
  }
}

async function copyLogs(){
  const textValue=filteredLogEntries().map(item=>item.line).join("\n");
  try{
    await navigator.clipboard.writeText(textValue);
    notify("Логи скопированы.");
  }catch(error){
    const area=document.createElement("textarea");
    area.value=textValue;
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    area.remove();
    notify("Логи скопированы.");
  }
}

async function clearLogs(){
  try{
    const result=await api().clear_logs();
    if(!result.ok){notify(result.message||"Не удалось очистить журнал.");return;}
    logEntries=[];
    pendingLogRows=[];
    lastLogId=Number(result.last_log_id||lastLogId);
    logsLoaded=true;
    renderLogs();
    notify(result.message||"Журнал очищен.");
  }catch(error){
    notify(error.message||"Не удалось очистить журнал.");
  }
}


function renderSettingsSummary(state){
  const username=String(state.authenticated_username||state.username||"");
  const profile=state.account_profile||{};
  const display=String(profile.display_name||username||"Twitch");
  $("settings-account-name").textContent=display;
  $("settings-account-login").textContent=username?"@"+username:"Аккаунт не подключён";
  $("settings-account-status").textContent=state.authenticated?(state.session_running?"Фарм работает":"Сессия Twitch подключена"):"Нет активной сессии";
  $("logout-account").disabled=!state.authenticated||Boolean(state.session_running);
  const avatar=$("settings-account-avatar");
  const avatarUrl=String(profile.avatar_url||"");
  const initial=esc(display.charAt(0).toUpperCase()||"?");
  if(avatar.dataset.url!==avatarUrl){
    avatar.dataset.url=avatarUrl;
    avatar.innerHTML=avatarUrl.startsWith("https://")?"<img src='"+esc(avatarUrl)+"' alt='' onerror='this.remove()'>":initial;
  }
  const stats=state.cache_stats||{};
  $("cache-avatar-count").textContent=Number(stats.avatars||0).toLocaleString("ru-RU");
  $("cache-currency-count").textContent=Number(stats.currency_icons||0).toLocaleString("ru-RU");
}

async function clearAvatarCache(){
  try{
    const result=await api().clear_avatar_cache();
    notify("Очищено аватарок: "+Number(result.cleared||0));
    await poll();
  }catch(error){
    notify(error.message||"Не удалось очистить кэш аватарок.");
  }
}

async function clearCurrencyCache(){
  try{
    const result=await api().clear_currency_cache();
    if(!result.ok){notify(result.message||"Не удалось очистить кэш.");return;}
    notify("Кэш иконок очищен. Обновляю оформление…");
    await api().refresh_channel_points_branding();
    await poll();
  }catch(error){
    notify(error.message||"Не удалось очистить кэш оформления.");
  }
}

async function logoutAccount(){
  if(!appState||!appState.authenticated) return;
  if(appState.session_running){
    notify("Сначала останови фарм.");
    return;
  }
  const button=$("logout-account");
  button.disabled=true;
  try{
    const result=await api().logout();
    if(!result.ok){notify(result.message||"Не удалось выйти.");button.disabled=false;return;}
    appState=null;
    appInitialized=false;
    notify(result.message||"Аккаунт отключён.");
    await poll();
  }catch(error){
    button.disabled=false;
    notify(error.message||"Не удалось выйти из аккаунта.");
  }
}

function renderDropsSection(campaigns,root,isSubscription){
  const groups=campaigns.map(campaign=>({
    campaign,
    drops:(isSubscription?campaign.subscription_drops:campaign.watch_drops)||[]
  })).filter(item=>item.drops.length);
  if(!groups.length){
    root.innerHTML="<div class='drops-empty'>"+(isSubscription?"Нет активных Drops для подписки.":"Нет активных наград за просмотр.")+"</div>";
    return;
  }
  root.innerHTML=groups.map(({campaign,drops})=>{
    const gameImage=String(campaign.game_image||"");
    const gameMarkup=gameImage.startsWith("https://")?"<img src='"+esc(gameImage)+"' alt='' loading='lazy' onerror='this.remove()'>":"<span class='drop-game-fallback'><svg><use href='#i-gift'></use></svg></span>";
    const endDate=campaign.end_at?new Date(campaign.end_at).toLocaleDateString("ru-RU",{day:"2-digit",month:"short",year:"numeric"}):"—";
    const rewardMarkup=drops.map(drop=>{
      const rewardImage=String(drop.image_url||"");
      const rewardThumb=rewardImage.startsWith("https://")?"<img src='"+esc(rewardImage)+"' alt='' loading='lazy' onerror='this.remove()'>":"<span class='drop-reward-fallback'><svg><use href='#i-gift'></use></svg></span>";
      const claimed=Boolean(drop.claimed);
      const progress=Math.max(0,Math.min(100,Number(drop.percentage||0)));
      const watched=Number(drop.watched_minutes||0);
      const required=Number(drop.required_minutes||0);
      const remaining=Number(drop.remaining_minutes||Math.max(0,required-watched));
      const subscriptionRequired=Number(drop.required_subscriptions||0);
      const subscriptionCurrent=Number(drop.current_subscriptions||0);
      const requirementParts=[];
      if(required>0) requirementParts.push("Просмотр: "+watched+" / "+required+" мин — осталось "+remaining+" мин");
      if(subscriptionRequired>0) requirementParts.push(drop.subscriptions_progress_known?"Подписок: "+subscriptionCurrent+" / "+subscriptionRequired:"Нужно подписок: "+subscriptionRequired);
      const progressText=claimed?"Получено":isSubscription
        ?(requirementParts.join(" · ")||"Требуется платная или подарочная подписка Twitch")
        :watched+" / "+required+" мин — осталось "+remaining+" мин";
      const progressMarkup=isSubscription
        ?"<div class='drop-progress-track'><span style='width:"+progress+"%'></span></div><div class='drop-progress-label'><span>"+esc(progressText)+"</span><strong>"+progress+"%</strong></div>"
        :"<div class='drop-progress-track'><span style='width:"+progress+"%'></span></div><div class='drop-progress-label'><span>"+esc(progressText)+"</span><strong>"+progress+"%</strong></div>";
      const status=claimed?"<span class='drop-claimed'>Получено</span>":"<span class='drop-active'>"+(isSubscription?"Подписка":"В процессе")+"</span>";
      return "<div class='drop-reward-row'><div class='drop-reward-image'>"+rewardThumb+"</div><div class='drop-reward-copy'><strong>"+esc(drop.name||"Награда")+"</strong><small>"+esc(drop.benefit||campaign.name||"Награда Twitch")+"</small>"+progressMarkup+"</div>"+status+"</div>";
    }).join("");
    const streamers=Array.isArray(campaign.streamers)?campaign.streamers:[];
    const streamMarkup=streamers.length?streamers.map(streamer=>{
      const login=String(streamer.login||"").toLowerCase();
      const name=String(streamer.display_name||login);
      const avatar=String(streamer.avatar_url||"");
      const image=avatar.startsWith("https://")?"<img src='"+esc(avatar)+"' alt='' loading='lazy' onerror='this.remove()'>":"<span>"+esc(name.charAt(0).toUpperCase()||"?")+"</span>";
      const added=Boolean(appState&&Array.isArray(appState.channels)&&appState.channels.includes(login));
      const running=Boolean(appState&&appState.session_running);
      const button=added&&running?"<button class='button button-soft drop-streamer-add' disabled>Уже в фарме</button>":"<button class='button button-primary drop-streamer-add' data-drop-action='add' data-login='"+esc(login)+"'>"+(added?"Запустить фарм":"Добавить в фарм")+"</button>";
      return "<div class='drop-streamer'><div class='drop-streamer-avatar'>"+image+"</div><div class='drop-streamer-info'><strong>"+esc(name)+"</strong><small><i></i>В эфире · "+Number(streamer.viewer_count||0).toLocaleString("ru-RU")+" зрителей</small></div>"+button+"</div>";
    }).join(""):"<div class='drop-no-streamers'>Среди фолловеров сейчас нет подходящих стримеров в эфире.</div>";
    return "<article class='drop-campaign-card'><div class='drop-campaign-head'><div class='drop-game-image'>"+gameMarkup+"</div><div class='drop-campaign-copy'><span class='drop-game-name'>"+esc(campaign.game||"Twitch Drops")+"</span><strong>"+esc(campaign.name||"Активная кампания")+"</strong><small>Кампания до "+esc(endDate)+"</small></div><span class='drop-campaign-badge'>"+(isSubscription?"SUB DROP":"WATCH DROP")+"</span></div><div class='drop-reward-list'>"+rewardMarkup+"</div><div class='drop-streamers-heading'>Подходящие стримеры из фолловеров <span>"+streamers.length+"</span></div><div class='drop-streamer-list'>"+streamMarkup+"</div></article>";
  }).join("");
}

function renderDropsInventory(state){
  const campaigns=Array.isArray(state&&state.campaigns)?state.campaigns:[];
  const watchCount=campaigns.reduce((sum,item)=>sum+(item.watch_drops||[]).length,0);
  const subCount=campaigns.reduce((sum,item)=>sum+(item.subscription_drops||[]).length,0);
  $("drops-campaign-count").textContent=String(campaigns.length);
  $("drops-badge").textContent=String(campaigns.length);
  $("drops-badge").classList.toggle("visible",campaigns.length>0);
  $("drops-reward-count").textContent=String(watchCount+subCount);
  $("drops-watch-count").textContent=String(watchCount);
  $("drops-sub-count").textContent=String(subCount);
  $("drops-updated").textContent=Number(state.updated_at||0)?"Обновлено "+new Date(Number(state.updated_at)*1000).toLocaleTimeString("ru-RU",{hour:"2-digit",minute:"2-digit"}):"";
  const status=$("drops-status");
  status.classList.toggle("is-error",Boolean(state.error));
  if(state.loading){
    status.textContent="Синхронизирую инвентарь Twitch Drops и список подходящих эфиров…";
  }else if(state.error){
    status.textContent="Не удалось получить Drops: "+state.error;
  }else if(state.followers_error){
    status.textContent="Drops загружены, но список фолловеров недоступен: "+state.followers_error;
  }else if(state.loaded){
    status.textContent=campaigns.length?"Активных кампаний: "+campaigns.length+". Прогресс получен из Twitch GraphQL.":"Активных кампаний Drops сейчас нет.";
  }else{
    status.textContent="Нажми «Обновить прогресс», чтобы загрузить кампании.";
  }
  renderDropsSection(campaigns,$("drops-watch-list"),false);
  renderDropsSection(campaigns,$("drops-sub-list"),true);
}

function stopDropsPolling(){
  if(dropsPollTimer!==null){
    clearInterval(dropsPollTimer);
    dropsPollTimer=null;
  }
}

function watchDropsInventory(sequence){
  stopDropsPolling();
  dropsPollTimer=setInterval(async()=>{
    if(sequence!==dropsLoadSequence){
      stopDropsPolling();
      return;
    }
    try{
      const state=await api().get_drops_inventory_state();
      if(currentPage==="drops") renderDropsInventory(state);
      if(!state.loading) stopDropsPolling();
    }catch(error){
      stopDropsPolling();
      if(currentPage==="drops") notify(error.message||"Не удалось получить прогресс Drops.");
    }
  },1200);
}

async function loadDropsInventory(force=false){
  if(!apiReady||!pyApi) return;
  if(!appState||!appState.authenticated){
    $("drops-status").textContent="Сначала подключи аккаунт Twitch.";
    return;
  }
  const sequence=++dropsLoadSequence;
  const button=$("refresh-drops");
  if(force&&button){button.disabled=true;button.textContent="Обновление…";}
  try{
    let state=await withTimeout(api().get_drops_inventory_state(),10000,"Twitch не ответил на запрос Drops.");
    renderDropsInventory(state);
    if(state.loading){
      watchDropsInventory(sequence);
      return;
    }
    if(force||!state.loaded){
      const result=await withTimeout(api().refresh_drops_inventory(Boolean(force)),10000,"Не удалось запустить обновление Drops.");
      if(!result.ok){notify(result.message||"Не удалось обновить Drops.");return;}
      state=await api().get_drops_inventory_state();
      renderDropsInventory(state);
      if(state.loading) watchDropsInventory(sequence);
    }
  }catch(error){
    $("drops-status").textContent=error.message||"Не удалось загрузить инвентарь Drops.";
    $("drops-status").classList.add("is-error");
  }finally{
    if(button){
      button.disabled=false;
      button.innerHTML="<svg><use href='#i-refresh'></use></svg>Обновить прогресс";
    }
  }
}

async function addDropChannelToFarm(login,button){
  if(!login||!apiReady) return;
  if(button){button.disabled=true;button.textContent="Добавление…";}
  try{
    const result=await withTimeout(api().add_drop_channel_to_farm(login),30000,"Добавление стримера заняло слишком много времени.");
    if(!result.ok){
      notify(result.message||"Не удалось добавить стримера.");
      if(button){button.disabled=false;button.textContent="Добавить в фарм";}
      return;
    }
    notify(result.message||"Канал добавлен в фарм.");
    await poll();
    const state=await api().get_drops_inventory_state();
    renderDropsInventory(state);
  }catch(error){
    notify(error.message||"Не удалось добавить стримера.");
    if(button){button.disabled=false;button.textContent="Добавить в фарм";}
  }
}

function showTutorial(){
  tutorialShown=true;
  tutorialStep=0;
  setPage("dashboard");
  renderTutorialStep();
  $("tutorial-overlay").classList.add("visible");
}

function renderTutorialStep(){
  document.querySelectorAll(".tour-highlight").forEach(node=>node.classList.remove("tour-highlight"));
  const step=tutorialSteps[tutorialStep];
  (step.targets||[step.target]).forEach(selector=>{
    const target=document.querySelector(selector);
    if(target) target.classList.add("tour-highlight");
  });
  const finalStep=tutorialStep===tutorialSteps.length-1;
  $("tutorial-step").textContent=(tutorialStep+1)+" / "+tutorialSteps.length;
  $("tutorial-title").textContent=step.title;
  $("tutorial-text").textContent=step.text;
  $("tutorial-next").textContent=finalStep?"Готово":"Далее";
  $("tutorial-skip").style.display=finalStep?"none":"inline-flex";
}

function hideTutorial(save){
  $("tutorial-overlay").classList.remove("visible");
  document.querySelectorAll(".tour-highlight").forEach(node=>node.classList.remove("tour-highlight"));
  if(save&&apiReady) api().finish_tutorial();
}

function nextTutorial(){
  if(tutorialStep<tutorialSteps.length-1){
    tutorialStep++;
    renderTutorialStep();
  }else{
    hideTutorial(true);
  }
}

async function waitForStartup(){
  const started=Date.now();
  while(true){
    const result=await withTimeout(api().get_startup_state(),5000,"Python не отвечает на запрос состояния запуска.");
    const phase=result&&result.phase?result.phase:"Подготовка настроек...";
    const elapsed=Number(result&&result.elapsed||((Date.now()-started)/1000));
    setStartupMessage(phase+" ("+elapsed.toFixed(1)+" сек.)");
    if(result&&result.ready){
      startupCompleteUi=true;
      if(result.error) notify("Ошибка загрузки конфигурации: "+result.error);
      updateFarmToggleButton(false);
      return result;
    }
    if(Date.now()-started>20000) throw new Error("Подготовка приложения зависла на этапе «"+phase+"». Перезапусти приложение.");
    await sleep(160);
  }
}


function initLogoAnimation(){
  const logo=document.querySelector(".brand-mark");
  if(!logo)return;
  const left=logo.querySelector(".brand-eye-left");
  const right=logo.querySelector(".brand-eye-right");
  if(!left||!right)return;
  let token=0;
  let timer=null;
  const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  const setEyes=(x=0,y=0,leftScale=1,rightScale=1)=>{
    left.style.transform="translate("+x+"px,"+y+"px) scaleY("+leftScale+")";
    right.style.transform="translate("+x+"px,"+y+"px) scaleY("+rightScale+")";
  };
  const reset=()=>setEyes();
  const schedule=()=>{
    clearTimeout(timer);
    timer=setTimeout(()=>runRandom(),2000+Math.random()*2000);
  };
  const blink=async current=>{
    if(current!==token)return;
    setEyes(0,0,.08,.08);
    await pause(150);
    if(current!==token)return;
    reset();
    await pause(280);
  };
  const doubleBlink=async current=>{
    await blink(current);
    if(current!==token)return;
    await pause(100);
    await blink(current);
  };
  const squint=async current=>{
    if(current!==token)return;
    setEyes(0,0,.45,.45);
    await pause(1500+Math.random()*500);
    if(current!==token)return;
    reset();
    await pause(280);
  };
  const glance=async current=>{
    const directions=[[-2,0],[2,0],[2,-2],[0,2]];
    for(const [x,y] of directions){
      if(current!==token)return;
      setEyes(x,y);
      await pause(380);
    }
    if(current!==token)return;
    await pause(1000);
    if(current!==token)return;
    reset();
    await pause(280);
  };
  const runRandom=async()=>{
    const current=++token;
    const actions=[blink,doubleBlink,squint,glance];
    const action=actions[Math.floor(Math.random()*actions.length)];
    await action(current);
    if(current===token&&!logo.matches(":hover"))schedule();
  };
  const hoverReaction=async()=>{
    clearTimeout(timer);
    const current=++token;
    logo.classList.add("logo-hovered");
    setEyes(0,0,.08,1.25);
    await pause(180);
    if(current!==token)return;
    reset();
    await pause(130);
    if(current!==token)return;
    setEyes(0,0,1.2,.08);
    await pause(180);
    if(current!==token)return;
    reset();
  };
  logo.addEventListener("pointerenter",()=>{
    hoverReaction();
  });
  logo.addEventListener("pointerleave",()=>{
    token++;
    reset();
    logo.classList.remove("logo-hovered");
    schedule();
  });
  runRandom();
}

async function initialize(){
  await waitForBridge();
  await waitForStartup();
  setStartupMessage("Загрузка профиля и списка каналов...");
  setupBetControls();
  lastLogId=0;
  const authState=await withTimeout(api().get_auth_state(),10000,"Не удалось получить состояние авторизации.");
  const state=await withTimeout(api().get_state(0),10000,"Не удалось загрузить состояние приложения.");
  state.authenticated=Boolean(authState.authenticated);
  state.authenticated_username=authState.username||state.authenticated_username;
  state.auth_status=authState.auth_status||state.auth_status;
  state.auth_code=authState.auth_code||state.auth_code;
  state.auth_error=authState.auth_error||state.auth_error;
  state.worker_alive=Boolean(authState.worker_alive);
  state.worker_state=authState.worker_state||"idle";
  state.restore_pending=Boolean(authState.restore_pending);
  state.elapsed=Number(authState.elapsed||0);
  applyState(state,true);
  appInitialized=Boolean(authState.authenticated);
  $("startup-overlay").classList.add("hidden");
  initialStateReady=true;
  updateFarmToggleButton(Boolean(state.session_running));
  setInterval(poll,900);
}

document.addEventListener("DOMContentLoaded",()=>{
  initLogoAnimation();
  $("channel-search").addEventListener("input",event=>{
    channelSearch=event.target.value||"";
    filterChannelCards();
  });
  document.addEventListener("keydown",event=>{
    if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==="k"){
      event.preventDefault();
      $("channel-search").focus();
      $("channel-search").select();
    }
    if(event.key==="Escape"&&$("delete-confirm-overlay").classList.contains("visible")){
      closeDeleteConfirmation();
      event.preventDefault();
      return;
    }
    if(event.key==="Escape"&&currentPage==="channel"){
      setPage("dashboard");
      event.preventDefault();
      return;
    }
    if(event.key==="Escape"&&document.activeElement===$("channel-search")){
      $("channel-search").value="";
      channelSearch="";
      filterChannelCards();
      $("channel-search").blur();
    }
  });
  $("window-back").addEventListener("click",()=>navigateHistory(-1));
  $("window-forward").addEventListener("click",()=>navigateHistory(1));
  $("window-minimize").addEventListener("click",minimizeWindow);
  $("window-maximize").addEventListener("click",toggleMaximizeWindow);
  $("window-close").addEventListener("click",closeWindow);
  $("nav-dashboard").addEventListener("click",()=>setPage("dashboard"));
  $("nav-drops").addEventListener("click",()=>focusOption("claim_drops","nav-drops"));
  $("nav-bonuses").addEventListener("click",()=>focusOption("claim_moments","nav-bonuses"));
  $("nav-raids").addEventListener("click",()=>focusOption("follow_raid","nav-raids"));
  $("nav-predictions").addEventListener("click",openBetSettings);
  $("nav-chat").addEventListener("click",()=>focusOption("chat","nav-chat"));
  $("nav-events").addEventListener("click",()=>setPage("logs"));
  $("nav-followers").addEventListener("click",()=>setPage("followers"));
  $("nav-drops-inventory").addEventListener("click",()=>setPage("drops"));
  $("nav-settings").addEventListener("click",()=>setPage("settings"));
  $("refresh-drops").addEventListener("click",()=>loadDropsInventory(true));
  $("page-drops").addEventListener("click",event=>{
    const button=event.target.closest("[data-drop-action='add']");
    if(button) addDropChannelToFarm(button.dataset.login,button);
  });
  $("refresh-followers").addEventListener("click",()=>loadFollowedChannels(true));
  $("page-followers").addEventListener("click",event=>{
    const button=event.target.closest("[data-followers-action]");
    if(!button) return;
    const action=button.dataset.followersAction;
    if(action==="add") addFollowedChannel(button.dataset.login,button);
    if(action==="reauth"){
      const username=appState&&(appState.authenticated_username||appState.username);
      if(username) loginUser(username);
    }
  });
  $("open-folder").addEventListener("click",openFolder);
  $("clear-avatar-cache").addEventListener("click",clearAvatarCache);
  $("clear-currency-cache").addEventListener("click",clearCurrencyCache);
  $("logout-account").addEventListener("click",logoutAccount);
  $("add-channel").addEventListener("click",()=>openChannelEditor("add",""));
  $("page-channel").addEventListener("click",event=>{if(event.target===$("page-channel"))setPage("dashboard");});
  $("channel-chips").addEventListener("click",event=>{
    const button=event.target.closest("[data-remove-channel]");
    if(!button) return;
    channelDraftTokens=channelDraftTokens.filter(item=>item!==button.dataset.removeChannel);
    updateChannelPreview();
  });
  $("channel-input").addEventListener("keydown",event=>{
    if(channelMode!=="add") return;
    if(event.key==="Enter"||event.key===" "||event.key===","){
      const value=$("channel-input").value.trim();
      if(value){
        event.preventDefault();
        addChannelTokens(channelTokens(value));
        $("channel-input").value="";
      }else if(event.key==="Enter"&&channelDraftTokens.length){
        event.preventDefault();
        saveChannel();
      }else if(event.key===" "||event.key===","){
        event.preventDefault();
      }
    }else if(event.key==="Backspace"&&!$("channel-input").value&&channelDraftTokens.length){
      channelDraftTokens.pop();
      updateChannelPreview();
    }
  });
  $("channel-input").addEventListener("paste",event=>{
    if(channelMode!=="add") return;
    const pasted=event.clipboardData&&event.clipboardData.getData("text")||"";
    if(/[\s,;]/.test(pasted)){
      event.preventDefault();
      addChannelTokens(channelTokens(pasted));
      $("channel-input").value="";
    }
  });
  $("channel-remove").addEventListener("click",()=>removeChannel(editingChannel));
  $("delete-confirm-overlay").addEventListener("click",event=>{if(event.target===$("delete-confirm-overlay"))closeDeleteConfirmation();});
  $("delete-cancel-button").addEventListener("click",closeDeleteConfirmation);
  $("delete-confirm-button").addEventListener("click",confirmDeleteChannel);
  $("startup-retry").addEventListener("click",async()=>{
    const button=$("startup-retry");
    button.disabled=true;
    button.textContent="Перезапуск...";
    try{
      if(apiReady&&pyApi&&typeof pyApi.restart_app==="function"){
        const result=await pyApi.restart_app();
        throw new Error(result&&result.message||"Не удалось перезапустить приложение.");
      }
      window.location.reload();
    }catch(error){
      button.disabled=false;
      button.textContent="Повторить запуск";
      setStartupMessage(error.message||"Не удалось перезапустить приложение.");
    }
  });
  $("refresh-channel-branding").addEventListener("click",refreshChannelBranding);
  $("channel-list").addEventListener("dragstart",event=>{
    const handle=event.target.closest(".channel-drag-handle");
    if(!handle||handle.disabled||appState&&appState.session_running){event.preventDefault();return;}
    draggedChannelCard=handle.closest(".channel-card");
    if(!draggedChannelCard){event.preventDefault();return;}
    draggedChannelCard.classList.add("is-dragging");
    event.dataTransfer.effectAllowed="move";
    event.dataTransfer.setData("text/plain",draggedChannelCard.dataset.channel||"");
  });
  $("channel-list").addEventListener("dragover",event=>{
    if(!draggedChannelCard||channelDropPending)return;
    const target=event.target.closest(".channel-card");
    if(!target||target===draggedChannelCard)return;
    event.preventDefault();
    const rect=target.getBoundingClientRect();
    const after=event.clientY>rect.top+rect.height/2;
    $("channel-list").insertBefore(draggedChannelCard,after?target.nextSibling:target);
    target.classList.add("drop-target");
    $("channel-list").querySelectorAll(".channel-card.drop-target").forEach(card=>{if(card!==target)card.classList.remove("drop-target");});
  });
  $("channel-list").addEventListener("dragleave",event=>{
    const target=event.target.closest(".channel-card");
    if(target&&!target.contains(event.relatedTarget))target.classList.remove("drop-target");
  });
  $("channel-list").addEventListener("drop",async event=>{
    if(!draggedChannelCard)return;
    event.preventDefault();
    const order=Array.from($("channel-list").querySelectorAll(".channel-card")).map(card=>card.dataset.channel).filter(Boolean);
    draggedChannelCard.classList.remove("is-dragging");
    $("channel-list").querySelectorAll(".channel-card").forEach(card=>card.classList.remove("drop-target"));
    draggedChannelCard=null;
    if(!appState||JSON.stringify(order)===JSON.stringify(appState.channels||[]))return;
    channelDropPending=true;
    try{
      const result=await api().reorder_channels(order);
      if(!result.ok)throw new Error(result.message||"Не удалось сохранить порядок каналов.");
      appState.channels=order.slice();
      channelCache="";
      await poll();
      notify("Порядок каналов сохранён.");
    }catch(error){
      channelCache="";
      await poll();
      notify(error.message||"Не удалось сохранить порядок каналов.");
    }finally{
      channelDropPending=false;
    }
  });
  $("channel-list").addEventListener("dragend",()=>{
    if(draggedChannelCard)draggedChannelCard.classList.remove("is-dragging");
    draggedChannelCard=null;
    $("channel-list").querySelectorAll(".channel-card").forEach(card=>card.classList.remove("drop-target"));
  });
  $("channel-list").addEventListener("click",event=>{
    const button=event.target.closest("button[data-action]");
    if(!button) return;
    const channel=button.dataset.channel;
    if(button.dataset.action==="open"){
      api().open_url("https://www.twitch.tv/"+encodeURIComponent(channel));
      return;
    }
    if(button.dataset.action==="edit") openChannelEditor("edit",channel);
    if(button.dataset.action==="remove") removeChannel(channel);
  });
  $("account-connect-button").addEventListener("click",()=>{
    const username=appState&&(appState.authenticated_username||appState.username);
    if(username) loginUser(username);
  });
  $("gate-login-button").addEventListener("click",gateLogin);
  $("gate-username").addEventListener("keydown",event=>{if(event.key==="Enter")gateLogin();});
  $("tutorial-next").addEventListener("click",nextTutorial);
  $("tutorial-skip").addEventListener("click",()=>hideTutorial(true));
  $("farm-toggle").addEventListener("click",toggleFarm);
  $("save-button").addEventListener("click",saveSettings);
  $("back-from-logs").addEventListener("click",()=>setPage("dashboard"));
  $("back-from-channel").addEventListener("click",()=>setPage("dashboard"));
  $("cancel-channel").addEventListener("click",()=>setPage("dashboard"));
  $("save-channel").addEventListener("click",saveChannel);
  $("channel-input").addEventListener("keydown",event=>{
    if(channelMode==="edit"&&event.key==="Enter"){
      event.preventDefault();
      saveChannel();
    }
  });
  $("open-bets").addEventListener("click",openBetSettings);
  $("back-from-bets").addEventListener("click",()=>setPage("settings"));
  $("preset-select").addEventListener("change",()=>fillPreset($("preset-select").value,appState&&appState.bet_settings,appState&&appState.custom_presets));
  $("settings-preset-select").addEventListener("change",()=>fillPreset($("settings-preset-select").value,appState&&appState.bet_settings,appState&&appState.custom_presets));
  $("load-preset").addEventListener("click",()=>fillPreset($("preset-select").value,appState&&appState.bet_settings,appState&&appState.custom_presets));
  $("apply-bets").addEventListener("click",applyBets);
  $("save-preset").addEventListener("click",savePreset);
  $("delete-preset").addEventListener("click",deletePreset);
  $("copy-logs").addEventListener("click",copyLogs);
  $("clear-logs").addEventListener("click",clearLogs);
  $("log-search").addEventListener("input",event=>{
    logSearch=event.target.value||"";
    renderLogs();
  });
  document.querySelectorAll("[data-log-filter]").forEach(button=>{
    button.addEventListener("click",()=>{
      logFilter=button.dataset.logFilter;
      document.querySelectorAll("[data-log-filter]").forEach(item=>item.classList.toggle("active",item===button));
      renderLogs();
    });
  });
  setInterval(()=>{
    if(currentPage==="drops"&&appState&&appState.session_running) loadDropsInventory(true);
  },150000);
  initialize().catch(error=>{
    const message=error&&error.message?error.message:"Не удалось запустить интерфейс.";
    $("startup-title").textContent="Не удалось загрузить приложение";
    $("startup-description").textContent=message;
    $("startup-spinner").style.display="none";
    $("startup-retry").style.display="inline-flex";
    $("startup-overlay").classList.remove("hidden");
    $("startup-overlay").classList.add("startup-error");
    $("startup-overlay").style.pointerEvents="auto";
  });
});
