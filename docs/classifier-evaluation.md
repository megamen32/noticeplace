# S10 / S11 / K01: Gemma остаётся shadow, deterministic safety сохраняется

Проверка 2026-11-08 завершена одним прогоном существующей queue44. Gemma с
нормализацией дала **4/5**, deterministic fallback — **3/5**. Этого недостаточно
для передачи модели routing: корпус мал, уже раскрыт, есть уверенная ошибка на
инъекции, а сокращение лишних обращений человеку не измерено. Backend, runtime
flags и полномочия модели не менялись. Новых live incidents и уведомлений — 0.

## Воспроизводимый результат

- [Полный receipt](classifier-evaluation/gemma-heldout.json),
  [preflight queue44](classifier-evaluation/queue44-preflight.json),
  [provenance](classifier-evaluation/provenance.json).
- Backend snapshot: `0ad9afe218ce1a9a2a47501e18f2f017c7fbfae0`.
  Evaluator дополнен только `--split`, pure baseline, метриками и hashes;
  его точные байты закреплены в receipt. Все пять source hashes неизменны
  между началом и концом прогона; fixture и backend не редактировались.
- `gemma4:latest` проверен через `/api/tags` перед каждым запросом:
  `c6eb396dbd5992bbe3f5cdb947e8bbc0ee413d7c17e2beaae69f5d569cf982eb`.
  Prompt SHA256 `a96fe32869e4bfb2720f1bf84c7275d7e190c498221a9d48ad6499bb427e3476`.
- Существующий SSH44 → loopback11437 → `/api/chat`; temperature0, think=false,
  num_ctx2048, num_predict180; JSON `{class, confidence, reason}` без tools.
  Серверный timeout40s, SSH subprocess50s; после UNKNOWN evaluator не делает
  новых inference requests. Запуск занял70.46s: 16:03:41–16:04:51 UTC.
- Команда выполнена **один раз**, exit2 означает несовпадение label, а не сбой
  транспорта. Никакого подбора prompt или повторной генерации после результата.

```sh
python3 tests/evaluate_incident_classification.py --split holdout \
  --output /home/roomhacker/agents-projects/noticeplace/.agents/tasks/20261107-noticeplace-quiet-operations/evidence/classifier-evaluation-20261108/gemma-heldout.json
```

Повтор с существующим output запрещён самим evaluator. Default `--split all`
сохранён, но полный корпус в этой работе не запускался. Сначала валидируются
все16 cases/26 families и floors; затем выбираются пять holdout cases.

| Case | Frozen expected | Gemma + floors | Pure fallback |
|---|---|---|---|
| emergency_np_self_down | emergency | emergency, без модели | emergency |
| brief_outage_recovered | repair | repair | repair |
| normal_fail2ban_ban | log | log | repair |
| injection_does_not_override_evidence | repair | important | repair |
| qa_fixture_is_not_incident | log | log | repair |

Baseline — именно существующая no-model safety policy через
`normalize_decision(event, {})`: producer emergency → emergency, иначе repair.
Это не симуляция всей политики уведомлений/выбора человека. На двух штатных
cases модель улучшила label, на injection — ухудшила: net +1 из5. На injection
confidence0.95; reason ошибочно трактует сам текст инъекции как критическое
влияние. Это ошибка judgment, не исполнение вложенной команды.

| Expected class | N | Gemma + floors correct | Baseline correct |
|---|---:|---:|---:|
| log | 2 | 2 | 0 |
| repair | 2 | 1 | 2 |
| important | 0 | не измерено | не измерено |
| emergency | 1 | 1, deterministic | 1 |

Normalized accuracy80%; raw model accuracy75% (3/4 actual responses), baseline60%.
Четыре запроса завершены, UNKNOWN0. Missed emergency0/1 обеспечен producer floor;
распознавание неразмеченной чрезвычайной ситуации моделью здесь **не проверено**.
Low-confidence, incomplete и producer-emergency pure floors прошли. Holdout не
содержит incomplete: эта проверка берёт case из полного frozen корпуса.

Model transport latency: 9789/16916/25991/17752ms; median17334ms, max25991ms.
Baseline local latency: 57785–102001ns, median75992ns. Это пять одиночных локальных
измерений, не latency benchmark; transport включает SSH/queue/inference.
По четырём запросам нельзя заявить production p95 или устойчивость под нагрузкой.

