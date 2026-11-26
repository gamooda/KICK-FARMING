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
if (!backend.includes("webview.start(\n                func=start_backend")) {
  throw new Error("Backend initialization is not attached to webview.start(func=...).");
}
const startBackendStart = backend.indexOf("        def start_backend():");
const startBackendEnd = backend.indexOf("\n        api.webview_start_requested_at", startBackendStart);
const startBackend = startBackendStart >= 0 && startBackendEnd >= 0
  ? backend.slice(startBackendStart, startBackendEnd)
  : "";
if (!startBackend.includes("threading.Thread(") || !startBackend.includes("daemon=True")) {
  throw new Error("The webview.start callback does not launch daemon background threads.");
}
const onLoadedStart = backend.indexOf("    def on_loaded(");
const onLoadedEnd = backend.indexOf("\n    def on_closing", onLoadedStart);
const onLoaded = onLoadedStart >= 0 && onLoadedEnd >= 0
  ? backend.slice(onLoadedStart, onLoadedEnd)
  : "";
if (!onLoaded || onLoaded.includes("start_startup_worker")) {
  throw new Error("The WebView2 loaded event still starts backend initialization on the GUI event thread.");
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

const inlineBootstrap = [...html.matchAll(/<script>([\\s\\S]*?)<\\/script>/gi)][0]?.[1];
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
  "get_startup_state",
  "record_frontend_timing",
  "record_startup_debug",
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
  "open_url",
  "minimize_window",
  "toggle_maximize_window",
  "close_window"
];

for (const name of required) {
  bridge[name] = () => Promise.resolve({ ok: true });
}

bridge.get_startup_state = () => Promise.resolve({ ready: true, phase: "Готово", elapsed: 0, error: "", diagnostics: [] });
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
  setInterval() { intervalCount++; return intervalCount; },
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
  if (intervalCount !== 3) {
    throw new Error("Startup timers and polling were not initialized after readiness.");
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
