const $=id=>document.getElementById(id);
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let appState=null;
let currentPage="dashboard";
let lastLogId=0;
let channelMode="add";
let editingChannel="";
let channelCache="";
let polling=false;
let toastTimer=null;
let logsLoaded=false;
let logsLoading=false;
let pendingLogRows=[];
let tutorialShown=false;
let tutorialStep=0;
const tutorialSteps=[
  {title:"Добавление каналов",text:"Нажми + и добавь Twitch-каналы по логину или ссылке. Аватары, имена и статус трансляции загрузятся автоматически.",target:".channels-panel"},
  {title:"Автоматизация",text:"Здесь включаются Drops, серии просмотров, моменты, рейды, прогнозы и IRC-чат. Настройки прогнозов открываются отдельной кнопкой.",target:".automation-panel"},
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
  if(!window.pywebview||!window.pywebview.api) throw new Error("Связь с приложением ещё не готова.");
  return window.pywebview.api;
}

async function waitForBridge(){
  while(!window.pywebview||!window.pywebview.api) await sleep(100);
}

function setPage(page){
  currentPage=page;
  document.querySelectorAll(".page").forEach(item=>item.classList.toggle("active",item.id==="page-"+page));
  $("nav-dashboard").classList.toggle("active",page==="dashboard");
  $("nav-logs").classList.toggle("active",page==="logs");
  updateReward();
  if(page==="logs") loadLogs();
}

function setFeedback(id,message,error){
  const node=$(id);
  node.textContent=message||"";
  node.style.color=error?"#ff8797":"#e5b866";
}

function renderChannels(channels,online,running,profiles){
  const signature=JSON.stringify([channels,online,running,profiles]);
  if(signature===channelCache) return;
  channelCache=signature;
  const root=$("channel-list");
  if(!channels.length){
    root.innerHTML='<div class="empty-channels">Нажми + и добавь канал по логину или ссылке Twitch</div>';
    $("add-channel").disabled=running;
    return;
  }
  root.innerHTML=channels.map(login=>{
    const profile=profiles&&profiles[login]?profiles[login]:{};
    const live=online.includes(login)||Boolean(profile.is_live);
    const loaded=Boolean(profile.loaded)||live;
    const status=live?"● В ЭФИРЕ":profile.error?"ОШИБКА ПРОВЕРКИ":loaded?"НЕ В ЭФИРЕ":"ПРОВЕРКА…";
    const displayName=profile.display_name||login;
    const avatar=typeof profile.avatar_url==="string"&&profile.avatar_url.startsWith("https://")?profile.avatar_url:"";
    const title=profile.stream_title||"";
    const viewers=Number(profile.viewer_count||0);
    const details=live?(title||"ТРАНСЛЯЦИЯ В ЭФИРЕ"):status;
    const initial=esc(login.charAt(0).toUpperCase());
    const avatarMarkup=avatar?'<div class="channel-avatar" data-initial="'+initial+'"><img loading="lazy" src="'+esc(avatar)+'" alt="" onerror="this.parentNode.textContent=this.parentNode.dataset.initial"></div>':'<div class="channel-avatar">'+initial+'</div>';
    return '<div class="channel-card">'+avatarMarkup+'<div><div class="channel-name">'+esc(displayName)+'　<span style="color:#7d8aa0;font-weight:500">@'+esc(login)+'</span></div><div class="channel-state '+(live?"online":loaded?"":"pending")+'">'+esc(details)+'</div>'+(live&&viewers?'<div class="channel-meta">'+viewers.toLocaleString("ru-RU")+' зрителей</div>':'')+'</div><div class="channel-spacer"></div><button class="button button-soft channel-action" data-action="edit" data-channel="'+esc(login)+'" '+(running?"disabled":"")+' title="Изменить">✎</button><button class="button button-danger channel-action" data-action="remove" data-channel="'+esc(login)+'" '+(running?"disabled":"")+' title="Удалить">×</button></div>';
  }).join("");
  $("add-channel").disabled=running;
}

function renderEvents(events){
  const root=$("event-list");
  const html=events.length?events.map(line=>'<div class="event-line">'+esc(line)+'</div>').join(""):'<div class="empty-channels">События появятся после запуска фарма.</div>';
  if(root.dataset.signature!==html){
    const nearBottom=root.scrollHeight-root.scrollTop-root.clientHeight<35;
    root.innerHTML=html;
    root.dataset.signature=html;
    if(nearBottom) root.scrollTop=root.scrollHeight;
  }
}

