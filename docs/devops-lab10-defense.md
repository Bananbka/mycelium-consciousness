# ЛР №10 (DevOps): інструкція до здачі

Тема: моніторинг і аналіз метрик застосунку через Amazon CloudWatch у LocalStack.
Гілка: `devops/lab10` (від `devops/lab9`). Код: `aws/lb/metrics.py`, `aws/monitoring/`.
Звіт: [devops-lab10-report.md](devops-lab10-report.md).

> Формат здачі в PDF не вказано (як у ЛР 3–9: гілка + звіт + показ наживо). **Уточніть
> у викладача** вимоги до звіту й графіків.

## 1. Що вимагає завдання → де це зроблено

| Вимога | Де показати |
| --- | --- |
| 1. Доступ до CloudWatch API у LocalStack | `aws/lb/docker-compose.yaml` (`SERVICES: …cloudwatch`); `aws … cloudwatch list-metrics` |
| 2. Збір запитів, помилок, тривалості | `aws/lb/metrics.py` · `MetricsPublisher.record` (рядок 81), виклик у `server.py` (рядок 113) |
| 3. Публікація через `PutMetricData` | `metrics.py` · `build` (рядок 100) і `flush` (рядок 154, виклик `put_metric_data` — рядок 161) |
| 4. Читання через `GetMetricStatistics` / `GetMetricData` | `aws/monitoring/scripts/report.py` · `fetch` (рядок 109), обидва API (`--api statistics\|data`) |
| 5. Запити за інтервал, частка помилок, середній час | `report.py` · `summarize` (рядок 67): Errors/RequestCount, Duration Sum/SampleCount |
| 6. CloudWatch Alarm | `aws/monitoring/scripts/alarms.ps1` (`put-metric-alarm`, рядки 41–58) |
| 7. Контрольоване навантаження | `scenario.py` · `run_phase` (рядок 50) |
| 8. Штучні помилки/затримки, перехід Alarm | `aws/lb/chaos.py` + `/admin/chaos` (`server.py` рядок 63, застосування — рядок 93) |
| 9. Графіки | `report.py` · `draw` (рядок 165) → `docs/img/lab10-metrics.png` |
| 10. Висновки | `aws/monitoring/README.md` («Що показав експеримент»), звіт |

## 2. Підготовка за годину до здачі

```powershell
cd "D:\studying\4.1\highload python\clone-factory-aws"
aws --version                                                      # AWS CLI встановлено
docker compose -f aws/docker-compose.yaml down                      # якщо піднятий стек ЛР 8 (той самий контейнер localstack)
docker compose -f aws/lb/docker-compose.yaml up -d --build
docker compose -f aws/lb/docker-compose.yaml ps                     # app1-3 (healthy), localstack (healthy)
.\aws\monitoring\scripts\alarms.ps1                                 # аларми створено, стан OK
uv run --with boto3 python aws/monitoring/scripts/scenario.py       # ≈5 хв — ПРОЙДИ НАПЕРЕД
uv run --with boto3 --with matplotlib python aws/monitoring/scripts/report.py
```

Образ LocalStack — **4.14.0** (див. розділ 6): новіші версії просять токен, а 3.x не
розуміє CloudWatch-запитів сучасного AWS CLI/boto3.

## 3. Сценарій показу

1. **Архітектура** (1 хв): схема з `aws/monitoring/README.md`. «Три екземпляри за nginx
   самі публікують метрики в CloudWatch; на них два аларми; я створюю навантаження й
   штучні збої та дивлюсь, як змінюються метрики й стани алармів».
2. **Метрики в CloudWatch** (1 хв):
   ```powershell
   $e = "http://localhost:4566"
   aws --endpoint-url $e cloudwatch list-metrics --namespace Mycelium/Api --query "Metrics[].[MetricName,Dimensions[0].Value]" --output text
   ```
   Поясни: чотири метрики × два набори вимірювань (вся система `Scope=all` і кожен
   `InstanceId`). Покажи `metrics.py`: накопичення вікна, `PutMetricData` кожні 5 с
   набором статистик.
3. **PutMetricData вручну** (30 с):
   ```powershell
   aws --endpoint-url $e cloudwatch put-metric-data --namespace Demo/Manual --metric-name Hello --value 42 --unit Count
   aws --endpoint-url $e cloudwatch get-metric-statistics --namespace Demo/Manual --metric-name Hello --start-time (Get-Date).AddMinutes(-5).ToUniversalTime().ToString("s") --end-time (Get-Date).AddMinutes(1).ToUniversalTime().ToString("s") --period 60 --statistics Sum
   ```
4. **Аларми** (1 хв): `aws … cloudwatch describe-alarms --query "MetricAlarms[].[AlarmName,StateValue,Threshold]" --output table`.
   Поясни умови: `Errors ≥ 5 за 30 с`, `Duration Average > 150 мс`, `notBreaching`.
5. **Експеримент наживо** (5 хв) — в одному вікні запусти `scenario.py`, поки пояснюєш:
   baseline → фаза помилок (`error_rate=0.5`) → фаза затримки (`latency_ms=400`) →
   відновлення. Вивід показує зміни станів алармів. Можна показати й ручний збій:
   `Invoke-RestMethod -Method Post http://localhost:8082/admin/chaos …` (і `DELETE`).
   *Якщо часу мало:* покажи вже готові `docs/img/lab10-metrics.png` і `aws/monitoring/results/`.
6. **Аналіз** (2 хв): `report.py` → таблиця (запити, RPS, частка помилок, середній
   час по фазах) і графіки. Прокоментуй: помилки 50 % при ін'єкції 0,5; середній
   час 410 мс при затримці 400 мс; пропускна здатність падає в фазі затримки.
