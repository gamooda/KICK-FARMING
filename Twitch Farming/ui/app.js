const $=id=>document.getElementById(id);
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let appState=null;
let pyApi=null;
let apiReady=false;
let apiReadyPromise=null;
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
let logsLoaded=false;
let logsLoading=false;
let pendingLogRows=[];
let pollErrorShown=false;
let tutorialShown=false;
let appInitialized=false;
let tutorialStep=0;
const tutorialSteps=[
  {title:"Добавление каналов",text:"Нажми + и добавь Twitch-каналы по логину или ссылке. Аватары, имена и статус трансляции загрузятся автоматически.",target:".channels-panel"},
  {title:"Автоматизация",text:"Drops, бонусы, рейды, прогнозы и IRC-чат включаются в разделе «Настройки» бокового меню.",target:"#nav-settings"},
  {title:"Запуск и логи",text:"Нажми «Запустить». Награды и изменения баланса появятся в ленте событий. Полный технический журнал открывается кнопкой «Логи».",target:"#start-button"}
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

async function waitForBridge(){
  if(!apiReadyPromise) apiReadyPromise=window.__twitchPywebviewReadyPromise;
  if(!apiReadyPromise) throw new Error("Событие готовности PyWebView не зарегистрировано.");
  await apiReadyPromise;
  const bridge=window.pywebview&&window.pywebview.api;
  if(!bridge||typeof bridge!=="object") throw new Error("PyWebView сообщил о готовности, но Python API не передан в окно.");
  const required=["get_auth_state","get_state","login","minimize_window","toggle_maximize_window","close_window"];
  const missing=required.filter(name=>typeof bridge[name]!=="function");
  if(missing.length) throw new Error("Python API не содержит методов: "+missing.join(", "));
  pyApi=bridge;
  apiReady=true;
  return pyApi;
}

function setPage(page,remember=true){
  if(remember&&page!==currentPage){
    pageHistory=pageHistory.slice(0,pageHistoryIndex+1);
    pageHistory.push(page);
    pageHistoryIndex=pageHistory.length-1;
  }
  currentPage=page;
  document.querySelectorAll(".page").forEach(item=>item.classList.toggle("active",item.id==="page-"+page));
  document.querySelectorAll(".nav-button").forEach(item=>item.classList.remove("active"));
  const navMap={dashboard:"nav-dashboard",settings:"nav-settings",bets:"nav-predictions",logs:"nav-events",channel:"nav-dashboard"};
  const active=$(navMap[page]||"nav-dashboard");
  if(active) active.classList.add("active");
  updateReward();
  if(page==="logs") loadLogs();
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

function renderChannels(channels,online,running,profiles,watchStatuses,balances){
  const signature=JSON.stringify([channels,online,running,profiles,watchStatuses,balances]);
  if(signature===channelCache) return;
  channelCache=signature;
  const root=$("channel-list");
  $("channel-count").textContent=String(channels.length);
  if(!channels.length){
    root.innerHTML="<div class='empty-channels'><div class='empty-channel-icon'><svg><use href='#i-channel'></use></svg></div><strong>Пока нет каналов</strong><span>Добавь первый Twitch-канал кнопкой +</span></div>";
    $("add-channel").disabled=running;
    return;
  }
  root.innerHTML=channels.map(login=>{
    const profile=profiles&&profiles[login]?profiles[login]:{};
    const live=online.includes(login)||Boolean(profile.is_live);
    const loaded=Boolean(profile.loaded)||live;
    const status=live?"В ЭФИРЕ":profile.error?"ОШИБКА":loaded?"ОФФЛАЙН":"ПРОВЕРКА";
    const displayName=profile.display_name||login;
    const avatar=typeof profile.avatar_url==="string"&&profile.avatar_url.startsWith("https://")?profile.avatar_url:"";
    const points=balances&&Object.prototype.hasOwnProperty.call(balances,login)?Number(balances[login]||0):null;
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
    return "<article class='channel-card' data-search='"+searchKey+"'><div class='channel-card-top'><span class='channel-live-pill "+liveClass+"'><i class='channel-status-dot "+(live?"is-live":"")+"'></i>"+status+"</span><div class='channel-actions'><button class='channel-icon-button' data-action='edit' data-channel='"+esc(login)+"' "+(running?"disabled":"")+" title='Изменить'><svg><use href='#i-edit'></use></svg></button><button class='channel-icon-button remove' data-action='remove' data-channel='"+esc(login)+"' "+(running?"disabled":"")+" title='Удалить'><svg><use href='#i-trash'></use></svg></button></div></div>"+avatarMarkup+"<div class='channel-info'><div class='channel-name'>"+esc(displayName)+"</div><div class='channel-login'>@"+esc(login)+"</div><div class='channel-card-bottom'><svg class='channel-points-icon'><use href='#i-diamond'></use></svg><strong>"+pointsText+"</strong><span>баллов</span></div><div class='channel-extra'>"+extra+"</div></div></article>";
  }).join("");
  $("add-channel").disabled=running;
  filterChannelCards();
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

function appendLogRows(rows){
  if(!rows||!rows.length) return;
  if(logsLoading){
    pendingLogRows.push(...rows);
    return;
  }
  const box=$("log-view");
  for(const row of rows){
    if(row.id>lastLogId){
      if(box.textContent.length>42000) box.textContent=box.textContent.slice(-30000);
      box.textContent+=(box.textContent?"\n":"")+row.line;
      lastLogId=row.id;
    }
  }
  if(currentPage==="logs") box.scrollTop=box.scrollHeight;
}

function renderAccountSidebar(state){
  const profile=state.account_profile||(appState&&appState.account_profile)||{};
  const username=state.authenticated_username||state.username||(appState&&appState.authenticated_username)||"";
  const display=profile.display_name||username||"Twitch";
  const initial=String(display).charAt(0).toUpperCase()||"T";
  $("account-display-name").textContent=display;
  $("account-login").textContent=username?"@"+username:"Аккаунт не подключён";
  const farmRunning=Boolean(state.session_running||(appState&&appState.session_running));
  const statusText=state.authenticated?(farmRunning?"РАБОТАЕТ":"TWITCH ПОДКЛЮЧЁН"):"НЕ ПОДКЛЮЧЁН";
  const statusColor=state.authenticated?"#42d6a4":"#8792a8";
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
  $("login-gate").classList.toggle("visible",!authenticated);
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
  applyAuthState(state);
  const authenticated=Boolean(state.authenticated);
  $("gate-login-button").disabled=state.auth_status.includes("ПОДКЛЮЧЕНИЕ")||state.auth_status.includes("ПРОВЕРКА")||state.auth_status.includes("ОЖИДАЕТСЯ ПОДТВЕРЖДЕНИЕ");
  if(document.activeElement!==$("gate-username")) $("gate-username").value=state.username||"";
  if(!authenticated){
    const waiting=state.auth_status.includes("ОЖИДАЕТСЯ ПОДТВЕРЖДЕНИЕ");
    $("gate-status").textContent=state.auth_code&&!state.auth_code.includes("Сессия")?"Код Twitch: "+state.auth_code+". Подтверди вход в открывшемся браузере.":waiting?"Ожидается подтверждение входа в браузере Twitch…":state.auth_status.includes("ПОДКЛЮЧЕНИЕ")?"Выполняется подключение к Twitch…":state.auth_status.includes("ПРОВЕРКА")?"Проверяю сохранённую сессию Twitch…":state.auth_status.includes("НЕ УДАЛАСЬ")?"Не удалось подключиться. Проверь браузер и попробуй ещё раз.":"Введи логин и подключи аккаунт, чтобы открыть приложение.";
  }
  $("start-button").disabled=state.session_running;
  $("stop-button").disabled=!state.session_running;
  $("start-button").innerHTML=state.session_running?"<svg><use href='#i-play'></use></svg>Работает":"<svg><use href='#i-play'></use></svg>Запустить";
  $("session-label").textContent=state.session_running?"ФАРМ РАБОТАЕТ":String(state.status_text||"ОСТАНОВЛЕН").replace(/[●]/g,"").trim();
  $("session-dot").classList.toggle("is-running",Boolean(state.session_running));
  $("points-value").textContent=Number(state.gained_total||0).toLocaleString("ru-RU");
  $("online-value").textContent=String((state.online_channels||[]).length);
  $("events-value").textContent=String(state.event_total||0);
  renderChannels(state.channels||[],state.online_channels||[],state.session_running,state.channel_profiles||{},state.watch_status||{},state.channel_balances||{});
  renderRewards(state.rewards||[]);
  renderEvents(state.events||[]);
  if(initial){
    const options=state.options||{};
    Object.keys(options).forEach(key=>{
      const checkbox=$("opt-"+key);
      if(checkbox) checkbox.checked=Boolean(options[key]);
    });
    fillPreset(state.selected_preset,state.bet_settings,state.custom_presets);
  }
  if(authenticated&&!state.tutorial_done&&!tutorialShown) showTutorial();
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
    const lines=result.lines||[];
    box.textContent="";
    for(let index=0;index<lines.length;index+=35){
      box.textContent+=(box.textContent?"\n":"")+lines.slice(index,index+35).join("\n");
      await new Promise(resolve=>requestAnimationFrame(resolve));
    }
    lastLogId=Math.max(lastLogId,Number(result.last_log_id||0));
    logsLoaded=true;
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
    bet_settings:collectBet(),
    custom_presets:presets,
    selected_preset:appState&&appState.selected_preset?appState.selected_preset:"Оптимальный"
  };
}

async function saveSettings(){
  try{
    const result=await api().save_settings(collectPayload());
    notify(result.message||"Настройки сохранены.");
    await poll();
  }catch(error){
    notify(error.message||"Не удалось сохранить настройки.");
  }
}

async function startFarm(){
  try{
    const result=await api().start_farm(collectPayload());
    notify(result.message||"");
    await poll();
  }catch(error){
    notify(error.message||"Не удалось запустить фарм.");
  }
}

async function loginUser(username){
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

async function stopFarm(){
  try{
    const result=await api().stop_farm();
    notify(result.message||"");
    await poll();
  }catch(error){
    notify(error.message||"Не удалось остановить фарм.");
  }
}

function openChannelEditor(mode,oldChannel){
  channelMode=mode;
  editingChannel=oldChannel||"";
  $("channel-page-title").textContent=mode==="add"?"Добавить канал":"Изменить @"+oldChannel;
  $("channel-input").value=oldChannel||"";
  setFeedback("channel-feedback","");
  setPage("channel");
  $("channel-input").focus();
  $("channel-input").select();
}

async function saveChannel(){
  const raw=$("channel-input").value.trim();
  try{
    const result=await api().save_channel_editor(channelMode,editingChannel,raw);
    if(!result.ok){setFeedback("channel-feedback",result.message,true);return;}
    notify(result.message||"Канал сохранён.");
    channelCache="";
    await poll();
    setPage("dashboard");
  }catch(error){
    setFeedback("channel-feedback",error.message||"Не удалось сохранить канал.",true);
  }
}

async function removeChannel(login){
  if(!confirm("Удалить канал @"+login+" из списка?")) return;
  try{
    const result=await api().remove_channel(login);
    if(!result.ok){notify(result.message||"Не удалось удалить канал.");return;}
    channelCache="";
    await poll();
    notify("Канал удалён.");
  }catch(error){
    notify(error.message||"Не удалось удалить канал.");
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
  $("open-bets").textContent="⚙　Прогнозы · "+$("preset-select").value;
}

function openBetSettings(){
  if(appState) fillPreset(appState.selected_preset,appState.bet_settings,appState.custom_presets);
  setFeedback("bet-feedback","Настройки применяются при следующем запуске сбора.");
  setPage("bets");
}

async function applyBets(){
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
  try{
    await navigator.clipboard.writeText($("log-view").textContent);
    notify("Логи скопированы.");
  }catch(error){
    const range=document.createRange();
    range.selectNodeContents($("log-view"));
    const selection=window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    document.execCommand("copy");
    selection.removeAllRanges();
    notify("Логи скопированы.");
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
  const target=document.querySelector(step.target);
  if(target) target.classList.add("tour-highlight");
  $("tutorial-step").textContent=(tutorialStep+1)+" / "+tutorialSteps.length;
  $("tutorial-title").textContent=step.title;
  $("tutorial-text").textContent=step.text;
  $("tutorial-next").textContent=tutorialStep===tutorialSteps.length-1?"Готово":"Далее";
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

async function initialize(){
  await waitForBridge();
  setupBetControls();
  const authState=await api().get_auth_state();
  applyAuthState(authState);
  lastLogId=0;
  if(authState.authenticated){
    const state=await api().get_state(0);
    applyState(state,true);
    appInitialized=true;
    setPage("dashboard");
  }
  setInterval(poll,900);
}

document.addEventListener("DOMContentLoaded",()=>{
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
  $("nav-settings").addEventListener("click",()=>setPage("settings"));
  $("open-folder").addEventListener("click",openFolder);
  $("add-channel").addEventListener("click",()=>openChannelEditor("add",""));
  $("channel-list").addEventListener("click",event=>{
    const button=event.target.closest("button[data-action]");
    if(!button) return;
    const channel=button.dataset.channel;
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
  $("start-button").addEventListener("click",startFarm);
  $("stop-button").addEventListener("click",stopFarm);
  $("save-button").addEventListener("click",saveSettings);
  $("back-from-logs").addEventListener("click",()=>setPage("dashboard"));
  $("back-from-channel").addEventListener("click",()=>setPage("dashboard"));
  $("cancel-channel").addEventListener("click",()=>setPage("dashboard"));
  $("save-channel").addEventListener("click",saveChannel);
  $("channel-input").addEventListener("keydown",event=>{if(event.key==="Enter")saveChannel();});
  $("open-bets").addEventListener("click",openBetSettings);
  $("back-from-bets").addEventListener("click",()=>setPage("settings"));
  $("preset-select").addEventListener("change",()=>fillPreset($("preset-select").value,appState&&appState.bet_settings,appState&&appState.custom_presets));
  $("load-preset").addEventListener("click",()=>fillPreset($("preset-select").value,appState&&appState.bet_settings,appState&&appState.custom_presets));
  $("apply-bets").addEventListener("click",applyBets);
  $("save-preset").addEventListener("click",savePreset);
  $("delete-preset").addEventListener("click",deletePreset);
  $("copy-logs").addEventListener("click",copyLogs);
  initialize().catch(error=>{
    const message=error&&error.message?error.message:"Не удалось запустить интерфейс.";
    $("gate-status").textContent="Ошибка запуска интерфейса: "+message;
    $("gate-login-button").disabled=false;
    notify(message);
  });
});
