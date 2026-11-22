# Звіт: DevOps, лабораторна робота №9

Балансування навантаження та доступність застосунку: три екземпляри застосунку з
ЛР №8 за nginx (docker compose), LocalStack — емулятор AWS. Код: [`aws/lb/`](../aws/lb/README.md).
Інструкція до здачі: [devops-lab9-defense.md](devops-lab9-defense.md).

> Замініть блоки `<вивід …>` реальним виводом із терміналу. Скриншоти — розділ 8.

## 1. Контейнеризація застосунку

Застосунок із ЛР №8 (`handler` / `service` / `stores`) без змін запускається як
сервіс: `aws/lb/server.py` перетворює HTTP-запит на подію API Gateway і викликає
`lambda_handler`. Образ — `aws/lb/Dockerfile` (`python:3.12-slim`, `boto3`,
`msgpack`, код ЛР №8, `shared.backup_codec`/`subscriptions`, `HEALTHCHECK`).

Кожен екземпляр повертає **власний id** у заголовку `X-Instance-ID` (змінна
`INSTANCE_ID`: `app-1`, `app-2`, `app-3`); `/health` повертає його в тілі.

## 2. Розгортання

```
> docker compose -f aws/lb/docker-compose.yaml up -d --build
<вивід>
> docker compose -f aws/lb/docker-compose.yaml ps
<вивід: localstack, lb-app1..3 (healthy), lb-nginx Up, lb-provision Exited (0)>
```

| Сервіс | Роль |
| --- | --- |
| `localstack` | емулятор AWS: DynamoDB і S3 |
| `provision` | одноразово створює таблиці й бакет |
| `app1`, `app2`, `app3` | три екземпляри застосунку |
| `nginx` | єдина точка входу `:8080`; `app1` ще й напряму на `:8081` |

## 3. Балансувальник і `/health`

`aws/lb/nginx.conf`: `upstream mycelium` з трьох серверів
(`max_fails=2 fail_timeout=10s`), алгоритм round robin, `proxy_next_upstream error
timeout http_502 http_503` (запит повторюється на наступному вузлі),
`proxy_connect_timeout 1s`.

`GET /health` перевіряє DynamoDB і S3; при недоступності залежності — 503.
Docker використовує його в `HEALTHCHECK`.

```
> Invoke-RestMethod http://localhost:8080/health
<вивід: status, checks, instance>
```

## 4. Розподіл запитів

```
> python aws/lb/scripts/lbtool.py distribution --url http://localhost:8080 --count 60
<вивід: ~20 на кожен app-N>
> docker logs lb-nginx --tail 10
<вивід: upstream=… для кожного запиту>
```

## 5. Вивід екземпляра з роботи і повернення

```
> docker stop lb-app2
> python aws/lb/scripts/lbtool.py distribution --url http://localhost:8080 --count 60
<вивід: лише app-1 і app-3, 0 помилок>
> docker start lb-app2
> Start-Sleep 12
> python aws/lb/scripts/lbtool.py distribution --url http://localhost:8080 --count 60
<вивід: знову три екземпляри>
```

Пояснення: `max_fails`/`fail_timeout` (пасивний health check) і
`proxy_next_upstream`.

## 6. З балансуванням і без

Сценарій `outage`: 30 с навантаження, на 10-й секунді контейнер зупиняється, на 20-й
стартує.

```
> python aws/lb/scripts/lbtool.py outage --url http://localhost:8080 --container lb-app2
<вивід: таймлайн; через nginx>
> python aws/lb/scripts/lbtool.py outage --url http://localhost:8081 --container lb-app1
<вивід: таймлайн; напряму в app1>
```

| | Запитів | Помилок | Під час простою |
| --- | --- | --- | --- |
| через nginx (3 екземпляри) | `<…>` | `<…>` | `<…>` |
| напряму в app1 | `<…>` | `<…>` | `<…>` |

Продуктивність (15 с, 20 воркерів):

```
> python aws/lb/scripts/lbtool.py load --url http://localhost:8081 --duration 15 --concurrency 20
<вивід>
> python aws/lb/scripts/lbtool.py load --url http://localhost:8080 --duration 15 --concurrency 20
<вивід>
```

| | RPS | p50 | p95 | p99 | Помилки |
| --- | --- | --- | --- | --- | --- |
| один екземпляр (`:8081`) | `<…>` | `<…>` | `<…>` | `<…>` | `<…>` |
| nginx + 3 екземпляри (`:8080`) | `<…>` | `<…>` | `<…>` | `<…>` | `<…>` |

## 7. Висновки

- Балансувальник дав єдину точку входу й доступність: при зупинці одного екземпляра
  клієнти через nginx помилок не бачать, прямий доступ до одного екземпляра — бачить.
- Масштабування обмежене спільними ресурсами (один хост і один LocalStack).
- Пасивна перевірка здоров'я працює через повтор запитів; активні проби потребують
  HAProxy/Traefik/nginx Plus.
- nginx лишається єдиною точкою відмови.

## 8. Скриншоти

| # | Що |
| --- | --- |
| 1 | `docker compose ps`: app1–3 `(healthy)`, nginx, provision Exited (0) |
| 2 | `distribution` — розподіл між трьома екземплярами |
| 3 | `docker stop lb-app2` і `distribution` — два екземпляри |
| 4 | `distribution` після `docker start` — знову три |
| 5 | таймлайн `outage` через nginx і напряму (обидва) |
| 6 | `load`: один екземпляр і три за nginx |
