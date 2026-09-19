# RACI Matrix

Команда: 2 особи — **Нікіта** (домен, бекенд-логіка) та **Давид**
(інфраструктура, тестування, супровідна документація).

R — Responsible (виконує роботу), A — Accountable (відповідає за
результат/приймає рішення), C — Consulted (консультує/погоджує), I —
Informed (інформується про результат).

| Модуль / Компонент | Нікіта | Давид |
| --- | --- | --- |
| Доменна модель та специфікація API (сутності, ендпоінти, статус-коди) | R, A | C |
| Схема БД та Alembic-міграції (`shared/db`, `shared/migrations`) | R, A | I |
| Автентифікація та контроль доступу (JWT, ролі, IDOR-захист, `shared/auth`, `api/deps.py`) | R, A | I |
| Гарячий шлях запису (`POST /memories/write`, Redis Streams, MAXLEN-запобіжник) | R, A | C |
| Дренаж Redis → Postgres (`celery_worker/flush.py`, `memory_buffers`) | R, A | C |
| Rollup-конвеєр та інтеграція з MinIO (`celery_worker/pipeline.py`, `shared/object_storage.py`, `shared/backup_codec.py`) | R, A | C |
| Адміністрування, тарифи, force-rollup, resurrect (`api/routers/admin.py`, `api/routers/memories.py`) | R, A | I |
| Контейнеризація (Dockerfile'и сервісів, `docker-compose.yaml`, мережі й volumes) | C | R, A |
| Reverse proxy та балансування навантаження (`infra/nginx/default.conf.template`) | I | R, A |
| CI/CD pipeline (`.github/workflows/ci.yaml`) | I | R, A |
| Набір тестів (`tests/`: unit, integration, IDOR, access control) | C | R, A |
| Інструмент навантажувального тестування (`tools/load_generator.py`) | I | R, A |
| Документація та архітектурні схеми (README, `docs/`, діаграма архітектури) | A | R |

## Короткий підсумок відповідальності

- **Нікіта** — власник доменної логіки та бекенд-сервісів: модель даних,
  автентифікація/авторизація, увесь конвеєр обробки пам'яті клонів (запис →
  Redis-буфер → Postgres → бекап у MinIO), адміністративні операції.
- **Давид** — власник інфраструктурного контуру: Docker-образи та
  docker-compose стек, конфігурація nginx як єдиної точки входу, CI/CD,
  тестове покриття та інструмент навантажувального тестування, а також
  оформлення документації за результатами роботи обох.
