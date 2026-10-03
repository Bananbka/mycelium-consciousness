# RACI Matrix

Команда: 2 особи — **Давид** (прикладний код: домен, API, автентифікація,
тести) та **Нікіта** (конвеєр обробки пам'яті, контейнеризація, інфраструктура,
навантажувальне тестування, документація).

R — Responsible (виконує роботу), A — Accountable (відповідає за
результат/приймає рішення), C — Consulted (консультує/погоджує), I —
Informed (інформується про результат).

| Модуль / Компонент | Давид | Нікіта |
| --- | --- | --- |
| Доменна модель та специфікація API (сутності, ендпоінти, статус-коди) | R, A | C |
| Схема БД та Alembic-міграції (`shared/db`, `shared/migrations`) | R, A | C |
| Автентифікація та контроль доступу (JWT, ролі, IDOR-захист, `shared/auth`, `api/deps.py`) | R, A | I |
| Профілі, адміністрування, тарифи, deactivate (`api/routers/profiles.py`, `api/routers/admin.py`) | R, A | I |
| Набір тестів (`tests/`: unit, integration, IDOR, access control) | R, A | C |
| Гарячий шлях запису (`POST /memories/write`, Redis Streams, MAXLEN-запобіжник, `shared/streams.py`) | C | R, A |
| Дренаж Redis → Postgres (`celery_worker/flush.py`, `memory_buffers`) | C | R, A |
| Rollup-конвеєр та інтеграція з MinIO (`celery_worker/pipeline.py`, `shared/object_storage.py`, `shared/backup_codec.py`) | C | R, A |
| Backups / restore / resurrect (`api/routers/memories.py`) | C | R, A |
| Stateless-рефакторинг, `X-Instance-ID`, `/health` (lab 2–3) | C | R, A |
| Розподілений кеш Cache-Aside (`shared/cache.py`, lab 4) | C | R, A |
| Контейнеризація (Dockerfile'и сервісів, `docker-compose.yaml`, мережі й volumes) | I | R, A |
| Reverse proxy та балансування навантаження (`infra/nginx/default.conf.template`) | I | R, A |
| CI/CD pipeline (`.github/workflows/ci.yaml`) | I | R, A |
| Навантажувальне тестування (`tools/`, `load-tests/`, `scripts/`) | C | R, A |
| Документація та архітектурні схеми (README, `docs/`, діаграми) | C | R, A |

## Короткий підсумок відповідальності

- **Давид** — власник прикладного коду: модель даних і міграції,
  автентифікація/авторизація, профілі та адміністративні ендпоінти, набір
  тестів (unit, інтеграційні, IDOR, контроль доступу).
- **Нікіта** — власник конвеєра обробки пам'яті клонів (запис → Redis-буфер →
  Postgres → бекап у MinIO), stateless-рефакторингу, кешування, а також усієї
  інфраструктури: Docker-образи та docker-compose стек, nginx як єдина точка
  входу, CI/CD, навантажувальне тестування та документація.
