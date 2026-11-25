<p align="center">
  <img src="assets/logo.svg" width="92" height="92" alt="Twitch Farming">
</p>

<h1 align="center">Twitch Farming</h1>

<p align="center">Настольное приложение для автоматизации сбора Channel Points, Drops и других доступных наград Twitch.</p>

<p align="center">
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.11-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.11"></a>
  <a href="https://pywebview.flowrl.com/"><img src="https://img.shields.io/badge/PyWebView-6.2.1-5B4B8A?style=flat-square" alt="PyWebView 6.2.1"></a>
  <img src="https://img.shields.io/badge/Windows-10%20%2F%2011-555555?style=flat-square&logo=windows&logoColor=white" alt="Windows 10 and 11">
  <a href="https://github.com/gamooda/KICK-FARMING/releases/latest"><img src="https://img.shields.io/badge/Release-latest-8B5CF6?style=flat-square" alt="Latest release"></a>
</p>

<p align="center">
  <a href="https://github.com/gamooda/KICK-FARMING/releases/latest">Скачать последнюю версию</a>
  ·
  <a href="#установка">Установка</a>
  ·
  <a href="#скриншоты">Скриншоты</a>
</p>

---

## Возможности

- Автоматический сбор Channel Points на выбранных каналах.
- Отображение баланса и пользовательских иконок валют каналов.
- Отслеживание прогресса и получение доступных Twitch Drops.
- Автоматические ставки на прогнозы с настраиваемой стратегией.
- Обработка рейдов, серий просмотров, моментов и IRC-чата.
- Список каналов с ручной сортировкой и сохранением приоритета.
- Компактный интерфейс на PyWebView.
- Сохранение настроек и защита Twitch-сессии средствами Windows DPAPI.

## Скриншоты

<table>
  <tr>
    <th align="center">Главный экран</th>
    <th align="center">Инвентарь Drops</th>
    <th align="center">Настройки прогнозов</th>
  </tr>
  <tr>
    <td align="center"><sub>Добавить <code>assets/screenshots/main.png</code></sub></td>
    <td align="center"><sub>Добавить <code>assets/screenshots/drops.png</code></sub></td>
    <td align="center"><sub>Добавить <code>assets/screenshots/predictions.png</code></sub></td>
  </tr>
</table>

## Установка

### Готовая версия

1. Откройте [последний релиз](https://github.com/gamooda/KICK-FARMING/releases/latest).
2. Скачайте `Twitch.Farming.exe`.
3. Запустите файл на Windows 10/11.
4. При первом подключении введите логин Twitch и подтвердите авторизацию одноразовым кодом на официальной странице Twitch.

Для работы приложения требуется [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/).

### Запуск из исходников

Требуются Git, Python 3.11 и WebView2 Runtime.

```powershell
git clone https://github.com/gamooda/KICK-FARMING.git
cd "KICK-FARMING/Twitch Farming"
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

Пароль Twitch приложение не запрашивает. Авторизация выполняется через официальный механизм Twitch.

## Структура проекта

- `Twitch Farming/` — основное Windows-приложение, интерфейс, Python-обвязка и движок Twitch.
- `assets/logo.svg` — анимированный SVG-логотип для README.
- `Kick Farming/` — отдельная консольная утилита KICK Watch Companion; она не входит в Twitch Farming EXE. Инструкция находится в [Kick Farming/README.md](Kick%20Farming/README.md).

## Сборка

Windows EXE собирается через GitHub Actions при изменениях в `Twitch Farming/`. Готовые сборки публикуются в [Releases](https://github.com/gamooda/KICK-FARMING/releases).

## Лицензия

Движок Twitch основан на [Twitch-Channel-Points-Miner-v2](https://github.com/rdavydov/Twitch-Channel-Points-Miner-v2). Условия GPL-3.0 и сведения об изменениях находятся в [Twitch Farming/LICENSE](Twitch%20Farming/LICENSE) и [Twitch Farming/UPSTREAM.md](Twitch%20Farming/UPSTREAM.md).
