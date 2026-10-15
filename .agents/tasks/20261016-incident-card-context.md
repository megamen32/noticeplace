# Контекст аварии в карточке Notice Place

Прямое поручение пользователя: выяснить, почему карточка fleet-health не называет сайт и исполнителя, проверить реально открытую OpenCode-сессию, исправить причину и доставить понятный результат.

Границы: notification_center/incident_card.py и focused integration regression; существующие incident/session/receipts сохраняются. Сетевой ремонт остаётся в исходной сессии ses_ebe9bf748ffe4Nq016ySr9mRJj. Новых executor sessions нет.

Текущий GSD: .planning/PROJECT.md, REQUIREMENTS.md, ROADMAP.md, STATE.md относятся к прежней Secretary call phase; не менять её состояние. Этот ограниченный debug не заменяет прежние обязательства. SPEC/PLAN для этого debug отсутствуют, маршрут original gsd-debug inline.

Доказано: inc_75d80b2defd74eceac9b0f07b31284f4 содержит artem.bezrabotnyi.com и TLS/DNS timeout. Native prompt передал их OpenCode. Renderer заменил английский title и отфильтровал body по кириллице. Session сообщила direct HTTP200 и продолжила разбор proxy-path; доставлено одно уточнение границы ремонта в текущий ход.

Приёмка: intake → incident_card_payload → renderer сохраняет цель, причины и фактическую launch identity; существующая Telegram-карточка обновлена штатным delivery worker без нового incident/launch. Не объявлять восстановление сайта по исправлению renderer.

Бюджет focused checks: одна Python workload, 1 CPU, RAM soft=hard 1 GiB, swap 0, tasks 64, temp 32 MiB/512 files, IO weight 10; ожидается до 20s с setup, аварийный wall 200s. Основание: live MemAvailable 16 GiB, существующие сервисы 1 GiB/CPU2/tasks256; suite измерить и записать. SSH/DB и соседние процессы сохраняются.

Проверка: два meaningful RED на исходном renderer; после исправления 19 focused integration checks PASS за 9.217s, peak RSS 27 MiB, cgroup RAM1GiB/CPU1/tasks64/swap0/wall200s. Первая попытка ulimit -u64 отказала fork из-за общего UID; исправлено использованием per-job systemd TasksMax. Первый systemd запуск без WorkingDirectory обнаружил0 tests и НЕ считается GREEN; повтор с правильным каталогом выполнил19.

Карточка: published main452a266, renderer-only immutable overlay установлен rolling на44/88/100, остальные установленные файлы byte-exact.8 preboot UNKNOWN/sending rows сохранены без replay. Существующая карточка chat -1004322359393/message7045/thread324 обновлена штатным worker через queue_incident_card_refresh; delivery dlv_8c26b5230a5043f0830c9db6a65741c4 sent, edited_in_place=true, projection0e980e9a69579c0b1f86f7b0abfc5c06f0da36cacb8efaf745ccd69b031850a7. Она содержит artem, TLS/DNS, исходные детали и фактическую session identity. Моя direct HTTPS HEAD проверка также200; это не independent закрытие всех probe routes.

Уточнение пользователя: затем исправить context, который передаётся native-сессии. Разрешённое расширение: notification_center/gptadmin_agent.py, agent_job_helper.py, tests/test_repair_first_incident.py. Отдельный RED доказал, что исходный adapter выбрасывает producer/plugin/type/time до actual project-launch. Исправление передаёт whitelisted bounded incident_context и добавляет source-owned различие origin/probe; неизвестные fingerprint/observation time/refs не выдумывает. Текущую сессию не запускать заново.

Context checks:75 проверок сначала выявили1 stale expectation awaiting_executor=false; тот же тест воспроизводится на исходных helper/adapter058f7f1. Проверка исправлена по действующему core contract: observer не завершает native executor. После этого73 focused checks PASS за27.133s, peak30MiB; новый actual project-launch test подтверждает producer/plugin/time/body и отсутствие выдуманного fingerprint.

Статус: карточка доставлена; контекст source проверен, activation pending.
