const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.resolve(__dirname, "../../Twitch Farming/ui/app.js"), "utf8");
const html = fs.readFileSync(path.resolve(__dirname, "../../Twitch Farming/ui/index.html"), "utf8");
const css = fs.readFileSync(path.resolve(__dirname, "../../Twitch Farming/ui/style.css"), "utf8");
const backend = fs.readFileSync(path.resolve(__dirname, "../../Twitch Farming/webview_app.py"), "utf8");
if (!css.includes(".sidebar{width:210px;min-width:210px;max-width:210px;flex:0 0 210px;flex-shrink:0}")) {
  throw new Error("The sidebar fixed width and flex-shrink protection are missing.");
}
if (!css.includes(".dashboard-toolbar{height:auto;min-height:49px;flex-wrap:wrap;align-items:center;gap:7px 10px}")) {
  throw new Error("The dashboard toolbar wrapping rule is missing.");
}
if (!source.includes("const currencyIcon=[currency.icon_url,currency.points_icon_url,item.currency_icon,item.points_icon_url]")) {
  throw new Error("Recent reward rendering does not prioritize the live channel currency icon.");
}
if (!html.includes("window.onerror") || !html.includes("unhandledrejection") || !html.includes("startup-debug-output")) {
  throw new Error("Startup error capture or debug console is missing from index.html.");
}
if (source.includes("WebView2 не запустился за 120 секунд")) {
  throw new Error("The obsolete 120-second startup placeholder is still present.");
}
const apiClassIndex = backend.indexOf("class TwitchFarmingApi:");
const apiConstructorStart = backend.indexOf("    def __init__(self):", apiClassIndex);
const apiConstructorEnd = backend.indexOf("\n    def ", apiConstructorStart + 10);
const apiConstructor = apiConstructorStart >= 0 && apiConstructorEnd >= 0
  ? backend.slice(apiConstructorStart, apiConstructorEnd)
  : "";
