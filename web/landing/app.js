/* Notice Place landing. Plain JavaScript; no network requests, trackers or runtime dependencies. */
(() => {
  'use strict';
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const icons = { idle: 'crosshair', detected: 'alert', ready: 'bell', working: 'crosshair', verifying: 'crosshair', observed: 'clock', degraded: 'alert', resolved: 'check' };
  const scenarios = {
    api: { source: 'API', title: 'API недоступен', detail: 'HTTP 503. Повторные ошибки объединены в один инцидент.', plan: 'Диагностика готова. Агент предлагает восстановить сервис. Решение за тобой.', done: 'API восстановлен', proof: 'Отдельная проверка: HTTP 200. Исходный сигнал восстановился.', action: 'Восстановление сервиса', failed: 'HTTP 503 сохраняется. Проверка сама по себе не исправляет сервис.' },
    database: { source: 'Database', title: 'Запросы замедлились', detail: 'Задержка базы данных выше порога. Связанные сигналы собраны вместе.', plan: 'Диагностика выявила блокирующий запрос. Выбери дальнейшее действие.', done: 'Задержка в норме', proof: 'Отдельная проверка подтвердила нормальное время ответа базы.', action: 'Работа с блокировкой', failed: 'Повторный замер подтвердил задержку. Инцидент остаётся открытым.' },
    server: { source: 'Server', title: 'Нагрузка CPU — 92%', detail: 'Порог превышен. Уведомление содержит узел и контекст нагрузки.', plan: 'Диагностика нашла ресурсоёмкий процесс. Выбери дальнейшее действие.', done: 'Нагрузка снизилась', proof: 'Независимый замер: CPU 34%. Сигнал вернулся в допустимые границы.', action: 'Снижение нагрузки', failed: 'Нагрузка всё ещё выше порога. Нужен отдельный план действий.' },
    cron: { source: 'Cron', title: 'Задача завершилась с ошибкой', detail: 'Плановый запуск не выполнен. Ошибка и контекст сохранены в инциденте.', plan: 'Агент предлагает устранить причину и повторить запуск. Выбери действие.', done: 'Задача выполнена', proof: 'Проверка подтвердила результат повторного запуска, а не только его старт.', action: 'Повторный запуск задачи', failed: 'Результата задачи нет. Диагностика не заменяет выполнение.' },
    site: { source: 'Site', title: 'Сертификат истекает', detail: 'Внешний источник сообщает: до истечения сертификата осталось 3 дня.', plan: 'Сформирован план обновления сертификата. Выбери действие.', done: 'Сертификат обновлён', proof: 'Отдельная проверка подтвердила новый срок действия сертификата.', action: 'Обновление сертификата', failed: 'Срок действия прежний. Сертификат ещё требует обновления.' },
    backup: { source: 'Backup', title: 'Резервная копия создана', detail: 'Информационное событие сохранено в истории. Аварийный инцидент не нужен.', plan: '', done: 'Резервная копия создана', proof: 'Успешное событие добавлено в историю без запуска исправления.', action: '', failed: '' },
    disk: { source: 'Disk', title: 'Диск заполнен на 93%', detail: 'Свободное место заканчивается. Повторные срабатывания относятся к одному инциденту.', plan: 'Агент подготовил план освобождения места. Изменения — после твоего выбора.', done: 'Место освобождено', proof: 'Отдельный замер: занято 61%. Исходный порог больше не превышен.', action: 'Освобождение места', failed: 'Диск по-прежнему заполнен на 93%. Инцидент не закрыт.' },
    application: { source: 'App', title: 'Ошибка приложения', detail: 'В логе обнаружено исключение. Сохранён контекст, а не только текст ошибки.', plan: 'Агент предлагает исправление. Выбери, как продолжить этот пример.', done: 'Приложение отвечает', proof: 'Независимая проверка подтвердила, что исходная ошибка не повторяется.', action: 'Исправление приложения', failed: 'Ошибка воспроизводится. Проверка оставляет инцидент открытым.' },
    network: { source: 'Network', title: 'Потеря сетевых пакетов', detail: 'Внешняя проверка передала деградацию соединения. Сигнал принят центром.', plan: 'Есть план восстановления маршрута. Решение принимает человек.', done: 'Соединение стабильно', proof: 'Повторная независимая проверка не обнаружила потерь пакетов.', action: 'Восстановление маршрута', failed: 'Потери сохраняются. Выполнена проверка, но не восстановление.' },
    other: { source: 'Service', title: 'Новое внешнее событие', detail: 'Сервис передал событие через API. Notice Place сохранил контекст и источник.', plan: 'Для этого примера настроен агентский сценарий. Выбери действие.', done: 'Сигнал восстановился', proof: 'Отдельная проверка подтвердила восстановление источника.', action: 'Выполнение выбранного плана', failed: 'Источник пока сообщает о проблеме. Инцидент остаётся открытым.' }
  };
  const initialEvents = [
    { source: 'Site', message: 'Проверка успешна', time: '19:24', alert: false },
    { source: 'API', message: 'Восстановлен', time: '18:03', alert: false },
    { source: 'Database', message: 'Задержка в норме', time: '17:11', alert: false },
    { source: 'Backup', message: 'Копия создана', time: '16:02', alert: false }
  ];
  let events = initialEvents.map(event => ({ ...event }));
  let state = 'idle';
  let activeSource = 'api';
  let generation = 0;
  let timers = [];
  let animationUserPaused = false;
  let flow = null;
  let toastTimer;
  const bars = Array.from({ length: 62 }, (_, i) => {
    const bar = document.createElement('i');
    bar.style.opacity = String(0.37 + ((i * 13) % 11) / 44);
    $('#signal-strip').append(bar);
    return bar;
  });

  function cancelSequence() {
    generation += 1;
    timers.forEach(clearTimeout);
    timers = [];
  }
  function later(callback, delay) {
    const ownGeneration = generation;
    timers.push(setTimeout(() => { if (ownGeneration === generation) callback(); }, delay));
  }
  function announce(message) { $('#demo-announcement').textContent = message; }
  function addEvent(source, message, alert = false) {
    const time = new Intl.DateTimeFormat('ru', { hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date());
    events.unshift({ source, message, time, alert });
    events = events.slice(0, 40);
    renderEvents();
  }
  function renderEvents() {
    const list = $('#event-list');
    list.replaceChildren();
    for (const event of events.slice(0, 4)) {
      const li = document.createElement('li');
      const dot = document.createElement('span');
      dot.className = `event-dot${event.alert ? ' alert' : ''}`;
      dot.setAttribute('aria-hidden', 'true');
      const source = document.createElement('span'); source.textContent = event.source;
      const message = document.createElement('span'); message.textContent = event.message;
      const time = document.createElement('time'); time.textContent = event.time;
      li.append(dot, source, message, time); list.append(li);
    }
  }
  function updateBars(nextState) {
    const isIncident = !['idle', 'resolved'].includes(nextState);
    bars.forEach((bar, i) => {
      const hotspot = i > 35 && i < 51;
      bar.style.height = `${isIncident && hotspot ? 7 + (i * 7 % 13) : 9}px`;
      bar.style.backgroundColor = isIncident && hotspot ? 'var(--orange)' : 'var(--silver)';
      bar.style.opacity = String(isIncident && hotspot ? 0.45 + (i % 4) / 7 : 0.28 + ((i * 13) % 11) / 65);
    });
  }
  function setState(nextState, options = {}) {
    state = nextState;
    document.body.dataset.state = nextState;
    const incident = !['idle', 'resolved'].includes(state);
    const scenario = scenarios[activeSource];
    $('#outcome-title').textContent = options.title || 'Инцидентов нет';
    $('#outcome-description').textContent = options.description || 'Система работает стабильно.';
    $('#outcome-icon use').setAttribute('href', `#i-${icons[nextState] || 'crosshair'}`);
    $('#metric-services').textContent = incident ? '23/24' : '24/24';
    $('#metric-incidents').textContent = incident ? '1' : '0';
    $('#metric-notifications').textContent = incident ? (state === 'ready' ? 'Ждёт решения' : state === 'observed' ? 'Наблюдение' : 'В работе') : 'В норме';
    $('#system-title').textContent = incident ? 'Есть сигнал_' : 'Всё под контролем_';
    $('#demo-progress').hidden = nextState === 'idle';
    $('#plan-actions').hidden = !['ready', 'degraded', 'observed'].includes(nextState);
    $('#demo-receipt').hidden = !options.receipt;
    $('#demo-receipt').textContent = options.receipt || '';
    const steps = { idle: 0, detected: 1, ready: 2, working: 3, verifying: 4, observed: 2, degraded: 2, resolved: 4 };
    const labels = { detected: 'Сигнал принят · 1 инцидент', ready: 'Решение человека', working: 'Агент выполняет план', verifying: 'Независимая проверка', observed: 'Инцидент остаётся открытым', degraded: 'Проверка не пройдена', resolved: 'Результат подтверждён' };
    const step = steps[nextState];
    $('#progress-label').textContent = labels[nextState] || '';
    $('#progress-step').textContent = `0${step} / 04`;
    $('#progress-fill').style.width = `${step * 25}%`;
    $('#core-tagline').textContent = ({ idle: 'СОБИРАЕТ · АНАЛИЗИРУЕТ · УВЕДОМЛЯЕТ', detected: 'СИГНАЛ ПРИНЯТ · СОБИРАЕМ КОНТЕКСТ', ready: 'ПЛАН ГОТОВ · РЕШЕНИЕ ЗА ТОБОЙ', working: 'ПЛАН В РАБОТЕ · СЛЕДИМ ЗА ПРОГРЕССОМ', verifying: 'ПРОВЕРЯЕМ ИСХОДНЫЙ СИГНАЛ', observed: 'НАБЛЮДАЕМ · ИНЦИДЕНТ ОТКРЫТ', degraded: 'СИГНАЛ НЕ ВОССТАНОВИЛСЯ', resolved: 'ПРОВЕРЕНО · РЕЗУЛЬТАТ ДОСТАВЛЕН' })[nextState];
    $('#demo-button-label').textContent = nextState === 'idle' ? 'Попробовать' : 'Повторить демо';
    updateBars(nextState);
    if (nextState !== 'idle') announce(`Демонстрация. ${options.title}. ${options.description || ''}`);
    flow?.setState(nextState, activeSource);
    // Layout may change when the plan controls appear; ResizeObserver updates the wires.
    return scenario;
  }
  function revealOutcome() {
    const panel = $('#outcome-panel');
    const rect = panel.getBoundingClientRect();
    if (rect.top < 0 || rect.bottom > window.innerHeight - 12) {
      panel.scrollIntoView({ behavior: reducedMotion.matches ? 'auto' : 'smooth', block: 'center' });
    }
  }
  function startDemo(source = 'api', reveal = true) {
    cancelSequence();
    activeSource = Object.hasOwn(scenarios, source) ? source : 'api';
    const scenario = scenarios[activeSource];
    $$('.source-row').forEach(row => row.classList.toggle('is-selected', row.dataset.source === activeSource));
    if (activeSource === 'backup') {
      addEvent(scenario.source, 'Копия создана');
      setState('resolved', { title: scenario.done, description: scenario.detail, receipt: scenario.proof });
      $('#progress-label').textContent = 'Информация сохранена';
      if (reveal) revealOutcome();
      return;
    }
    addEvent(scenario.source, 'Сигнал принят', true);
    setState('detected', { title: scenario.title, description: scenario.detail });
    if (reveal) revealOutcome();
    later(() => {
      setState('ready', { title: 'Есть план действий', description: scenario.plan });
      addEvent('Notice Place', 'Ждёт решения', true);
    }, 1600);
  }
  function choosePlan(plan) {
    if (!['ready', 'observed', 'degraded'].includes(state)) return;
    cancelSequence();
    const scenario = scenarios[activeSource];
    if (plan === 'observe') {
      addEvent(scenario.source, 'Наблюдение', true);
      setState('observed', { title: 'Продолжаем наблюдать', description: 'Ты выбрал наблюдение. Изменения не выполняются, инцидент остаётся открытым.', receipt: 'В этой демонстрации действие всегда выбирает человек.' });
      // All three controls remain available, allowing a deliberate change of plan.
      return;
    }
    if (plan === 'verify') {
      $('#outcome-panel').focus({ preventScroll: true });
      addEvent(scenario.source, 'Повторная проверка', true);
      setState('verifying', { title: 'Проверяем источник', description: 'Выполняется отдельная проверка. План исправления не запускался.' });
      later(() => {
        setState('degraded', { title: 'Сбой подтверждён', description: scenario.failed, receipt: 'Проверка не пройдена — инцидент не закрывается.' });
        addEvent(scenario.source, 'Сигнал сохраняется', true);
      }, 1900);
      return;
    }
    if (plan !== 'repair') return;
    $('#outcome-panel').focus({ preventScroll: true });
    addEvent('Agent Herder', 'План запущен', true);
    setState('working', { title: 'План передан агенту', description: `${scenario.action}. GPTAdmin и Agent Herder выполняют настроенный сценарий.` });
    later(() => {
      setState('verifying', { title: 'Проверяем результат', description: 'Агент завершил работу. Теперь отдельно проверяем исходный сигнал.' });
      addEvent(scenario.source, 'Проверка результата', true);
    }, 2400);
    later(() => {
      setState('resolved', { title: scenario.done, description: 'Состояние подтверждено. Инцидент закрыт.', receipt: scenario.proof });
      addEvent(scenario.source, 'Восстановлен');
      $$('.source-row').forEach(row => row.classList.remove('is-selected'));
    }, 4500);
  }
  function resetDemo() {
    cancelSequence();
    events = initialEvents.map(event => ({ ...event }));
    renderEvents();
    $$('.source-row').forEach(row => row.classList.remove('is-selected'));
    setState('idle');
    announce('Демонстрация сброшена. Инцидентов нет.');
  }

  $('#demo-start').addEventListener('click', () => startDemo('api'));
  $('#demo-again').addEventListener('click', () => startDemo('api'));
  $$('.source-row').forEach(row => {
    row.addEventListener('click', () => startDemo(row.dataset.source));
    row.addEventListener('pointerenter', () => flow?.highlight(row.dataset.source));
    row.addEventListener('pointerleave', () => flow?.highlight(null));
    row.addEventListener('focus', () => flow?.highlight(row.dataset.source));
    row.addEventListener('blur', () => flow?.highlight(null));
  });
  $$('[data-plan]').forEach(button => button.addEventListener('click', () => choosePlan(button.dataset.plan)));

  // Local, explicitly labelled demo history. No incident API or production credentials are used.
  const dialog = $('#history-dialog');
  function renderHistory() {
    const list = $('#history-list'); list.replaceChildren();
    for (const event of events) {
      const li = document.createElement('li');
      const time = document.createElement('time'); time.textContent = event.time;
      const source = document.createElement('span'); source.className = 'history-source'; source.textContent = event.source;
      const message = document.createElement('span'); message.className = 'history-message'; message.textContent = event.message;
      li.append(time, source, message); list.append(li);
    }
    $('#history-total').textContent = `Событий: ${events.length} / DEMO`;
  }
  $('#history-open').addEventListener('click', () => { renderHistory(); dialog.showModal(); });
  $('#history-close').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', event => {
    if (event.target !== dialog) return;
    const r = dialog.getBoundingClientRect();
    if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) dialog.close();
  });
  $('#demo-reset').addEventListener('click', () => { resetDemo(); renderHistory(); });

  // Accessible mobile navigation; it never opens an external browser or uses a client-side router.
  const menuToggle = $('#menu-toggle');
  const mobileMenu = $('#mobile-menu');
  function closeMenu(returnFocus = false) {
    mobileMenu.hidden = true; menuToggle.setAttribute('aria-expanded', 'false');
    menuToggle.setAttribute('aria-label', 'Открыть меню');
    $('use', menuToggle).setAttribute('href', '#i-menu');
    if (returnFocus) menuToggle.focus();
  }
  menuToggle.addEventListener('click', () => {
    const isOpen = menuToggle.getAttribute('aria-expanded') === 'true';
    mobileMenu.hidden = isOpen;
    menuToggle.setAttribute('aria-expanded', String(!isOpen));
    menuToggle.setAttribute('aria-label', isOpen ? 'Открыть меню' : 'Закрыть меню');
    $('use', menuToggle).setAttribute('href', isOpen ? '#i-menu' : '#i-close');
  });
  $$('a', mobileMenu).forEach(link => link.addEventListener('click', () => closeMenu()));
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !mobileMenu.hidden) closeMenu(true);
  });
  document.addEventListener('click', event => { if (!mobileMenu.hidden && !event.target.closest('.topbar')) closeMenu(); });
  window.matchMedia('(min-width: 980px)').addEventListener('change', event => { if (event.matches) closeMenu(); });

  const tabs = $$('[role="tab"]');
  function activateTab(selected, focus = false) {
    tabs.forEach(tab => {
      const active = tab === selected;
      tab.setAttribute('aria-selected', String(active)); tab.tabIndex = active ? 0 : -1;
      document.getElementById(tab.getAttribute('aria-controls')).hidden = !active;
    });
    if (focus) selected.focus();
  }
  tabs.forEach((tab, index) => {
    tab.addEventListener('click', () => activateTab(tab));
    tab.addEventListener('keydown', event => {
      let next;
      if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
      else if (event.key === 'ArrowLeft') next = (index - 1 + tabs.length) % tabs.length;
      else if (event.key === 'Home') next = 0;
      else if (event.key === 'End') next = tabs.length - 1;
      else return;
      event.preventDefault(); activateTab(tabs[next], true);
    });
  });
  function showToast(message) {
    clearTimeout(toastTimer);
    const toast = $('#toast'); toast.textContent = message; toast.hidden = false;
    toastTimer = setTimeout(() => { toast.hidden = true; }, 2800);
  }
  async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      try { await navigator.clipboard.writeText(text); return; } catch (_) { /* file:// or browser policy; fall through */ }
    }
    const textarea = document.createElement('textarea');
    textarea.value = text; textarea.setAttribute('readonly', '');
    textarea.style.cssText = 'position:fixed;top:-9999px;left:0;opacity:0';
    document.body.append(textarea); textarea.select();
    try { if (!document.execCommand('copy')) throw new Error('Clipboard unavailable'); }
    finally { textarea.remove(); }
  }
  $$('[data-copy]').forEach(button => button.addEventListener('click', async () => {
    const code = document.getElementById(button.dataset.copy);
    try { await copyText(code.textContent); showToast('Скопировано в буфер обмена'); }
    catch (_) {
      const range = document.createRange(); range.selectNodeContents(code);
      window.getSelection()?.removeAllRanges(); window.getSelection()?.addRange(range);
      showToast('Текст выделен. Нажми Ctrl+C или ⌘C.');
    }
    button.focus({ preventScroll: true });
  }));

  /* Lightweight signal renderer.
   * Static wires are cached on an offscreen canvas; only particles redraw, at <=30 FPS.
   * No animation outside the viewport, in hidden tabs, or when reduced-motion is requested.
   * The canvas follows DOM geometry, so it also connects the vertical mobile layout.
   */
  class SignalFlow {
    constructor(canvas, scene) {
      this.canvas = canvas; this.scene = scene; this.ctx = canvas.getContext('2d');
      this.cache = document.createElement('canvas'); this.cacheCtx = this.cache.getContext('2d');
      this.paths = []; this.mode = 'idle'; this.selected = 'api'; this.hover = null;
      this.visible = true; this.paused = reducedMotion.matches; this.raf = 0; this.resizeRaf = 0;
      this.lastFrame = 0; this.time = 0; this.width = 0; this.height = 0;
      if (!this.ctx || !this.cacheCtx) throw new Error('Canvas 2D unavailable');
      this.resizeObserver = new ResizeObserver(() => this.scheduleMeasure());
      this.resizeObserver.observe(scene); this.resizeObserver.observe($('#outcome-panel')); this.resizeObserver.observe($('#source-list'));
      this.intersectionObserver = new IntersectionObserver(entries => {
        this.visible = entries[0].isIntersecting; this.sync();
      }, { rootMargin: '60px' });
      this.intersectionObserver.observe(scene);
      this.onVisibility = () => this.sync();
      document.addEventListener('visibilitychange', this.onVisibility);
      this.measure();
    }
    scheduleMeasure() {
      if (!this.resizeRaf) this.resizeRaf = requestAnimationFrame(() => { this.resizeRaf = 0; this.measure(); });
    }
    relative(element) {
      const a = element.getBoundingClientRect(), b = this.scene.getBoundingClientRect();
      return { x: a.left - b.left, y: a.top - b.top, w: a.width, h: a.height };
    }
    measure() {
      const bounds = this.scene.getBoundingClientRect();
      this.width = bounds.width; this.height = bounds.height; this.dpr = Math.min(window.devicePixelRatio || 1, 2);
      for (const canvas of [this.canvas, this.cache]) {
        canvas.width = Math.round(this.width * this.dpr); canvas.height = Math.round(this.height * this.dpr);
      }
      this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0); this.cacheCtx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
      const core = this.relative($('#signal-core')), outcome = this.relative($('#outcome-panel')), list = this.relative($('#source-list'));
      const rows = $$('.source-row').filter(row => row.getBoundingClientRect().height > 0);
      this.paths = [];
      const verticalInput = window.innerWidth <= 640;
      const verticalOutput = window.innerWidth < 980;
      const lanes = window.innerWidth > 979 ? 5 : 4;
      rows.forEach((row, i) => {
        const box = this.relative(row);
        for (let lane = 0; lane < lanes; lane++) {
          const endOffset = ((i * lanes + lane) / (rows.length * lanes) - .5) * 45;
          const offset = (lane - (lanes - 1) / 2) * 3;
          let start, end, c1, c2;
          if (verticalInput) {
            start = { x: list.x + list.w * (.12 + .76 * (i + .5) / rows.length) + offset, y: list.y + list.h + 1 };
            end = { x: core.x + core.w / 2 + endOffset, y: core.y - 1 };
            const dy = end.y - start.y;
            c1 = { x: start.x, y: start.y + dy * .45 };
            c2 = { x: end.x, y: end.y - dy * .30 };
          } else {
            start = { x: box.x + box.w, y: box.y + box.h / 2 + offset };
            end = { x: core.x - 1, y: core.y + core.h / 2 + endOffset };
            const dx = end.x - start.x;
            c1 = { x: start.x + dx * .42, y: start.y + (i - rows.length / 2) * 2 };
            c2 = { x: end.x - dx * .38, y: end.y };
          }
          this.addPath(start, c1, c2, end, i * 11 + lane, 'in', row.dataset.source, lane);
        }
      });
      const outputCount = verticalOutput ? 17 : 23;
      for (let i = 0; i < outputCount; i++) {
        const offset = (i / (outputCount - 1) - .5);
        let start, end, c1, c2;
        if (verticalOutput) {
          start = { x: core.x + core.w / 2 + offset * 42, y: core.y + core.h + 1 };
          end = { x: outcome.x + outcome.w / 2 + offset * (outcome.w - 55), y: outcome.y - 1 };
          const dy = end.y - start.y;
          c1 = { x: start.x, y: start.y + dy * .48 };
          c2 = { x: end.x, y: end.y - dy * .43 };
        } else {
          start = { x: core.x + core.w + 1, y: core.y + core.h / 2 + offset * 35 };
          end = { x: outcome.x, y: outcome.y + 30 + i / (outputCount - 1) * (outcome.h - 66) };
          const dx = end.x - start.x;
          c1 = { x: start.x + dx * .57, y: start.y };
          c2 = { x: end.x - dx * .43, y: end.y };
        }
        this.addPath(start, c1, c2, end, i * 7 + 150, 'out', null, i);
      }
      this.paintCache(); this.draw(); this.sync();
    }
    noise(seed) { const x = Math.sin(seed * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x); }
    addPath(a, b, c, d, seed, kind, source, lane) {
      const points = [];
      for (let j = 0; j <= 65; j++) {
        const t = j / 65, u = 1 - t;
        let x = u*u*u*a.x + 3*u*u*t*b.x + 3*u*t*t*c.x + t*t*t*d.x;
        let y = u*u*u*a.y + 3*u*u*t*b.y + 3*u*t*t*c.y + t*t*t*d.y;
        if (kind === 'in') {
          const jitter = (this.noise(seed * 8 + j) - .5) * 4 * Math.sin(t * Math.PI);
          if (window.innerWidth <= 640) x += jitter; else y += jitter;
        }
        points.push({ x, y });
      }
      this.paths.push({ points, seed, kind, source, lane, phase: this.noise(seed + 78), speed: .05 + this.noise(seed + 66) * .085 });
    }
    trace(ctx, points) { ctx.beginPath(); points.forEach((p,i) => i ? ctx.lineTo(p.x,p.y) : ctx.moveTo(p.x,p.y)); }
    paintCache() {
      const ctx = this.cacheCtx;
      ctx.clearRect(0,0,this.width,this.height);
      // Etched circuit fragments and registration marks behind the live signal paths.
      for (let i = 0; i < 70; i++) {
        const x = this.noise(i * 2 + 8) * this.width, y = this.noise(i * 3 + 19) * this.height;
        const length = 14 + this.noise(i + 88) * 90;
        ctx.strokeStyle = `rgba(155,165,168,${.025 + this.noise(i) * .09})`; ctx.lineWidth = .5;
        ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x+length*.6, y); ctx.lineTo(x+length, y-14); ctx.lineTo(x+length+24,y-14); ctx.stroke();
        if (i % 3 === 0) { ctx.fillStyle = 'rgba(164,174,176,.25)'; ctx.fillRect(x-1,y-1,1.5,1.5); }
      }
      for (const path of this.paths) {
        const { kind, lane } = path;
        const isBright = this.hover && path.source === this.hover;
        ctx.strokeStyle = kind === 'out'
          ? `rgba(181,195,200,${.16 + (lane % 5) * .055})`
          : lane === 1 ? 'rgba(157,167,170,.22)' : `rgba(255,83,48,${isBright ? .70 : .30 + (lane % 4) * .10})`;
        ctx.lineWidth = isBright ? .95 : .65;
        this.trace(ctx, path.points); ctx.stroke();
      }
      // A tiny crosshair around the central enclosure, rather than a glowing neon blob.
      const core = this.relative($('#signal-core'));
      [[core.x+core.w/2,core.y-39],[core.x+core.w/2,core.y+core.h+30],[core.x-18,core.y-12],[core.x+core.w+17,core.y+core.h+8]].forEach(([x,y],i) => {
        ctx.strokeStyle = 'rgba(197,210,215,.4)'; ctx.lineWidth = .6;
        ctx.beginPath();ctx.moveTo(x-5,y);ctx.lineTo(x+5,y);ctx.moveTo(x,y-5);ctx.lineTo(x,y+5);ctx.stroke();
        if(i===0){ctx.fillStyle='rgba(218,226,229,.9)';ctx.fillRect(x-1,y-1,2,2);}
      });
    }
    draw() {
      const ctx = this.ctx;
      ctx.clearRect(0,0,this.width,this.height);
      ctx.drawImage(this.cache,0,0,this.width,this.height);
      const urgent = !['idle','resolved'].includes(this.mode);
      for (const path of this.paths) {
        if (path.lane % 2 && path.kind === 'out') continue;
        const speed = path.speed * (urgent && path.kind === 'in' ? 1.35 : 1);
        const progress = (this.time * speed + path.phase) % 1;
        const position = progress * (path.points.length - 1);
        const index = Math.floor(position), frac = position-index;
        const a = path.points[index], b = path.points[Math.min(index+1,path.points.length-1)];
        const x = a.x+(b.x-a.x)*frac, y=a.y+(b.y-a.y)*frac;
        const trail = path.points[Math.max(0,index-3)];
        const active = path.source === this.hover || (urgent && path.source === this.selected);
        const color = path.kind === 'in' ? (active ? '255,129,87' : '255,82,45') : '212,228,237';
        const alpha = path.kind === 'in' ? (active ? .95 : .63) : .57;
        const gradient = ctx.createLinearGradient(trail.x,trail.y,x+.01,y+.01);
        gradient.addColorStop(0,`rgba(${color},0)`);gradient.addColorStop(1,`rgba(${color},${alpha})`);
        ctx.strokeStyle=gradient;ctx.lineWidth=active?1.35:.95;
        ctx.beginPath();ctx.moveTo(trail.x,trail.y);ctx.lineTo(x,y);ctx.stroke();
        ctx.fillStyle=`rgba(${color},${alpha})`;ctx.fillRect(x-.6,y-.6,1.4,1.4);
      }
    }
    tick = timestamp => {
      this.raf = 0;
      if (!this.shouldRun()) return;
      const delta = timestamp - this.lastFrame;
      if (delta >= 1000 / 30) {
        this.time += Math.min(delta, 60) / 1000; this.lastFrame = timestamp; this.draw();
      }
      this.raf = requestAnimationFrame(this.tick);
    };
    shouldRun() { return this.ctx && this.visible && !this.paused && !document.hidden; }
    sync() {
      if (this.shouldRun() && !this.raf) { this.lastFrame = performance.now(); this.raf = requestAnimationFrame(this.tick); }
      else if (!this.shouldRun() && this.raf) { cancelAnimationFrame(this.raf); this.raf = 0; }
    }
    pause(value) { this.paused=value;this.sync();if(value)this.draw(); }
    setState(mode,source) { this.mode=mode;this.selected=source;this.draw(); }
    highlight(source) { this.hover=source;this.paintCache();this.draw(); }
    destroy() { cancelAnimationFrame(this.raf);cancelAnimationFrame(this.resizeRaf);this.resizeObserver?.disconnect();this.intersectionObserver?.disconnect();document.removeEventListener('visibilitychange',this.onVisibility); }
  }
  try { flow = new SignalFlow($('#signal-canvas'), $('#signal-scene')); }
  catch (error) { console.warn('Signal animation unavailable; all page content remains available.', error); }
  function syncAnimationPreference() {
    const paused = animationUserPaused || reducedMotion.matches;
    document.body.classList.toggle('animation-paused', paused);
    flow?.pause(paused);
    const button = $('#animation-toggle');
    button.setAttribute('aria-pressed', String(paused));
    button.setAttribute('aria-label', reducedMotion.matches ? 'Анимация отключена настройками устройства' : paused ? 'Возобновить анимацию' : 'Приостановить анимацию');
    $('use', button).setAttribute('href', paused ? '#i-play' : '#i-pause');
    $('span', button).textContent = reducedMotion.matches ? 'Без анимации' : paused ? 'Продолжить' : 'Пауза анимации';
  }
  $('#animation-toggle').addEventListener('click', () => {
    if (reducedMotion.matches) { showToast('Анимация отключена настройкой «Уменьшение движения».'); return; }
    animationUserPaused = !animationUserPaused; syncAnimationPreference();
  });
  reducedMotion.addEventListener('change', syncAnimationPreference);
  syncAnimationPreference();
  window.addEventListener('pagehide', () => { flow?.pause(true); });
  window.addEventListener('pageshow', syncAnimationPreference);
})();