function updateReward(){
  const reward=appState&&appState.reward;
  const visible=currentPage==="dashboard"&&reward&&reward.until>Date.now()/1000;
  $("reward-banner").classList.toggle("visible",Boolean(visible));
  if(visible) $("reward-text").textContent=reward.text;
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

function applyState(state,initial){
  appState=state;
  const authenticated=Boolean(state.authenticated);
  $("login-gate").classList.toggle("visible",!authenticated);
  $("app-shell").classList.toggle("auth-hidden",!authenticated);
  $("sidebar-status").textContent=state.status_text.replace(/^●\s*/,"");
  $("sidebar-status").style.color=state.status_color;
  document.querySelector(".status-dot").style.background=state.status_color;
  $("auth-status").textContent=state.auth_status;
  $("auth-status").style.color=authenticated?"#42d6a4":state.auth_status.includes("НЕ УДАЛАСЬ")?"#ff7b89":"#8792a8";
  $("auth-code").textContent=state.auth_code?("Код авторизации: "+state.auth_code):"Код авторизации: —";
  $("login-button").textContent=authenticated?"Подключено":state.auth_status.includes("ПОДКЛЮЧЕНИЕ")||state.auth_status.includes("ПРОВЕРКА")?"Подключение…":"Войти через Twitch";
  $("login-button").disabled=state.auth_status.includes("ПОДКЛЮЧЕНИЕ")||state.auth_status.includes("ПРОВЕРКА");
  $("gate-login-button").disabled=state.auth_status.includes("ПОДКЛЮЧЕНИЕ")||state.auth_status.includes("ПРОВЕРКА")||state.auth_status.includes("ОЖИДАЕТСЯ ПОДТВЕРЖДЕНИЕ");
  if(document.activeElement!==$("gate-username")) $("gate-username").value=state.username||"";
  if(document.activeElement!==$("username")) $("username").value=state.username||"";
  if(!authenticated){
    const waiting=state.auth_status.includes("ОЖИДАЕТСЯ ПОДТВЕРЖДЕНИЕ");
    $("gate-status").textContent=state.auth_code&&!state.auth_code.includes("Сессия")?"Код Twitch: "+state.auth_code+". Подтверди вход в открывшемся браузере.":waiting?"Ожидается подтверждение входа в браузере Twitch…":state.auth_status.includes("ПОДКЛЮЧЕНИЕ")?"Выполняется подключение к Twitch…":state.auth_status.includes("ПРОВЕРКА")?"Проверяю сохранённую сессию Twitch…":state.auth_status.includes("НЕ УДАЛАСЬ")?"Не удалось подключиться. Проверь браузер и попробуй ещё раз.":"Введи логин и подключи аккаунт, чтобы открыть приложение.";
  }
  $("start-button").disabled=state.session_running;
  $("stop-button").disabled=!state.session_running;
  $("start-button").textContent=state.session_running?"▶　Работает":"▶　Запустить";
  $("points-value").textContent=Number(state.gained_total||0).toLocaleString("ru-RU");
  $("online-value").textContent=String((state.online_channels||[]).length);
  $("events-value").textContent=String(state.event_total||0);
  renderChannels(state.channels||[],state.online_channels||[],state.session_running,state.channel_profiles||{});
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
  if(polling||!window.pywebview||!window.pywebview.api) return;
  polling=true;
  try{
    const state=await api().get_state(lastLogId);
    applyState(state,false);
  }catch(error){}
  polling=false;
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
    username:$("username").value.trim().toLowerCase(),
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
    const payload=collectPayload();
    payload.username=username;
    await api().save_settings(payload);
    const result=await api().login(username);
    $("gate-status").textContent=result.message||"Ожидается авторизация Twitch.";
    await poll();
  }catch(error){
    $("gate-status").textContent=error.message||"Ошибка авторизации.";
    $("gate-login-button").disabled=false;
  }
}

async function login(){
  return loginUser($("username").value);
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
  if(save&&window.pywebview&&window.pywebview.api) api().finish_tutorial();
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
  const state=await api().get_state(0);
  lastLogId=0;
  applyState(state,true);
  setPage("dashboard");
  setInterval(poll,900);
}

document.addEventListener("DOMContentLoaded",()=>{
  $("nav-dashboard").addEventListener("click",()=>setPage("dashboard"));
  $("nav-logs").addEventListener("click",()=>setPage("logs"));
  $("open-folder").addEventListener("click",openFolder);
  $("add-channel").addEventListener("click",()=>openChannelEditor("add",""));
  $("channel-list").addEventListener("click",event=>{
    const button=event.target.closest("button[data-action]");
    if(!button) return;
    const channel=button.dataset.channel;
    if(button.dataset.action==="edit") openChannelEditor("edit",channel);
    if(button.dataset.action==="remove") removeChannel(channel);
  });
  $("login-button").addEventListener("click",login);
  $("gate-login-button").addEventListener("click",gateLogin);
  $("gate-username").addEventListener("keydown",event=>{if(event.key==="Enter")gateLogin();});
  $("tutorial-next").addEventListener("click",nextTutorial);
  $("tutorial-skip").addEventListener("click",()=>hideTutorial(true));
  $("start-button").addEventListener("click",startFarm);
  $("stop-button").addEventListener("click",stopFarm);
  $("save-button").addEventListener("click",saveSettings);
  $("logs-shortcut").addEventListener("click",()=>setPage("logs"));
  $("back-from-logs").addEventListener("click",()=>setPage("dashboard"));
  $("back-from-channel").addEventListener("click",()=>setPage("dashboard"));
  $("cancel-channel").addEventListener("click",()=>setPage("dashboard"));
  $("save-channel").addEventListener("click",saveChannel);
  $("channel-input").addEventListener("keydown",event=>{if(event.key==="Enter")saveChannel();});
  $("open-bets").addEventListener("click",openBetSettings);
  $("back-from-bets").addEventListener("click",()=>setPage("dashboard"));
  $("preset-select").addEventListener("change",()=>fillPreset($("preset-select").value,appState&&appState.bet_settings,appState&&appState.custom_presets));
  $("load-preset").addEventListener("click",()=>fillPreset($("preset-select").value,appState&&appState.bet_settings,appState&&appState.custom_presets));
  $("apply-bets").addEventListener("click",applyBets);
  $("save-preset").addEventListener("click",savePreset);
  $("delete-preset").addEventListener("click",deletePreset);
  $("copy-logs").addEventListener("click",copyLogs);
  initialize().catch(error=>notify(error.message||"Не удалось запустить интерфейс."));
});