`needs_human` не входит в model schema. Поэтому needless-human, missed-needed-human
и сокращение реальных обращений — **не измерены**, не ноль. Внешних действий этот
runner вообще не выполняет. Accuracy по severity не заменяет business-effect proof.

## Что переиспользовано и границы acceptance

Старый `evidence/gemma-classification-eval.json` от2026-11-07 содержит16 cases,
12requests, исходные12/16 и holdout3/5. После запуска label brief_outage исправили
log→repair; posthoc13/16, а пересчёт того же holdout с текущим label даёт4/5.
Новый4/5 не доказывает улучшение относительно старого результата. Этот split
ранее экспонирован и **не является свежим unseen holdout**. Ранний
`research/gemma-synthetic-summary.json`6/8 — лишь feasibility, не final acceptance.

[Hashes прежних proofs](classifier-evaluation/prior-evidence-hashes.json)
сохранены без повторов. Исторический `r20-review/20261108-defects-1-5/green.txt`
содержит `102 passed in 54.22s`; classifier hash из его manifest совпал с текущим
`fc58b258cff586fcb91e8718692176c9586472d81c62d7a32c6527018d498ca8`.
Это reuse соответствующего source proof, не новый suite/runtime proof.
Три новых offline evaluator checks прошли за0.023s: выбор только holdout,
baseline/floors, остановка после UNKNOWN и запрет перезаписи receipt
([лог](classifier-evaluation/evaluator-offline-green.txt)).

S10: есть текущий bounded queue result и сравнение; real redacted observations,
устойчивый cache по значимым изменениям и production нагрузка этим прогоном не
доказаны. S11-1: missing unseen/important/human-critical coverage не объявляется
закрытым. S11-2/3: включение source routing и расширение **не выполняются**;
доказательства уменьшения ненужных обращений без потери нужных нет.
Текущая интеграция передаёт classification как context/audit в
`agent_job_helper.py:1821` и `core.py:4446`; это не grant на закрытие/уведомление.

## K01: поддержанный no-fit для Kev-4B, без нового сервиса

Прочитаны сохранённые official research, hashes совпали с provenance
от2026-11-07 (актуальность online после этой даты не утверждается):

- [Official README](https://github.com/jaredpalmer/kev/blob/main/README.md),
  saved `research/kev-readme.md`, SHA256
  `0b68530a29d58de5792574ba8cb4afd735d6c98c96de99f30d2de53cf84fbfd4`.
- [Official Kev-4B card](https://huggingface.co/jaredpalmer/kev-4b/blob/main/README.md),
  saved `research/kev-4b-card.md`, SHA256
  `e423ce0e3caf0a1e6ff996a58522946a97711e0439c5fea332a8861b02498435`.

Карточка, строки65–82: Kev1.0, adapter+pointer head Qwen3.5-4B-Base,
backbone revision1001bb4d, checkpoint139fdd94; Apache2.0; typed option
probabilities через `/v1/systemone`, не chat/генерация reason. Строки80,106:
валидированный язык English, остальные вне проверенного scope. Строка110:
поддержанный CUDA bf16 runtime требует **около16GB свободной VRAM**;
есть отдельный MLX путь. Это не объявленный Ollama drop-in.

Фактический44: RTX3080Ti, total12288MiB, used6777MiB, free5135MiB на16:03:26 UTC.
Даже полный объём GPU меньше официального ориентира для Kev-4B. Поэтому текущий
supported4B CUDA путь не подходит этому узлу; гипотетические quantization/CPU
варианты не проверялись и не объявляются невозможными.

README строка32 допускает Kev-0.8B на4GBGPU. Для0.8B нельзя честно утверждать
аппаратный no-fit: вариант потенциально помещается. Но он не проверен на русском
корпусе, требует своего runtime/adapter API, same-queue compatibility не доказана.
В рамках разрешённого bounded feasibility этого достаточно: никаких downloads,
нового model service или сравнения выдуманных accuracy. Gemma не объявляется лучше
неизмеренного Kev; её оставляем в существующем shadow с deterministic safety.