if (!apiConstructor) throw new Error("TwitchFarmingApi constructor was not found.");
for (const forbidden of ["threading.Thread", ".start(", "_load_config", "_restore_session", "TwitchChannelPointsMiner", "requests."]) {
  if (apiConstructor.includes(forbidden)) {
    throw new Error("TwitchFarmingApi constructor contains startup work: " + forbidden);
  }
}
const appLogHandlerIndex = backend.indexOf("class AppLogHandler(");
if (appLogHandlerIndex < 0) throw new Error("AppLogHandler class declaration was not found.");
const moduleHeader = backend.slice(0, appLogHandlerIndex);
for (const forbidden of ["TwitchChannelPointsMiner", "requests", "PIL", "pystray"]) {
  if (moduleHeader.includes(forbidden)) {
    throw new Error("A heavy optional library is imported before the backend worker: " + forbidden);
  }
}
if (!/webview\.start\(\s*func\s*=\s*on_loaded\s*,/.test(backend)) {
  throw new Error("The backend callback is not attached to webview.start(func=on_loaded).");
}
if (backend.includes("func=start_backend")) {
  throw new Error("The previous startup callback is still active.");
}
const initBackendStart = backend.indexOf("        def init_backend():");
const initBackendEnd = backend.indexOf("\n\n        def on_loaded", initBackendStart);
const initBackend = initBackendStart >= 0 && initBackendEnd >= 0
  ? backend.slice(initBackendStart, initBackendEnd)
  : "";
if (!initBackend.includes("api._startup_tasks()") ||
    !initBackend.includes("target=api.watch_webview_startup") ||
    !initBackend.includes("daemon=True")) {
  throw new Error("The background init_backend worker is incomplete.");
}
const onLoadedStart = backend.indexOf("        def on_loaded():");
const onLoadedEnd = backend.indexOf("\n\n        api.webview_start_requested_at", onLoadedStart);
const onLoaded = onLoadedStart >= 0 && onLoadedEnd >= 0
  ? backend.slice(onLoadedStart, onLoadedEnd)
  : "";
const onLoadedBody = onLoaded.split("\n").slice(1).map(line => line.trim()).filter(Boolean);
if (onLoadedBody.length !== 1 || onLoadedBody[0] !== "threading.Thread(target=init_backend, daemon=True).start()") {
  throw new Error("on_loaded must contain exactly one daemon-thread start statement.");
}
const batchMethodStart = backend.indexOf("    def record_startup_debug_batch(self, items):");
const batchMethodEnd = backend.indexOf("\n    def mark_frontend_ready", batchMethodStart);
const batchMethod = batchMethodStart >= 0 && batchMethodEnd >= 0
  ? backend.slice(batchMethodStart, batchMethodEnd)
  : "";
if (!batchMethod || batchMethod.includes("_flush_logs") || batchMethod.includes("evaluate_js")) {
  throw new Error("Startup debug batches must not synchronously flush files or call evaluate_js.");
}
if (!html.includes("bridge.record_startup_debug_batch(batch)") ||
    !html.includes("setTimeout(flush,delay)") ||
    !html.includes("delay=280") ||
    html.includes("bridge.record_startup_debug(item.level,item.line)")) {
  throw new Error("Startup diagnostics are not buffered into 280ms batches.");
}
if (!source.includes('"record_startup_debug_batch"') || !source.includes("await sleep(280);")) {
  throw new Error("Frontend startup polling is not using the batched diagnostic API and reduced cadence.");
}
if (!backend.includes("def get_startup_status(") ||
    backend.slice(backend.indexOf("    def get_startup_status("), backend.indexOf("\n    def get_startup_state", backend.indexOf("    def get_startup_status("))).includes("with self.lock")) {
  throw new Error("The lightweight non-blocking startup status API is missing or locks the UI.");
}
if (backend.includes("evaluate_js")) {
  throw new Error("Python still calls evaluate_js during startup or window events.");
}
if (!source.includes("window.setInterval(()=>") ||
    !source.includes("},300);") ||
    !source.includes("get_startup_status(lastPythonStartupEventId)")) {
  throw new Error("The startup overlay is not polling Python status every 300ms with setInterval.");
}
if (!source.includes("function getDropCategory(") ||
    !source.includes("data-drops-category") ||
    !source.includes("data-drop-action='browse'")) {
  throw new Error("Drops category filtering or Twitch catalog actions are missing.");
}
if (html.includes("Drops для подписки") || html.includes("drops-sub-list") || !html.includes("drops-campaign-list")) {
  throw new Error("Subscription Drops panel was not fully removed.");
}
const twitchParser = fs.readFileSync(path.resolve(__dirname, "../../Twitch Farming/engine/TwitchChannelPointsMiner/classes/Twitch.py"), "utf8");
if (!twitchParser.includes("rewardCampaignsAvailableToUser") ||
    !twitchParser.includes('"category": "twitch"') ||
    !twitchParser.includes('"category": "game"') ||
    !twitchParser.includes("collection_items")) {
  throw new Error("Full campaign collection parsing or reward category classification is missing.");
}
if (!html.includes("startup-window-bar pywebview-drag-region") ||
    !html.includes("startup-window-minimize") ||
    !html.includes("startup-window-close") ||
    (html.match(/startup-window-control[^>]*pywebview-drag-region/g) || []).length < 2) {
  throw new Error("The startup overlay is missing draggable window controls.");
}
if (!css.includes(".startup-debug-panel,.startup-debug-panel *{-webkit-user-select:text!important;user-select:text!important") ||
    !css.includes("overflow-y:auto") ||
    !css.includes("touch-action:pan-y")) {
  throw new Error("The startup diagnostics panel is not selectable and scrollable.");
}
if (backend.includes("WEBVIEW_DIR.mkdir") || backend.includes("storage_path=str(WEBVIEW_DIR)")) {
  throw new Error("The WebView2 profile is still prepared synchronously during application startup.");
}

const workflow = fs.readFileSync(path.resolve(__dirname, "../workflows/build-twitch-exe.yml"), "utf8");
const versionInfo = fs.readFileSync(path.resolve(__dirname, "../../Twitch Farming/version_info.py"), "utf8");
const buildStepStart = workflow.indexOf("- name: Build executable");
const buildStepEnd = workflow.indexOf("\n      - name:", buildStepStart + 10);
const buildStep = buildStepStart >= 0 && buildStepEnd >= 0
  ? workflow.slice(buildStepStart, buildStepEnd)
  : "";
if (!buildStep.includes('--paths "Twitch Farming"') ||
    !buildStep.includes('"Twitch Farming/webview_app.py"') ||
    buildStep.includes('"Twitch Farming/app.py"') ||
    !backend.includes('if __name__ == "__main__":\n    main()')) {
  throw new Error("PyInstaller must package webview_app.py directly and include the application root in its module search path.");
}
if (!workflow.includes("--onedir") || workflow.includes("--onefile") || !workflow.includes("Twitch.Farming.zip")) {
  throw new Error("The Windows build must use onedir and publish the updater ZIP package.");
}
if (!workflow.includes("GITHUB_RUN_NUMBER") || !versionInfo.includes("APP_VERSION")) {
  throw new Error("The application version is not generated from the GitHub Actions build number.");
}
const mainFunctionIndex = backend.indexOf("def main():");
const backendModuleHeader = mainFunctionIndex >= 0 ? backend.slice(0, mainFunctionIndex) : backend;
if (/^\s*import webview\s*$/m.test(backendModuleHeader) ||
    !backend.includes("        import webview") ||
    backend.includes("_start_native_splash") ||
    backend.includes("NativeStartupSplash") ||
    backend.includes("import tkinter")) {
  throw new Error("Only the main WebView2 window may be created; the duplicate native splash must be absent.");
}
if (!backend.includes("def get_update_state(") ||
    !backend.includes("def check_for_updates(") ||
    !backend.includes("def download_update(") ||
    !backend.includes("Twitch.Farming.zip")) {
  throw new Error("The GitHub updater API or ZIP installer is incomplete.");
}
if (!backend.includes("faulthandler.enable(file=_CRASH_LOG_HANDLE, all_threads=True)") ||
    !backend.includes("sys.unraisablehook = _startup_unraisablehook") ||
    !backend.includes('CRASH_LOG_FILE = APP_DIR / "crash.log"') ||
    !backend.includes("def enqueue_ui_state(") ||
    !backend.includes("self._apply_ui_state_queue_locked()") ||
    !backend.includes('"ui_events": ui_events')) {
  throw new Error("Crash logging or the polling-only farm state queue is missing.");
}
const engineWorkerStart = backend.indexOf("class EngineWorker(");
const engineWorkerEnd = backend.indexOf("\n\nclass ChannelProfileWorker", engineWorkerStart);
const engineWorker = engineWorkerStart >= 0 && engineWorkerEnd >= 0
  ? backend.slice(engineWorkerStart, engineWorkerEnd)
  : "";
if (!engineWorker.includes("enqueue_ui_state") ||
    engineWorker.includes("window.") ||
    engineWorker.includes("evaluate_js") ||
    engineWorker.includes("self.app.engine_error") ||
    engineWorker.includes("self.app.engine_finished")) {
  throw new Error("The miner worker still calls UI APIs directly instead of queueing state for polling.");
}
const minerSource = fs.readFileSync(path.resolve(__dirname, "../../Twitch Farming/engine/TwitchChannelPointsMiner/TwitchChannelPointsMiner.py"), "utf8");
const twitchSource = fs.readFileSync(path.resolve(__dirname, "../../Twitch Farming/engine/TwitchChannelPointsMiner/classes/Twitch.py"), "utf8");
if (!minerSource.includes("self.twitch.streamers_lock = self.streamers_lock") ||
    !twitchSource.includes("def get_active_watch_channels(") ||
    !twitchSource.includes("def _streamer_snapshot(") ||
    minerSource.includes("sys.exit(0)") ||
    minerSource.includes("signal.SIGSEGV")) {
  throw new Error("Streamer collection locking or safe miner shutdown is missing.");
}
if (!html.includes("active-farm-panel") || !html.includes("update-check-button") ||
    !html.includes("update-download-button") || !source.includes("function renderActiveFarmPanel(")) {
  throw new Error("The active farming panel or application update card is missing.");
}
for (const filter of ["all_campaigns", "started", "twitch_view"]) {
  if (!html.includes('data-drops-category="' + filter + '"')) {
    throw new Error("Drops inventory filter is missing: " + filter);
  }
}
if (!source.includes("drop.is_time_based") || !source.includes("item=>Boolean(item.started||item.in_inventory)") ||
    !source.includes("function isFreeWatchDrop(")) {
  throw new Error("Drops campaign filtering does not separate started inventory and time-based Twitch rewards.");
}
if (!source.includes("channel-info--always-visible") ||
    !css.includes(".channel-info--always-visible .channel-name{display:block!important") ||
    !css.includes(".channel-card-top .channel-watch-slot{position:absolute")) {
  throw new Error("The active watch badge still interferes with streamer names and logins.");
}
if (html.indexOf("settings-account-card") > html.indexOf("settings-update-card")) {
  throw new Error("The updater card must be placed beside the Twitch account card at the bottom of settings.");
}
if (!source.includes("ВРЕМЕННЫЙ • DROPS") ||
    !backend.includes("self.temporary_channels") ||
    !backend.includes("permanent_channels = [login for login in self.channels if login not in self.temporary_channels]") ||
    !backend.includes("Временные Drops-каналы удалены после завершения фарма")) {
  throw new Error("Drops channels are not marked and cleaned as temporary channels.");
}
if (!source.includes("Остановите фарм, чтобы изменить список каналов") ||
    !backend.includes("Остановите фарм, чтобы изменить список каналов.")) {
  throw new Error("Live-farming channel additions are not blocked in both UI and API.");
}
if (!source.includes(`"<div class='channel-name'>`) ||
    !source.includes(`"<div class='channel-login'>`) ||
    !source.includes("watchSlotMarkup")) {
  throw new Error("Streamer nickname, login or watch-slot badge markup is missing.");
}

const inlineBootstrap = [...html.matchAll(/<script>([\s\S]*?)<\/script>/gi)][0]?.[1];
if (!inlineBootstrap) throw new Error("Inline startup diagnostic bootstrap was not found.");
new vm.Script(inlineBootstrap, { filename: "index.html:inline-startup" });
const elements = new Map();
let intervalCount = 0;
let authCalls = 0;
let stateCalls = 0;
let restoreStarted = false;

function getElement(id) {
  if (!elements.has(id)) {
    elements.set(id, {
      id,
      textContent: "",
      innerHTML: "",
      value: "",
      title: "",
      disabled: false,
      setAttribute() {},
      dataset: {},
      style: {},
      classList: (() => {
        const values = new Set();
        return {
          add(name) { values.add(name); },
          remove(name) { values.delete(name); },
          toggle(name, force) {
            const enabled = force === undefined ? !values.has(name) : Boolean(force);
            if (enabled) values.add(name);
            else values.delete(name);
            return enabled;
          },
          contains(name) { return values.has(name); }
        };
      })()
    });
  }
  return elements.get(id);
}

const document = {
  activeElement: null,
  getElementById: getElement,
  addEventListener() {},
  querySelectorAll() { return []; },
  querySelector() { return null; }
};

const state = {
  authenticated: false,
  username: "",
  auth_status: "АККАУНТ НЕ ПОДКЛЮЧЁН",
  auth_code: "",
  auth_error: "",
  worker_alive: false,
  worker_state: "idle",
  restore_pending: false,
  elapsed: 0,
  session_running: false,
  session_started_at: 0,
  status_text: "●  НЕ ЗАПУЩЕН",
  gained_total: 0,
  event_total: 0,
  online_channels: [],
  channels: [],
  channel_profiles: {},
  watch_status: {},
  channel_balances: {},
  channel_currency: {},
  options: {},
  app_settings: {},
  bet_settings: {},
  custom_presets: {},
  selected_preset: "Оптимальный",
  tutorial_done: true,
  rewards: [],
  events: [],
  logs: [],
  drops_active_campaigns: 0
};

const bridge = {};
const required = [
  "get_auth_state",
  "get_state",
  "get_startup_status",
  "record_frontend_timing",
  "record_startup_debug_batch",
  "mark_frontend_ready",
  "restart_app",
  "begin_session_restore",
  "login",
  "reorder_channels",
  "get_followed_channels_state",
  "refresh_followed_channels",
  "add_followed_channel",
  "get_drops_inventory_state",
  "refresh_drops_inventory",
  "add_drop_channel_to_farm",
  "clear_avatar_cache",
  "clear_currency_cache",
  "logout",
  "clear_logs",
  "get_app_version",
  "get_update_state",
  "check_for_updates",
  "download_update",
  "open_url",
  "minimize_window",
  "toggle_maximize_window",
  "close_window"
];

for (const name of required) {
  bridge[name] = () => Promise.resolve({ ok: true });
}

bridge.get_startup_status = () => Promise.resolve({ ready: true, phase: "Готово", elapsed: 0, error: "", diagnostics: [] });
bridge.get_auth_state = () => {
  authCalls++;
  if (authCalls === 1) {
    return Promise.resolve({
      authenticated: false,
      username: "example",
      auth_status: "ПРОВЕРКА СОХРАНЁННОЙ СЕССИИ",
      auth_code: "",
      auth_error: "",
      worker_alive: false,
      worker_state: "idle",
      restore_pending: true,
      elapsed: 0
    });
  }
  if (restoreStarted && authCalls === 2) {
    return Promise.resolve({
      authenticated: false,
      username: "example",
      auth_status: "ПРОВЕРКА СОХРАНЁННОЙ СЕССИИ",
      auth_code: "",
      auth_error: "",
      worker_alive: true,
      worker_state: "restoring",
      restore_pending: false,
      elapsed: 1
    });
  }
  return Promise.resolve({
    authenticated: false,
    username: "example",
    auth_status: "АВТОРИЗАЦИЯ НЕ УДАЛАСЬ",
    auth_code: "",
    auth_error: "Тестовая сессия отклонена",
    worker_alive: false,
    worker_state: "failed",
    restore_pending: false,
    elapsed: 0
  });
};
bridge.begin_session_restore = () => {
  restoreStarted = true;
  return Promise.resolve({ ok: true });
};
bridge.get_state = () => {
  stateCalls++;
  return Promise.resolve(state);
};

const window = {
  setTimeout(callback) { return setImmediate(callback); },
  clearTimeout(handle) { clearImmediate(handle); },
  setInterval(callback, delay) {
    intervalCount++;
    if (delay === 300) setImmediate(callback);
    return intervalCount;
  },
  clearInterval() {},
  __tfStartupDebug: {
    log() {},
    display() {},
    flush() {}
  },
  pywebview: { api: bridge }
};

const sandbox = {
  document,
  window,
  console,
  performance: {
    now() { return 0; },
    getEntriesByType() {
      return [{ domContentLoadedEventEnd: 0, loadEventEnd: 0 }];
    }
  },
  setTimeout(callback) { return setImmediate(callback); },
  clearTimeout(handle) { clearImmediate(handle); },
  setInterval() { return 0; },
  clearInterval() {},
  process
};

const smoke = `
setupBetControls = () => {};
cacheChannelProfiles = () => {};
applyAuthState = () => {};
renderChannels = () => {};
renderEvents = () => {};
appendLogRows = () => {};
updateReward = () => {};
appInitialized = true;
(async () => {
  await initialize();
  if (!startupCompleteUi || !initialStateReady) {
    throw new Error("Startup flags were not set.");
  }
  if (!document.getElementById("startup-overlay").classList.contains("hidden")) {
    throw new Error("Startup overlay was not hidden after successful initialization.");
  }
  if (intervalCount !== 4) {
    throw new Error("Startup status timer and application polling were not initialized after readiness.");
  }
  if (!restoreStarted || authCalls < 3 || stateCalls !== 2) {
    throw new Error("Startup did not wait for the saved-session check to finish.");
  }
  const button = document.getElementById("farm-toggle");
  if (!button.innerHTML.includes("Запустить")) {
    throw new Error("Farm button did not initialize.");
  }
  renderRewards([
    {
      kind: "gem",
      channel_login: "bratishkinoff",
      title: "bratishkinoff",
      description: "+50 Channel Points",
      timestamp: Date.now() / 1000
    }
  ], {
    bratishkinoff: {
      name: "Тугрики",
      icon_url: "https://cdn.example/tuugrik.png"
    }
  });
  const rewardsHtml = document.getElementById("reward-list").innerHTML;
  if (!rewardsHtml.includes("<img class='reward-currency-icon'") || !rewardsHtml.includes("https://cdn.example/tuugrik.png")) {
    throw new Error("Recent rewards did not render the custom channel currency icon.");
  }
  console.log("JavaScript startup and reward icon smoke tests passed.");
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
`;

vm.runInNewContext(source + "\n" + smoke, sandbox, { filename: "app.js" });
