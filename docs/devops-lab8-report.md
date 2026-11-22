# Звіт: DevOps, лабораторна робота №8

Serverless-застосунок у локальному AWS-середовищі: Lambda + API Gateway + DynamoDB у LocalStack.
Застосунок — serverless-версія нашого проєкту Mycelium Consciousness
(клони, кадри пам'яті, резервні копії, рівні підписки), код у [`aws/`](../aws/).
Інструкція до здачі: [devops-lab8-defense.md](devops-lab8-defense.md).

> **Як заповнити.** Усі блоки `<вивід …>` замініть реальним виводом, скопійованим з
> терміналу (довгі рядки можна скоротити `…`). Список потрібних скриншотів — у
> розділі 8.

## 1. Середовище

```
> docker --version
<вивід>
> aws --version
<вивід>
> docker compose -f aws/docker-compose.yaml up -d
<вивід>
> docker compose -f aws/docker-compose.yaml ps
<вивід: контейнер localstack, STATUS (healthy)>
```

Образ LocalStack: `<localstack/localstack:4.14.0 або інший, який використали>`.
LocalStack — емулятор AWS API на одному порту `4566`; AWS CLI викликається з
`--endpoint-url http://localhost:4566` і фіктивними ключами `test`/`test`.

## 2. Архітектура

Скопіюйте схему з [`aws/README.md`](../aws/README.md) (mermaid) або скриншот.

| Основний проєкт | ЛР №8 |
| --- | --- |
| FastAPI | Lambda `mycelium-api` (`aws/lambda/handler.py`) |
| nginx / Ingress | API Gateway REST API, proxy-інтеграція `AWS_PROXY`, стадія `dev` |
| Redis Stream + `memory_buffers` | DynamoDB `mycelium-frames` (`clone_id` + `frame_id`) |
| `clone_profiles` | DynamoDB `mycelium-clones` (`clone_id`) |
| `memory_backups` | DynamoDB `mycelium-backups` (`clone_id` + `backup_id`) |
| MinIO | S3 `mycelium-backups` (payload `msgpack`+gzip) |

Три шари коду: `handler.py` (подія API Gateway → маршрут → відповідь), `service.py`
(бізнес-логіка без boto3, використовує `shared.backup_codec` і
`shared.subscriptions` з основного проєкту), `stores.py` (DynamoDB і S3 через boto3).

## 3. Послідовність розгортання (`deploy.ps1`)

```
> .\aws\scripts\deploy.ps1
<вивід: кроки Waiting for LocalStack → … → Deployed. API_URL = …>
```

Кроки скрипта і відповідні команди AWS CLI:

| # | Крок | Команда |
| --- | --- | --- |
| 1 | очищення попереднього розгортання | `destroy.ps1` |
| 2 | пакування (код + `shared` + `msgpack` під Linux) | `python aws/scripts/package.py` → `build/function.zip` |
| 3 | бакет S3 | `aws s3api create-bucket` |
| 4 | таблиці DynamoDB | `aws dynamodb create-table` ×3 |
| 5 | IAM-роль | `aws iam create-role --assume-role-policy-document` |
| 6 | Lambda | `aws lambda create-function`, очікування стану `Active` |
| 7 | API Gateway | `create-rest-api`, `create-resource`, `put-method`, `put-integration`, `create-deployment` |

## 4. Створені ресурси

```
> aws --endpoint-url http://localhost:4566 dynamodb list-tables
<вивід>
> aws --endpoint-url http://localhost:4566 lambda list-functions --query "Functions[].[FunctionName,Runtime]"
<вивід>
> aws --endpoint-url http://localhost:4566 apigateway get-rest-apis --query "items[].[name,id]"
<вивід>
> aws --endpoint-url http://localhost:4566 s3 ls
<вивід>
```

## 5. API: створення, отримання, видалення

Маршрути (API Gateway → Lambda): таблиця з `aws/README.md`.

```
> $api = (Get-Content aws\build\api-url.txt).Trim()
> Invoke-RestMethod -Method Post "$api/clones" -ContentType application/json -Body '{"designation":"alpha","tier":"standard"}'
<вивід: create → 201>
> Invoke-RestMethod "$api/clones/alpha"
<вивід: get>
> Invoke-RestMethod -Method Post "$api/clones/alpha/memories" -ContentType application/json -Body '{"content":"first frame"}'
<вивід: 202>
> Invoke-RestMethod "$api/clones/alpha/memories/status"
<вивід: buffered_frames>
> $b = Invoke-RestMethod -Method Post "$api/clones/alpha/backups"
<вивід: rollup → копія в S3>
> Invoke-RestMethod "$api/clones/alpha/backups/$($b.backup_id)"
<вивід: копія з payload>
> Invoke-RestMethod -Method Delete "$api/clones/alpha/backups/$($b.backup_id)"
<вивід: 204>
```

Помилки: `POST /clones` двічі → 409, неправильна назва → 422, невідомий клон → 404
(`smoke.ps1` перевіряє це автоматично).

## 6. Автоматична перевірка

```
> .\aws\scripts\smoke.ps1
<вивід: 22 рядки PASS, All checks passed>
> uv run pytest aws/tests
<вивід: 27 passed>
```

## 7. Висновки

- Lambda + API Gateway дають serverless-версію нашого API без жодного сервера:
  підтримується тільки код обробника.
- DynamoDB замінила Postgres/Redis для даних; важкий payload лишається в S3 (так
  само, як у MinIO в основному проєкті), бо елемент DynamoDB обмежений 400 КБ.
- Автоматизація AWS CLI-скриптом робить розгортання відтворюваним і повторюваним.
- Обмеження: LocalStack — емуляція (IAM не примушується); немає автентифікації;
  rollup між S3 і DynamoDB не транзакційний.

## 8. Скриншоти (додати у `docs/img/` і підключити)

| # | Що на скриншоті |
| --- | --- |
| 1 | `docker compose ps` — LocalStack `(healthy)` |
| 2 | вивід `deploy.ps1` до `Deployed` і `API_URL` |
| 3 | `list-tables`, `list-functions`, `get-rest-apis`, `s3 ls` |
| 4 | create / get / delete через HTTP-клієнт (Postman або термінал) |
| 5 | `smoke.ps1` — `All checks passed` |
| 6 | `dynamodb scan` та `s3 ls --recursive` (дані справді збережені) |
