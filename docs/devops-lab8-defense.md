# ЛР №8 (DevOps): інструкція до здачі

Тема: serverless-застосунок у LocalStack — Lambda + API Gateway + DynamoDB.
Гілка: `devops/lab8`. Код: `aws/`. Звіт: [devops-lab8-report.md](devops-lab8-report.md).

> У PDF не сказано, як саме здавати. Для ЛР 3–7 було: гілка в репозиторії + звіт
> `docs/devops-labN-report.md` + показ наживо. Тут так само; **формат уточніть у
> викладача** (скриншоти, архів, посилання).

## 1. Що вимагає завдання → де це зроблено

| Вимога | Де показати |
| --- | --- |
| Встановити LocalStack, AWS CLI | `aws/docker-compose.yaml`; `aws --version`; `docker ps` |
| Lambda-функція, що обробляє HTTP-запити | `aws/lambda/handler.py` · `lambda_handler` (рядки 126–144) |
| Таблиця DynamoDB | `aws/scripts/deploy.ps1` · крок «DynamoDB tables» (рядки 55–66); `aws dynamodb list-tables` |
| API Gateway → Lambda | `deploy.ps1` · «API Gateway REST API» (рядки 104–142): `Ensure-Resource`, `put-method`, `put-integration` (`AWS_PROXY`), `create-deployment` |
| Створення, отримання, видалення записів | маршрути `POST/GET/DELETE /clones…` у `handler.py` · `ROUTES` (рядки 47–79); логіка в `service.py` |
| Перевірка HTTP-клієнтом | `aws/scripts/smoke.ps1` (22 перевірки) або `curl.exe`/Postman |
| Автоматизація сценарієм AWS CLI | `aws/scripts/deploy.ps1` (+ `destroy.ps1`) |
| Документація архітектури й послідовності | `aws/README.md` (схема, таблиця відповідності), звіт |

## 2. Підготовка за годину до здачі

```powershell
cd "D:\studying\4.1\highload python\clone-factory-aws"
git status                                            # гілка devops/lab8
aws --version                                         # AWS CLI v2 встановлено?
docker compose -f aws/docker-compose.yaml up -d
docker compose -f aws/docker-compose.yaml ps          # чекати (healthy)
.\aws\scripts\deploy.ps1                              # перший раз ~ кілька хвилин (pull образу Lambda)
.\aws\scripts\smoke.ps1                               # має бути "All checks passed"
```

Прожени це **до** здачі: перший старт Lambda найдовший. Якщо LocalStack не
піднімається — розділ «Якщо LocalStack не стартує» в `aws/README.md`.

## 3. Сценарій показу (що виконати і що сказати)

1. **Архітектура** (1 хв). Покажи схему з `aws/README.md`. Скажи: «Це наш проєкт
   Mycelium Consciousness на AWS-сервісах: FastAPI замінила Lambda, nginx — API
   Gateway, Redis-буфер і метадані — DynamoDB, MinIO — S3. Код кодування копій і
   політики за рівнями підписки ті самі, що в основному проєкті».
2. **Середовище** (30 с). `docker compose -f aws/docker-compose.yaml ps`,
   `aws --version`. Поясни: LocalStack — емулятор AWS API на одному порту 4566;
   тому в кожній команді `--endpoint-url http://localhost:4566`.
3. **Розгортання сценарієм** (2 хв). `.\aws\scripts\deploy.ps1`. Коментуй етапи:
   бакет → 3 таблиці DynamoDB → IAM-роль → пакет і Lambda → API Gateway
   (ресурси, методи, інтеграція) → деплой стадії `dev`. В кінці виводиться
   `API_URL`. Покажи, що скрипт можна запустити повторно (він спершу знищує старе).
4. **Ресурси в AWS CLI** (1 хв):
   ```powershell
   $e = "http://localhost:4566"
   aws --endpoint-url $e dynamodb list-tables
   aws --endpoint-url $e lambda list-functions --query "Functions[].FunctionName"
   aws --endpoint-url $e apigateway get-rest-apis --query "items[].name"
   aws --endpoint-url $e s3 ls
   ```
5. **Робота API HTTP-клієнтом** (3 хв) — головний показ. Запусти `smoke.ps1` і
   коментуй або виконай вручну:
   ```powershell
   $api = (Get-Content aws\build\api-url.txt).Trim()
   Invoke-RestMethod "$api/health"
   Invoke-RestMethod -Method Post "$api/clones" -ContentType application/json -Body '{"designation":"alpha","tier":"standard"}'   # create
   Invoke-RestMethod "$api/clones/alpha"                                                                                        # get
   Invoke-RestMethod -Method Post "$api/clones/alpha/memories" -ContentType application/json -Body '{"content":"first frame"}'
   Invoke-RestMethod "$api/clones/alpha/memories/status"                                                                         # buffered_frames
   $b = Invoke-RestMethod -Method Post "$api/clones/alpha/backups"                                                              # rollup -> S3
   Invoke-RestMethod "$api/clones/alpha/backups/$($b.backup_id)"                                                                # get з payload
   Invoke-RestMethod -Method Delete "$api/clones/alpha/backups/$($b.backup_id)"                                                 # delete
   ```
   Покажи також помилки: дубль клона → 409, неправильна назва → 422, неіснуючий → 404.
6. **Дані справді в DynamoDB і S3** (1 хв):
   ```powershell
   aws --endpoint-url $e dynamodb scan --table-name mycelium-clones
   aws --endpoint-url $e s3 ls s3://mycelium-backups --recursive
   ```
   (виконувати, поки клон і копія ще не видалені).
