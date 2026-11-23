# KICK Watch Companion

Локальный помощник для официального OAuth-подключения к Kick и учёта реального времени просмотра.

## Требования

- Python 3.10+
- Приложение Kick Developer с включённой 2FA
- Redirect URL: `http://localhost:8765/callback`

Создай приложение в [Kick Developer Settings](https://kick.com/settings/developer). Введи Client ID и Client Secret при запуске.

## Запуск

Windows: запусти `start.bat`. Другие системы: `python main.py`.

Вход в аккаунт и пароль вводятся только на официальной странице Kick. Приложение использует OAuth 2.1 с PKCE; Client Secret не сохраняется.

## Возможности

- OAuth-подключение и обновление сессии
- Получение профиля через Kick Public API
- Открытие выбранной трансляции в браузере
- Ручной учёт времени просмотра и расчёт ориентировочного количества Channel Points

## Ограничения

Баллы начисляются Kick автоматически только за реальный просмотр при авторизации: 10 баллов за 5 минут, 20 баллов для подписчика. Локальный таймер показывает оценку, а не фактический баланс. Публичный API не предоставляет зрительского метода отправки времени просмотра или чтения личного баланса Channel Points.

Приложение не подменяет воспроизведение, не отправляет фиктивные события просмотра и не накручивает просмотры.

Документация: [Kick API](https://docs.kick.com) · [Channel Points](https://help.kick.com/en/articles/10709188-guide-to-channel-points) · [Community Guidelines](https://kick.com/community-guidelines)
