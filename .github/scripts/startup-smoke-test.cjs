const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.resolve(__dirname, "../../Twitch Farming/ui/app.js"), "utf8");
const html = fs.readFileSync(path.resolve(__dirname, "../../Twitch Farming/ui/index.html"), "utf8");
if (!html.includes("window.onerror") || !html.includes("unhandledrejection") || !html.includes("startup-debug-output")) {
  throw new Error("Startup error capture or debug console is missing from index.html.");
}
if (source.includes("WebView2 не запустился за 120 секунд")) {
  throw new Error("The obsolete 120-second startup placeholder is still present.");
}
const elements = new Map();
let intervalCount = 0;

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

bridge.get_startup_state = () => Promise.resolve({ ready: true, phase: "Готово", elapsed: 0, error: "" });
bridge.get_auth_state = () => Promise.resolve({
  authenticated: false,
  username: "",
  auth_status: "АККАУНТ НЕ ПОДКЛЮЧЁН",
  auth_code: "",
  auth_error: "",
  worker_alive: false,
  worker_state: "idle",
  restore_pending: false,
  elapsed: 0
});
bridge.get_state = () => Promise.resolve(state);

const window = {
  setTimeout() { return 0; },
  clearTimeout() {},
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
  setTimeout() { return 0; },
  clearTimeout() {},
  setInterval() { return 0; },
  clearInterval() {},
  process
};

const smoke = `
setupBetControls = () => {};
cacheChannelProfiles = () => {};
applyAuthState = () => {};
renderChannels = () => {};
renderRewards = () => {};
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
  const button = document.getElementById("farm-toggle");
  if (!button.innerHTML.includes("Запустить")) {
    throw new Error("Farm button did not initialize.");
  }
  console.log("JavaScript startup smoke test passed.");
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
`;

vm.runInNewContext(source + "\n" + smoke, sandbox, { filename: "app.js" });
