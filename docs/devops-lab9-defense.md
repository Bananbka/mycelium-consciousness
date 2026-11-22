# ЛР №9 (DevOps): інструкція до здачі

Тема: балансування навантаження і доступність застосунку (nginx + docker compose,
LocalStack як емулятор AWS). Гілка: `devops/lab9` (від `devops/lab8`). Код:
`aws/lb/`. Звіт: [devops-lab9-report.md](devops-lab9-report.md).

> Формат здачі в PDF не вказано (як у ЛР 3–8: гілка + звіт + показ наживо).
> **Уточніть у викладача** вимоги до звіту.

## 1. Що вимагає завдання → де це зроблено

| Вимога | Де показати |
| --- | --- |
| Контейнеризована версія застосунку з ЛР №8 | `aws/lb/Dockerfile` (копіює код ЛР №8 без змін), `aws/lb/server.py` — HTTP-обгортка навколо `lambda_handler` |
| ≥3 екземпляри, кожен повертає свій id | `aws/lb/docker-compose.yaml` (`app1`–`app3`, `INSTANCE_ID`); `server.py` рядок 56 — заголовок `X-Instance-ID`; `/health` теж повертає `instance` |
| Балансувальник | `aws/lb/nginx.conf` · `upstream mycelium` (рядки 10–15), `location /` (рядки 21–36) |
| Endpoint `/health` | `aws/lambda/handler.py` · `_health` (рядки 43–47) + `service.py` · `Service.health` (рядок 85): перевірка DynamoDB і S3, 503 при збої |
| Серія запитів і розподіл | `lbtool.py distribution` (рядок 74) |
| Вивести екземпляр і перевірити поведінку | `docker stop lb-app2` + `lbtool.py distribution`; `lbtool.py outage` (рядок 138) |
| Відновити екземпляр | `docker start lb-app2` — повернення в розподіл |
| Порівняння з балансуванням і без | `lbtool.py outage` проти `:8080` і `:8081`; `lbtool.py load` проти обох |
| nginx + docker compose, LocalStack — емулятор AWS | `docker-compose.yaml`: сервіси `localstack`, `provision`, `app1–3`, `nginx` |

## 2. Підготовка за годину до здачі

```powershell
cd "D:\studying\4.1\highload python\clone-factory-aws"
docker compose -f aws/docker-compose.yaml down                   # зупинити стек ЛР 8 (той самий контейнер localstack)
docker compose -f aws/lb/docker-compose.yaml up -d --build
docker compose -f aws/lb/docker-compose.yaml ps                  # app1-3 (healthy), nginx Up, provision Exited (0)
python aws/lb/scripts/lbtool.py distribution --count 30          # має бути ~по 10 на кожен app-N
```

Прожени заздалегідь повний сценарій (розділ 3): перша збірка образу й старт
LocalStack найповільніші.

## 3. Сценарій показу

1. **Архітектура** (1 хв): схема з `aws/lb/README.md`. «Три однакові контейнери з
   кодом ЛР №8, nginx — єдина точка входу, LocalStack — спільне сховище. Застосунок
   stateless: стан у DynamoDB і S3, тому будь-який екземпляр обробляє будь-який запит».
2. **Стек піднято** (30 с): `docker compose -f aws/lb/docker-compose.yaml ps`.
   Покажи healthchecks і що `provision` — одноразова задача (створила таблиці й бакет).
3. **Кожен екземпляр має id** (1 хв):
   ```powershell
   Invoke-WebRequest -UseBasicParsing http://localhost:8081/health | Select-Object -ExpandProperty Headers
   Invoke-RestMethod http://localhost:8080/health      # повторити 3–4 рази: instance змінюється
   ```
4. **Розподіл** (1 хв): `python aws/lb/scripts/lbtool.py distribution --count 60` →
   ~33 % на кожен. Покажи `docker logs lb-nginx --tail 10` (`upstream=…`). Поясни
   round robin.
5. **Вивести екземпляр** (2 хв):
   ```powershell
   docker stop lb-app2
   python aws/lb/scripts/lbtool.py distribution --count 60
   ```
   Очікування: усе між `app-1` і `app-3` (≈50/50), **0 помилок**; у `lb-nginx` видно
   `upstream=…, …` (перша спроба на мертвий — перейшла на живий). Поясни
   `max_fails=2 fail_timeout=10s` і `proxy_next_upstream`.
6. **Відновити** (1 хв):
   ```powershell
   docker start lb-app2
   Start-Sleep 12
   python aws/lb/scripts/lbtool.py distribution --count 60
   ```
   Екземпляр знову в розподілі (≈33 %). Якщо ні — `docker compose -f aws/lb/docker-compose.yaml restart nginx`
   (nginx резолвить імена upstream при старті; після перестворення контейнера IP може змінитись).
7. **З балансуванням і без** (3 хв) — головне порівняння:
   ```powershell
   python aws/lb/scripts/lbtool.py outage --url http://localhost:8080 --container lb-app2   # через nginx
   python aws/lb/scripts/lbtool.py outage --url http://localhost:8081 --container lb-app1   # напряму
   ```
   Під навантаженням контейнер зупиняється на 1/3 часу й повертається на 2/3.
   **Перевірено вживу:** через nginx — **0 помилок** із ~5000 запитів, у таймлайні
   `app-2` зникає на час зупинки й повертається; напряму в app1 — запити
   падають/зависають (помилки по 10 штук кожні 3 с = таймаут клієнта) весь простій.
   Нюанс: після `docker start` опублікований порт `:8081` у Docker Desktop починає
   відповідати лише через ~10 с, тому в прямому режимі «тиша» довша за сам простій;
   nginx ходить мережею Docker і цього не має. Покажи таймлайн по секундах.