7. **GetMetricData** (30 с): `report.py --api data` — ті самі числа іншим API.

## 4. Що треба знати (теорія)

- **CloudWatch.** Сервіс метрик, логів і алармів. **Метрика** = простір імен
  (`Mycelium/Api`) + ім'я + вимірювання (dimensions) + набір точок даних.
  Вимірювання — пари ключ/значення, що ділять метрику на серії (`InstanceId`).
- **PutMetricData.** Публікація власних метрик. Можна слати окреме значення або
  **набір статистик** (`SampleCount`, `Sum`, `Min`, `Max`) — економить виклики й
  гроші; обмеження: з наборів **не відновити перцентилі**.
- **Роздільність.** Стандартна — 1 хв (періоди кратні 60 с); висока
  (`StorageResolution=1`) — до 1 с, періоди 10/30 с. Ми публікуємо високу, щоб
  аларми з періодом 30 с були коректні.
- **Статистики й період.** `Sum`, `Average`, `Minimum`, `Maximum`, `SampleCount`,
  `pNN`. `GetMetricStatistics` — простіше, одна метрика; `GetMetricData` — кілька
  запитів і **метричні вирази** (math), пагінація, рекомендований сучасний API.
- **Обчислення показників.** Запити за інтервал = Sum(RequestCount); частка помилок =
  Sum(Errors)/Sum(RequestCount) (не середнє відсотків!); середній час =
  Sum(Duration.Sum)/Sum(Duration.SampleCount) (зважене, а не середнє середніх).
- **Аларм.** Стани `OK`, `ALARM`, `INSUFFICIENT_DATA`. Параметри: метрика +
  статистика + `period`, `evaluation-periods`, `datapoints-to-alarm`, `threshold`,
  `comparison-operator`, `treat-missing-data`. Дії (SNS, Auto Scaling) — не в цій лабі.
- **Чому два набори вимірювань.** Одна серія на всю систему (для алармів) і серії по
  екземплярах (для діагностики «хто винен»). Кардинальність вимірювань коштує грошей
  і ускладнює запити.
- **Навантаження й збої.** Контрольоване навантаження (закритий цикл, N воркерів) +
  fault injection (`error_rate`, `latency_ms`) дають повторюваний експеримент.
- **Observability.** Метрики відповідають «що/скільки», логи — «що саме», трасування —
  «де». RED-метрики: Rate, Errors, Duration — саме їх ми збираємо.

## 5. Ймовірні питання

| Питання | Відповідь |
| --- | --- |
| Чому набір статистик, а не метрика на запит? | Менше викликів `PutMetricData` (ліміти, вартість); ціна — немає перцентилів, лише Sum/Min/Max/середнє. |
| Як порахувати частку помилок? | Sum(Errors) / Sum(RequestCount) за період; середнє відсотків неправильне, бо періоди мають різну кількість запитів. |
| Чому середній час у фазі помилок менший? | Ін'єктований збій відповідає, не ходячи в DynamoDB; тому середнє може «покращуватись» під час аварії — дивись разом із частками помилок. |
| Чому аларм не спрацював миттєво? | Аларм оцінюється з періодичністю й по вікну `period` (30 с); у LocalStack затримка 30–90 с; ще є затримка публікації (5 с). |
| Різниця `GetMetricStatistics` і `GetMetricData`? | Друге: багато метрик за один запит, метричні вирази, пагінація; перше — одна метрика, простіше. |
| Що таке `treat-missing-data`? | Як аларм трактує періоди без даних: `notBreaching` — не збій (так для «немає трафіку»), `breaching`, `ignore`, `missing`. |
| Що змінилось би в реальному AWS? | Метрики стандартної роздільності (60 с), реальна вартість викликів, SNS-сповіщення, точніші аларми; код і CLI-команди ті самі, без `--endpoint-url`. |
| Чому ви додали `Scope=all`? | LocalStack зіставляє вимірювання як підмножину: запит по `Service` повертав і загальну серію, і серії екземплярів — подвійний підрахунок. Виявлено порівнянням із клієнтським лічильником. |
| Чому LocalStack 4.14.0? | 3.x не розуміє CloudWatch-запитів сучасних клієнтів (CBOR), `latest` (2026.x) вимагає токен. |

## 6. Чесні межі

- Немає перцентилів (p95/p99): лише середнє, максимум, кількість.
- LocalStack — емуляція: аларми оцінюються із затримкою 30–90 с, історія алармів
  (`DescribeAlarmHistory`) має неточні часові позначки (графіки беруть власний журнал
  опитування).
- Немає сповіщень (SNS/e-mail), лише стани алармів.
- Один хост: навантажувач, застосунок і LocalStack ділять ПК — числа орієнтовні.
- Перевірено вживу автором: публікація метрик, обидва API читання, обидва аларми
  (OK → ALARM → OK), `set-alarm-state`, `/admin/chaos`, графіки. Прогін на вашій
  машині дасть інші числа, але ті самі закономірності.

## 7. Чекліст

- [ ] `docker compose … ps`: app1–3 і localstack `(healthy)`, версія LocalStack 4.14.0
- [ ] `alarms.ps1` створює два аларми, стан `OK`
- [ ] `scenario.py` + `report.py` пройдені наперед; графіки в `docs/img/`
- [ ] Знаю: набір статистик, `Scope=all`, обчислення частки помилок і середнього
- [ ] Звіт заповнений виводами й скриншотами (список у звіті)
- [ ] Гілка `devops/lab10` запушена
