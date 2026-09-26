# GigaChat API — краткая справка

Выжимка из [документации GigaChat](https://developers.sber.ru/docs/ru/gigachat/api/main) в объёме, нужном проекту. Сверено 2026-09-26. Первоисточник — официальная документация.

## Авторизация

1. **Ключ авторизации (Authorization Key)** выдаётся в личном кабинете developers.sber.ru (проект GigaChat API → «Настройки API» → «Получить ключ»). Это Base64 от строки `Client ID:Client Secret`, показывается один раз. Получить его могут только роли «Владелец» и «Администратор».
2. **Токен доступа.** `POST https://ngw.devices.sberbank.ru:9443/api/v2/oauth`
   - заголовки: `Authorization: Basic <ключ авторизации>`, `RqUID: <uuid4>`, `Content-Type: application/x-www-form-urlencoded`;
   - тело: `scope=GIGACHAT_API_PERS` (физлица), `GIGACHAT_API_B2B` (юрлица, пакеты) или `GIGACHAT_API_CORP` (юрлица, оплата по факту);
   - токен живёт 30 минут, лимит — до 10 запросов в секунду.
3. Все остальные запросы — с заголовком `Authorization: Bearer <токен>`.

Ключ авторизации напрямую как Bearer не используется. Ответ `400 Can't decode 'Authorization' header` означает, что в `GIGACHAT_AUTH_KEY` лежит не Base64-ключ (например, Client ID или Client Secret по отдельности).

TLS: нужен корневой сертификат НУЦ Минцифры (`GIGACHAT_CA_BUNDLE`, см. README).

## Адреса

| Назначение | Метод и путь |
|---|---|
| Базовый адрес | `https://api.giga.chat/v1` (прежний `https://gigachat.devices.sberbank.ru/api/v1` пока работает) |
| Генерация | `POST /chat/completions` |
| Загрузка файла | `POST /files` (multipart: `file`, `purpose=general`) |
| Файл / список / удаление | `GET /files/{id}`, `GET /files`, `POST /files/{id}/delete` |

## Модели генерации

| Модель | Идентификатор | Назначение |
|---|---|---|
| Lite | `GigaChat-2` (алиас `GigaChat`) | Простые задачи, быстрее и дешевле |
| Pro | `GigaChat-2-Pro` (алиас `GigaChat-Pro`) | Лучше следует инструкциям, сложные задачи |
| Max | `GigaChat-2-Max` (алиас `GigaChat-Max`) | Сложные задачи, упор на качество и креативность |
| Ultra | `GigaChat-3-Ultra` | Новая модель; пока только для физлиц во freemium |

## Запрос генерации

Основные поля тела: `model`, `messages` (`role`: `system` / `user` / `assistant`, `content`), `temperature`, `top_p`, `max_tokens`, `repetition_penalty`, `stream`, `functions` / `function_call`, `profanity_check`.

`finish_reason` в ответе: `stop`, `length`, `function_call`, `blacklist` — ответ заблокирован фильтрами (обрабатывается в `intonaciya.llm.refusals`).

## Изображения

- Сначала файл загружается через `POST /files`, в ответе — идентификатор.
- В сообщении указывается `"attachments": ["<id файла>"]`.
- Одно изображение на сообщение, до 10 изображений на запрос, до 15 МБ на изображение, до 80 МБ на запрос.
- Форматы: JPEG, PNG, TIFF, BMP.
- На обработку одного изображения уходит до 1792 токенов.

## Ограничения freemium для физлиц

- Генерация выполняется в один поток: параллельные запросы не ускоряют работу, скрипты по умолчанию шлют запросы последовательно.
- Бесплатные токены выдаются на 12 месяцев отдельно на каждую модель; актуальные объёмы и цены — на странице [тарифов для физлиц](https://developers.sber.ru/docs/ru/gigachat/tariffs/individual-tariffs).