8. **Продуктивність** (1 хв): `lbtool.py load` проти `:8081` і `:8080` (3 екземпляри).
   Виміряно вживу (20 воркерів, 12 с): один екземпляр ≈164–187 RPS (p99 160–740 мс),
   три за nginx ≈190–197 RPS (p99 150–195 мс), помилок 0. **Виграш у RPS малий
   (+5…15 %), основний ефект — стабільніший хвіст (p99).** Причина: усі екземпляри
   ділять один LocalStack (емуляція DynamoDB ≈ 90 мс на запит) і один ПК — це
   наступне вузьке місце; у справжньому AWS воно було б масштабованим.

## 4. Що треба знати

- **Навіщо балансувальник.** Єдина точка входу, розподіл навантаження, масштабування
  вшир, **висока доступність** (відмова одного екземпляра не зупиняє сервіс).
  Передумова — **stateless** застосунок: стан у спільному сховищі (DynamoDB/S3), а не
  в пам'яті екземпляра.
- **Алгоритми nginx.** `round robin` (за замовчуванням, рівномірно для однакових
  запитів), `least_conn` (до найменш завантаженого — для різних за тривалістю
  запитів), `ip_hash` (прив'язка клієнта до вузла — суперечить stateless), `weight`.
- **Health check.** *Пасивний* (open-source nginx): `max_fails` невдач за
  `fail_timeout` виводять вузол на `fail_timeout`; невдалий запит повторюється на
  іншому (`proxy_next_upstream`). *Активний* (nginx Plus, HAProxy, Traefik): окремі
  проби `/health`. У нас `/health` потрібен Docker (`HEALTHCHECK`, `depends_on:
  service_healthy`) і людині; nginx його не опитує.
- **Повтор запитів і ідемпотентність.** Запит, що не дійшов до вузла (connection
  refused), безпечно повторити; POST, який уже оброблявся, — ні (тому за
  замовчуванням nginx не повторює небезпечні методи).
- **`/health` і готовність.** Глибока перевірка (DynamoDB, S3) краще за «процес живий»:
  екземпляр без бази не має отримувати трафік. Мілка (liveness) vs readiness.
- **nginx як SPOF.** Єдиний балансувальник — єдина точка відмови; усувається
  Keepalived/VRRP, кількома балансувальниками з DNS, хмарним L4.
- **Docker.** `restart: unless-stopped` не повертає контейнер після ручного
  `docker stop`; `depends_on` з `service_healthy`/`service_completed_successfully`;
  імена сервісів = DNS у мережі compose.
- **Зв'язок з кодом ЛР №8.** Це та сама функція: `server.py` лише перетворює HTTP
  у подію API Gateway і викликає `lambda_handler`.

## 5. Ймовірні питання

| Питання | Відповідь |
| --- | --- |
| Чому три окремі сервіси, а не `--scale app=3`? | Явні імена й порти (app1 на `:8081` для порівняння), зручний `docker stop`; nginx резолвить DNS при старті — з `scale` IP-адреси змінюються. |
| Що станеться, якщо впаде nginx? | Недоступність усього сервісу — він SPOF. Рішення: два nginx + VRRP або зовнішній LB. |
| Чому запити не зникають, коли app2 вимкнено? | nginx отримує connection refused/timeout, позначає вузол збійним і повторює запит на наступному вузлі (`proxy_next_upstream`). |
| Чому app2 не одразу повертається після `start`? | `fail_timeout=10s`: вузол вважається недоступним 10 с; після цього nginx знову пробує. |
| Чим `/health` кращий за ping порту? | Перевіряє залежності (DynamoDB, S3); при збої віддає 503. |
| Чи можна ходити з цього ж клієнта на різні екземпляри? | Так: нічого не зберігається в екземплярі, `X-Instance-ID` показує, хто відповів. |
| Чому LocalStack один, а не по одному на екземпляр? | Інакше дані були б різними: спільне сховище — умова stateless. Це також обмежує виграш у продуктивності. |
| Active vs passive health check? | Див. розділ 4. |

## 6. Чесні межі

- Пасивна перевірка: перший запит після падіння може потрапити на мертвий вузол і
  повторитись (затримка до `proxy_connect_timeout`, 1 с).
- Один хост і один LocalStack: результати продуктивності орієнтовні.
- nginx — єдиний балансувальник (SPOF); немає TLS.
- Тут перевірено без Docker: сервер, `/health`, `lbtool distribution/load`,
  `smoke.ps1` проти того самого `server.py`, 28 unit-тестів. **Docker-частину
  (збірка образу, compose, nginx, `outage`) треба прогнати вам.**

## 7. Чекліст

- [ ] ЛР 8 зупинено (`down`), `aws/lb` стек піднято: app1–3 `(healthy)`
- [ ] `distribution` показує три `app-N` приблизно порівну
- [ ] Знаю різницю результату `outage` через nginx і напряму
- [ ] Відповідаю на: round robin vs least_conn, active vs passive, SPOF nginx
- [ ] Звіт: виводи й скриншоти за списком у звіті
- [ ] Гілка `devops/lab9` запушена