7. **Логи Lambda** (30 с, за бажанням): `aws --endpoint-url $e logs describe-log-groups`.

## 4. Що треба знати (теорія)

**Lambda.** Функція без сервера: платиш за виклики. `handler(event, context)` —
подія описує запит, `context` — метадані виклику (`aws_request_id`). Виконується в
середовищі виконання, яке **повторно використовується** між викликами (тому
`get_service()` будує клієнти один раз — «warm start»); перший виклик після
простою — **cold start**. Ліміти: таймаут до 15 хв, пам'ять, розмір пакета. Роль
IAM визначає, до чого функція має доступ.

**API Gateway.** Публічна точка входу HTTP → Lambda. **REST API** (v1) з
**proxy-інтеграцією** (`AWS_PROXY`): Gateway передає весь запит у Lambda як подію
(`httpMethod`, `resource`, `path`, `pathParameters`, `body`), а Lambda повертає
`{statusCode, headers, body}`. Ресурси (`/clones/{clone_id}`), методи, стадії
(`dev`), деплой стадії. HTTP API (v2) дешевший і простіший, REST API має більше
можливостей (usage plans, валідація).

**DynamoDB.** NoSQL, ключ–значення/документи. **Partition key** розподіляє дані,
**sort key** впорядковує всередині партиції: `clone_id` + `frame_id`/`backup_id`.
Тому «усі кадри клона» — це `Query` по ключу (швидко), а не `Scan` (читає все).
`PAY_PER_REQUEST` — оплата за запит. Умовний запис
(`attribute_not_exists`) дає атомарну перевірку «такого клона ще нема» → 409.
Елемент ≤ 400 КБ — тому payload копії лежить у S3, а в таблиці лише метадані
(так само, як у Postgres + MinIO в основному проєкті).

**S3.** Об'єктне сховище; тут — бінарний payload (`msgpack` + gzip) за ключем
`backups/{clone}/{backup_id}.bin`.

**IAM.** Роль із *trust policy* (хто може її взяти — `lambda.amazonaws.com`) і
політиками дозволів. LocalStack **не примушує** IAM, але ресурс потрібен
структурно.

**LocalStack.** Емулятор AWS API локально; не гарантує повної поведінки AWS
(IAM, ліміти, консистентність). Ті самі `aws` CLI і boto3, лише з `endpoint-url`.
У функції ми визначаємо endpoint за змінними оточення — без override код ходить у
справжній AWS.

**Автоматизація.** `deploy.ps1` — ідемпотентний (знищує старе й створює заново),
перевіряє готовність LocalStack, чекає `Active` для функції, друкує `API_URL`.

**Чому код розділено на `handler` / `service` / `stores`.** `service` не знає про
boto3, тож його можна тестувати без AWS (27 тестів, `uv run pytest aws/tests`) і
запускати локально (`local_server.py`).

## 5. Ймовірні питання

| Питання | Відповідь |
| --- | --- |
| Чим Lambda відрізняється від контейнера? | Немає сервера й процесу, що живе постійно: платформа запускає код на виклик, масштабує і зупиняє; є cold start і ліміти часу. |
| Що таке proxy-інтеграція? | Gateway віддає запит у Lambda як є, а відповідь Lambda повертає клієнту; маршрутизацію й валідацію робить код функції. |
| Чому три таблиці, а не одна? | Відповідає сутностям основного проєкту (clones/frames/backups) і різним ключам доступу; можна й single-table, але читабельність нижча. |
| Чому payload у S3? | Ліміт елемента DynamoDB 400 КБ, а payload росте; об'єктне сховище дешевше і створене під «записав цілим, прочитав цілим». |
| Як гарантовано, що rollup не втратить дані? | Спершу S3, потім метадані, лише в кінці видаляємо кадри: збій посередині залишає зайві кадри, але не губить. Транзакції S+DynamoDB немає. |
| Як вирішується дубль клона? | Умовний запис `attribute_not_exists(clone_id)` → ConditionalCheckFailed → 409. |
| Що буде, якщо Lambda впаде? | Gateway поверне 5xx; дані вже записані лишаються; повтор безпечний, окрім POST (кадр може задвоїтись — немає ключа ідемпотентності). |
| Чому в Lambda endpoint `http://localstack:4566`? | Контейнери функцій у мережі `mycelium-aws-net`; контейнер LocalStack доступний там за іменем. |
| Чим LocalStack відрізняється від AWS? | Це емуляція: немає реального IAM, лімітів, регіональної поведінки, часу старту. |

## 6. Чесно про межі

- Немає автентифікації (в основному проєкті JWT). У AWS це authorizer (Lambda
  authorizer / Cognito) — не входить у завдання.
- Rollup не транзакційний між S3 і DynamoDB (див. вище).
- `GET /clones/{id}/backups` без пагінації (≈ мало записів на клона через retention).
- Перевірено: unit-тести, `smoke.ps1` проти `local_server.py` (той самий handler).
  Розгортання в живому LocalStack треба прогнати вам (див. розділ 2).

## 7. Чекліст перед здачею

- [ ] `docker compose … ps` → `(healthy)`; `aws --version` працює
- [ ] `deploy.ps1` проходить без помилок, друкує `API_URL`
- [ ] `smoke.ps1` → `All checks passed`
- [ ] Звіт заповнений: вивід команд + скриншоти (список у звіті)
- [ ] Знаю відповіді з розділу 5, можу показати `handler.py` / `deploy.ps1`
- [ ] Гілка `devops/lab8` запушена (якщо вимагають репозиторій)
