'use strict';

/* PLATEAU → Blender Bridge / GUI v2.1（B案: 一問ずつ）
   Python 側はローカルHTTPの POST /api/<名前>。押し込みはせず、poll() が返ってから次を予約する。
   作業に紐づくイベントは job_id で選別し、今の作業以外では画面の状態を変えない。 */

const JOB_KINDS = ['stage', 'result', 'done', 'error', 'cancelled'];
const TOKEN = new URLSearchParams(location.search).get('t') || '';

const state = {
  api: null,
  ready: false,
  closed: false,
  view: 'input',
  step: 1,
  job: null,          /* {id, kind} */
  pending: null,      /* 開始の返事待ちの作業 */
  buffered: [],       /* 開始の返事より先に届いた作業イベント */
  stage: 0,
  result: null,
  scanned: false,
  blenderOk: false,
  advReturn: { view: 'input', step: 1 },
  plan: null,
  planKey: null,
  planPending: false,
  planAbort: null,
  saveKind: 'app',
  inspect: { ok: false, explicit_size_m: null, error: '' },
  placeRequest: null,
  placeTimer: 0,
  scan: { blender: [], nusamai: null },
  meta: {
    places: [], sizes: [], stages: [], modes: [],
    default_size: '400', default_output: '', version: '', cancelled_message: '',
    outputs: {}
  },
  form: {
    mode: 'center', place_name: '', area: '', size: '400', output: '', auto_name: true,
    blender: '', nusamai: '', epsg: 'AUTO', lod: 'textured_max_lod',
    zoom: '18', dem_res: '100', axis: 'auto', clip: true
  }
};

const $ = id => document.getElementById(id);
const SIZE_PX = { '200': 40, '400': 62, '800': 86 };

/* ── 起動 ─────────────────────────────────────────────────────── */

if (document.readyState === 'loading') {
  window.addEventListener('DOMContentLoaded', start, { once: true });
} else {
  start();
}
window.addEventListener('beforeunload', () => { state.closed = true; });
/* 窓を閉じる・再読込する時の合図。Python 側は3秒待って連絡が戻らなければ終わる。 */
window.addEventListener('pagehide', () => {
  try {
    fetch('/api/_bye', { method: 'POST', keepalive: true, headers: { 'X-Bridge-Token': TOKEN } });
  } catch (error) { /* 閉じる途中なので何もしない */ }
});

function makeApi() {
  return new Proxy({}, {
    get(_target, name) {
      if (typeof name !== 'string') return undefined;
      const timeout = name === 'choose_output' || name === 'choose_file' || name === 'save_log'
        ? 0 : (name === 'poll' ? 5000 : 20000);
      return (...args) => callApi(name, args, { timeout });
    }
  });
}

// 何度呼んでも結果が変わらないAPI。通信が切れた時だけ1回やり直す。
// AdGuard などのフィルタが 127.0.0.1 を中継すると、接続の使い回しで時々失敗するため。
// 生成の開始・中止・ダイアログ・外部を開く操作は、二重に走ると困るので入れない。
const RETRYABLE = new Set([
  'initial_state', 'plan', 'poll', 'scan', 'check_place', 'inspect_area', 'range_preview',
  'use_output_folder', 'blender_status', 'log_text'
]);

async function callApi(name, args, options = {}) {
  try {
    return await callApiOnce(name, args, options);
  } catch (error) {
    if (!(error instanceof TypeError) || !RETRYABLE.has(name)
        || (options.signal && options.signal.aborted)) throw error;
    await new Promise(resolve => setTimeout(resolve, 150));
    return await callApiOnce(name, args, options);
  }
}

async function callApiOnce(name, args, options = {}) {
  const controller = options.signal ? null : new AbortController();
  const signal = options.signal || (controller && controller.signal);
  const timeout = options.timeout === undefined ? 20000 : options.timeout;
  const timer = controller && timeout > 0 ? setTimeout(() => controller.abort(), timeout) : null;
  let response;
  try {
    response = await fetch('/api/' + name, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Bridge-Token': TOKEN },
      body: JSON.stringify({ args }),
      signal
    });
  } catch (error) {
    if (error && error.name === 'AbortError') {
      throw new Error('通信がタイムアウトしました。ネットワークの状態を確認して、もう一度お試しください');
    }
    throw error;
  } finally {
    if (timer) clearTimeout(timer);
  }
  let payload = null;
  try { payload = await response.json(); } catch (error) { payload = null; }
  if (payload && payload.ok) return payload.result;
  throw new Error((payload && payload.error) || ('HTTP ' + response.status));
}

async function loadInitialState() {
  let last = null;
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      return await state.api.initial_state();
    } catch (error) {
      last = error;
      await new Promise(resolve => setTimeout(resolve, 300));
    }
  }
  throw last;
}

async function start() {
  if (state.ready) return;
  state.ready = true;
  state.api = makeApi();
  try {
    const meta = await loadInitialState();
    if (meta && typeof meta === 'object') state.meta = Object.assign(state.meta, meta);
    if (state.meta.title) document.title = state.meta.title;
  } catch (error) {
    console.error('initial_state failed', error);
  }
  state.form.output = state.meta.default_output || '';
  paintSaveLocation(state.meta.default_kind || 'documents');
  state.form.size = state.meta.default_size || '400';
  $('foot-version').textContent = state.meta.version ? 'v' + state.meta.version : '';
  renderPlaces();
  renderSizes();
  applyModeLabels();
  syncAdvanced();
  showStep(1);
  showView('input');
  pollLoop();
  state.api.scan();
}

/* ── 取り込み ─────────────────────────────────────────────────── */

async function pollLoop() {
  if (!state.api || state.closed) return;
  try {
    const events = await state.api.poll();
    (events || []).forEach(handle);
  } catch (error) {
    /* 窓を閉じた後の poll は黙って止める */
  }
  if (!state.closed) setTimeout(pollLoop, 150);
}

function describe(data) {
  if (data == null) return '';
  if (typeof data === 'string') return data;
  if (typeof data === 'object') return String(data.message || data.path || data.text || '');
  return String(data);
}

function handle(event) {
  if (!event || !event.kind) return;
  if (JOB_KINDS.indexOf(event.kind) !== -1) {
    if (!state.job) {
      if (state.pending) { state.buffered.push(event); return; }
      appendLog('[' + event.kind.toUpperCase() + '] ' + describe(event.data));
      return;
    }
    if (event.job_id && event.job_id !== state.job.id) {
      /* 前の作業の取りこぼし。画面は変えず、ログにだけ残す */
      appendLog('[' + event.kind.toUpperCase() + '] ' + describe(event.data));
      return;
    }
  }
  switch (event.kind) {
    case 'log': appendLog(String(event.data == null ? '' : event.data)); break;
    case 'stage': updateStage(event.data || {}); break;
    case 'scan': onScan(event.data || {}); break;
    case 'place': onPlace(event.data || {}); break;
    case 'result': state.result = String(event.data == null ? '' : event.data); break;
    case 'smoke_result': appendLog('自己診断ファイル: ' + describe(event.data)); break;
    case 'done': onDone(event.data); break;
    case 'error': onError(event.data); break;
    case 'cancelled': onCancelled(String(event.data == null ? '' : event.data)); break;
  }
}

async function beginJob(kind, call) {
  if (state.job || state.pending) return { started: false, response: null };
  state.pending = kind;
  state.buffered = [];
  updateRunButtons();
  let response;
  try {
    response = await call();
  } catch (error) {
    state.pending = null;
    state.buffered = [];
    updateRunButtons();
    throw error;
  }
  state.pending = null;
  if (!response || response === false || response.started === false) {
    state.buffered = [];
    updateRunButtons();
    return { started: false, response: response || null };
  }
  state.job = { id: response.job || null, kind };
  const buffered = state.buffered;
  state.buffered = [];
  buffered.forEach(handle);
  updateRunButtons();
  return { started: true, response };
}

function finishJob() {
  state.job = null;
  updateRunButtons();
}

function updateRunButtons() {
  const busy = Boolean(state.job || state.pending);
  ['adv-preview', 'adv-diagnose', 'adv-smoke'].forEach(id => { $(id).disabled = busy; });
  const planReady = state.step !== 3 ||
    (Boolean(state.plan) && state.planKey === formKey() && !state.planPending);
  $('btn-next').disabled = busy || !planReady;
}

/* ── 入力: 場所 ───────────────────────────────────────────────── */

function renderPlaces() {
  const select = $('place-select');
  select.innerHTML = '';
  const first = document.createElement('option');
  first.value = '';
  first.textContent = '有名な場所から選ぶ';
  select.appendChild(first);
  (state.meta.places || []).forEach(place => {
    const option = document.createElement('option');
    option.value = place.name;
    option.textContent = `${place.name}（${place.size}m）`;
    select.appendChild(option);
  });
  const current = state.form.place_name || '';
  select.value = (state.meta.places || []).some(place => place.name === current) ? current : '';
}

async function onPlaceSelect() {
  const name = $('place-select').value;
  const place = (state.meta.places || []).find(item => item.name === name);
  if (!place) return;
  state.form.mode = 'center';
  state.form.place_name = place.name;
  state.form.area = place.coordinates;
  state.form.size = place.size || state.meta.default_size || '400';
  $('area-input').value = place.coordinates;
  setModeButtons('center');
  applyModeLabels();
  hidePlaceError();
  clearTimeout(state.placeTimer);
  state.inspect = await inspectNow();
  renderSizes();
  await checkPlace();
}

async function inspectNow() {
  if (!state.api) return { ok: false, explicit_size_m: null, error: '' };
  try {
    const info = await state.api.inspect_area(state.form);
    return info && typeof info === 'object'
      ? Object.assign({ ok: false, explicit_size_m: null, error: '' }, info)
      : { ok: false, explicit_size_m: null, error: '' };
  } catch (error) {
    return { ok: false, explicit_size_m: null, error: '入力内容を確認できませんでした: ' + error };
  }
}

function localExplicit() {
  if (state.form.mode !== 'center') return null;
  const text = String(state.form.area || '').normalize('NFKC').trim().replace(/^r:/, '');
  if (/@-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?/.test(text)) return null;
  if (/[NSEW]/i.test(text)) return null;
  const numbers = text.match(/-?\d+(?:\.\d+)?/g);
  if (!numbers || numbers.length !== 3) return null;
  const value = Number(numbers[2]);
  return Number.isFinite(value) ? value : null;
}

function explicitSize() {
  const fromInspect = state.inspect && state.inspect.explicit_size_m;
  if (fromInspect != null) {
    const value = Number(fromInspect);
    if (Number.isFinite(value)) return value;
  }
  return localExplicit();
}

function onAreaInput() {
  state.form.area = $('area-input').value;
  state.form.place_name = '';
  state.manualSize = false;
  if ($('place-select').value) $('place-select').value = '';
  hidePlaceInfo();
  hidePlaceError();
  if (state.form.mode === 'center' && localExplicit() !== null) state.form.size = 'input';
  else if (state.form.size === 'input') state.form.size = state.meta.default_size || '400';
  renderSizes();
  scheduleInspectCheck();
}

function scheduleInspectCheck() {
  clearTimeout(state.placeTimer);
  state.placeTimer = setTimeout(runInspectCheck, 700);
}

async function runInspectCheck() {
  const info = await inspectNow();
  state.inspect = info;
  if (state.form.mode === 'center') {
    if (explicitSize() !== null) {
      if (!state.manualSize) state.form.size = 'input';
    } else if (state.form.size === 'input') {
      state.form.size = state.meta.default_size || '400';
    }
  }
  renderSizes();
  if (!state.form.area.trim()) { hidePlaceInfo(); return; }
  if (!info.ok) { hidePlaceInfo(); return; }
  await checkPlace();
}

function setModeButtons(mode) {
  state.form.mode = mode;
  document.querySelectorAll('#modes button').forEach(button => {
    button.setAttribute('aria-checked', String(button.dataset.mode === mode));
  });
}

function setMode(mode) {
  setModeButtons(mode);
  applyModeLabels();
  state.form.area = '';
  state.form.place_name = '';
  state.form.size = state.meta.default_size || '400';
  state.manualSize = false;
  $('area-input').value = '';
  $('more-modes').open = true;
  if ($('place-select').value) $('place-select').value = '';
  hidePlaceInfo();
  hidePlaceError();
  renderSizes();
}

function applyModeLabels() {
  const found = (state.meta.modes || []).find(mode => mode.value === state.form.mode);
  $('area-label').textContent = found ? found.label : '';
  $('area-hint').textContent = found ? found.hint : '';
}

function hidePlaceInfo() {
  const info = $('place-info');
  info.hidden = true;
  info.className = 'result';
  info.textContent = '';
}

function showPlaceResult(data) {
  const info = $('place-info');
  const status = data.status || (data.ok ? 'ok' : 'nodata');
  info.hidden = false;
  info.className = 'result ' + (status === 'ok' ? 'ok'
    : status === 'unknown' ? 'unknown' : 'bad');
  info.textContent = String(data.text || '').replace(/^[✓✗]\s*/, '');
}

function setCheckBusy(busy) {
  $('check-place').disabled = Boolean(busy);
}

function showPlaceError(text) {
  $('place-error').hidden = false;
  $('place-error').textContent = text;
}

function hidePlaceError() {
  $('place-error').hidden = true;
  $('place-error').textContent = '';
}

function sameRequest(a, b) {
  if (a == null || b == null) return true;
  return String(a) === String(b);
}

async function checkPlace() {
  if (!state.form.area.trim()) {
    showPlaceError('場所を選ぶか、座標を入れてください');
    $('area-input').focus();
    return;
  }
  hidePlaceError();
  const id = Number(state.placeRequest || 0) + 1;
  state.placeRequest = id;
  const info = $('place-info');
  info.hidden = false;
  info.className = 'result';
  info.textContent = 'PLATEAUのデータを探しています…';
  setCheckBusy(true);
  let reply = null;
  try {
    reply = await state.api.check_place(state.form, id);
  } catch (error) {
    if (!sameRequest(id, state.placeRequest)) return;
    showPlaceResult({ status: 'unknown', text: '確かめられませんでした。通信を確かめて、もう一度押してください' });
    setCheckBusy(false);
    return;
  }
  if (reply && reply.started === false) {
    if (!sameRequest(id, state.placeRequest)) return;
    if (reply.text) showPlaceResult({ status: 'unknown', text: reply.text });
    setCheckBusy(false);
  }
}

function onPlace(data) {
  const id = data && data.request_id;
  if (id != null && !sameRequest(id, state.placeRequest)) return;
  setCheckBusy(false);
  showPlaceResult(data || {});
}

function centerPoint() {
  if (state.form.mode !== 'center') return null;
  const text = String(state.form.area || '').normalize('NFKC');
  const numbers = text.match(/-?\d+(?:\.\d+)?/g);
  if (!numbers || numbers.length < 2) return null;
  let lat = Number(numbers[0]);
  let lon = Number(numbers[1]);
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
  if (lon >= 20 && lon <= 46 && lat >= 122 && lat <= 154) {
    const swap = lat; lat = lon; lon = swap;
  }
  return [lat, lon];
}

function openMaps() {
  const point = centerPoint();
  if (point) state.api.open_url('maps', point[0], point[1]);
  else state.api.open_url('maps');
}

/* ── 入力: 広さ ───────────────────────────────────────────────── */

function renderSizes() {
  const wrap = $('sizes');
  wrap.innerHTML = '';
  const explicit = state.form.mode === 'center' ? explicitSize() : null;
  if (state.form.mode === 'center' && explicit === null && state.form.size === 'input') {
    state.form.size = state.meta.default_size || '400';
  }
  wrap.classList.toggle('four', explicit !== null);
  (state.meta.sizes || []).forEach(size => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'pick';
    button.dataset.size = size.value;
    button.setAttribute('role', 'radio');
    const square = document.createElement('div');
    square.className = 'sq';
    const px = SIZE_PX[size.value] || 56;
    square.style.width = px + 'px';
    square.style.height = px + 'px';
    const title = document.createElement('b');
    title.textContent = size.title;
    const detail = document.createElement('small');
    detail.textContent = size.detail;
    button.append(square, title, detail);
    button.addEventListener('click', () => {
      state.form.size = size.value;
      state.manualSize = true;
      paintSizeSelection();
    });
    wrap.appendChild(button);
  });
  if (explicit !== null) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'pick';
    button.dataset.size = 'input';
    button.setAttribute('role', 'radio');
    const square = document.createElement('div');
    square.className = 'sq free';
    square.style.width = '62px';
    square.style.height = '62px';
    const title = document.createElement('b');
    title.textContent = `入力した広さ（${explicit}m）`;
    const detail = document.createElement('small');
    detail.textContent = '入力欄の指定';
    button.append(square, title, detail);
    button.addEventListener('click', () => {
      state.form.size = 'input';
      state.manualSize = true;
      paintSizeSelection();
    });
    wrap.appendChild(button);
  }
  paintSizeSelection();
}

function paintSizeSelection() {
  document.querySelectorAll('#sizes .pick').forEach(button => {
    const selected = button.dataset.size === state.form.size;
    button.classList.toggle('sel', selected);
    button.setAttribute('aria-checked', String(selected));
  });
  refreshRangePreview();
}

function updateSizeView() {
  const center = state.form.mode === 'center';
  $('size-question').hidden = !center;
  $('sizes').hidden = !center;
  $('size-fixed').hidden = center;
  $('size-lead').hidden = !center;
}

/* ── 入力: 保存先とまとめ ─────────────────────────────────────── */

let planSeq = 0;

function formKey() {
  return JSON.stringify([
    state.form.mode, state.form.place_name, state.form.area, state.form.size,
    state.form.output, Boolean(state.form.auto_name)
  ]);
}

function currentPlanPath() {
  if (!state.plan || state.planKey !== formKey()) return null;
  return state.plan.output || null;
}

async function refreshPlan() {
  if (state.planAbort) state.planAbort.abort();
  const controller = new AbortController();
  state.planAbort = controller;
  const timer = setTimeout(() => controller.abort(), 10000);
  const seq = ++planSeq;
  const key = formKey();
  state.planPending = true;
  state.plan = null;
  state.planKey = null;
  $('retry-plan').hidden = true;
  updateRunButtons();
  let plan;
  try {
    plan = await callApi('plan', [state.form], { signal: controller.signal });
  } catch (error) {
    clearTimeout(timer);
    if (seq !== planSeq || key !== formKey()) return;
    state.planPending = false;
    state.planAbort = null;
    updateRunButtons();
    const text = error && error.name === 'AbortError'
      ? '保存先の確認がタイムアウトしました' : String(error);
    showStartError('保存先を確認できませんでした: ' + text);
    $('retry-plan').hidden = false;
    return;
  }
  clearTimeout(timer);
  if (seq !== planSeq || key !== formKey()) return;
  state.planPending = false;
  state.planAbort = null;
  if (!plan || plan.error) {
    updateRunButtons();
    showStartError((plan && plan.error) || '保存先を確認できませんでした');
    $('retry-plan').hidden = false;
    return;
  }
  hideStartError();
  $('retry-plan').hidden = true;
  state.plan = plan;
  state.planKey = key;
  $('output-name').textContent = plan.output_name || '—';
  $('output-name').title = plan.output || '';
  $('output-folder').textContent = plan.output_folder || '';
  $('output-folder').title = plan.output_folder || '';
  $('summary-place').textContent = plan.place || '—';
  $('summary-place').title = plan.place || '';
  $('summary-area').textContent = plan.area || '—';
  $('summary-area').title = plan.area || '';
  $('rename-note').hidden = !(plan.renamed || plan.note);
  $('rename-note').textContent = plan.rename_note || plan.note || '';
  updateRunButtons();
}

function showStartError(text) {
  $('start-error').hidden = false;
  $('start-error').textContent = text;
}

function hideStartError() {
  $('start-error').hidden = true;
  $('start-error').textContent = '';
}

function showRunError(text) {
  $('run-error').hidden = false;
  $('run-error').textContent = text;
}

function hideRunError() {
  $('run-error').hidden = true;
  $('run-error').textContent = '';
}

function paintSaveLocation(kind) {
  state.saveKind = kind;
  document.querySelectorAll('#saveloc button').forEach(button => {
    button.setAttribute('aria-checked', String(button.dataset.kind === kind));
  });
}

async function chooseOutput() {
  const suggested = currentPlanPath() || state.form.output;
  const picked = await state.api.choose_output(suggested);
  if (!picked) return;
  state.form.output = String(picked);
  state.form.auto_name = false;
  paintSaveLocation('other');
  syncAdvanced();
  await refreshPlan();
}

async function chooseSaveLocation(kind) {
  if (kind === 'other') {
    await chooseOutput();
    return;
  }
  let result = null;
  try {
    result = await state.api.use_output_folder(kind, state.form.output);
  } catch (error) {
    showStartError('保存場所を切り替えられませんでした: ' + error);
    return;
  }
  if (!result) return;
  if (result.error) showStartError(result.error);
  else hideStartError();
  if (result.output) {
    state.form.output = String(result.output);
    syncAdvanced();
  }
  paintSaveLocation(result.kind || kind);
  await refreshPlan();
}

/* ── 手順の切り替え ───────────────────────────────────────────── */

function showStep(step) {
  state.step = Math.min(3, Math.max(1, Number(step) || 1));
  document.querySelectorAll('#view-input .wz').forEach(wz => {
    wz.hidden = Number(wz.dataset.wz) !== state.step;
  });
  document.querySelectorAll('#stepper button').forEach(button => {
    const number = Number(button.dataset.step);
    button.classList.toggle('on', number === state.step);
    button.classList.toggle('past', number < state.step);
    button.dataset.ahead = String(number > state.step);
    button.disabled = number > state.step;
  });
  $('next-label').textContent = state.step === 3 ? '3Dの街を作る' : '次へ';
  $('btn-back').disabled = state.step === 1;
  updateSizeView();
  paintSizeSelection();
  syncRangePreview();
  updateBanner();
  updateRunButtons();
  if (state.step === 3) refreshPlan();
}

async function goStep(step) {
  if (step === 2) {
    state.inspect = await inspectNow();
    if (state.form.mode === 'center' && explicitSize() !== null && !state.manualSize) {
      state.form.size = 'input';
    }
  }
  showStep(step);
  renderSizes();
}

async function onNext() {
  if (state.view !== 'input' || state.job || state.pending) return;
  if (state.step === 1) {
    const info = await inspectNow();
    if (!info.ok) {
      showPlaceError(info.error || '場所を選ぶか、座標を入れてください');
      $('area-input').focus();
      return;
    }
    state.inspect = info;
    hidePlaceError();
    await goStep(2);
    return;
  }
  if (state.step === 2) {
    await goStep(3);
    return;
  }
  await startGeneration();
}

async function startGeneration() {
  if (state.job || state.pending) return;
  const key = formKey();
  if (!state.plan || state.planKey !== key || state.planPending) {
    // 保存先が未確認のまま生成を始めない。確認だけして戻る。
    await refreshPlan();
    return;
  }
  const plan = state.plan;
  $('btn-next').disabled = true;
  hideStartError();
  hideRunError();
  state.result = null;
  let started;
  try {
    started = await beginJob('generate', () => state.api.start_generate(state.form));
  } catch (error) {
    updateRunButtons();
    showStartError('作成を始められませんでした: ' + error);
    return;
  }
  if (!started || !started.started) {
    updateRunButtons();
    const response = started && started.response;
    if (response && response.error) showStartError(response.error);
    return;
  }
  if (!state.job) { updateRunButtons(); return; }
  state.stage = 1;
  $('btn-cancel').disabled = false;
  updateStage({ stage: 1, text: (state.meta.stages[0] || {}).text || '' });
  $('run-summary').textContent =
    `${plan.place || 'この場所'}・${plan.area || ''} を作っています。数分かかることがあります。`;
  showView('run');
}

/* ── 表示の切り替え ───────────────────────────────────────────── */

function showView(view) {
  state.view = view;
  const views = {
    input: 'view-input', run: 'view-run', done: 'view-done',
    cancelled: 'view-cancelled', failed: 'view-failed', advanced: 'view-advanced'
  };
  Object.entries(views).forEach(([name, id]) => { $(id).hidden = name !== view; });
  $('stepper').hidden = view !== 'input';
  $('nav').hidden = view !== 'input';
  syncRangePreview();
  updateBanner();
}

async function updateBanner() {
  const path = String(state.form.blender || '').trim();
  let ok = false;
  if (path && state.api) {
    try {
      const status = await state.api.blender_status(path);
      ok = Boolean(status && status.ok);
    } catch (error) {
      ok = false;
    }
  }
  state.blenderOk = ok;
  const visible = !ok && state.scanned && state.view === 'input'
    && (state.step === 1 || state.step === 3);
  $('blender-banner').hidden = !visible;
}

function updateStage(data) {
  state.stage = Number(data.stage) || 1;
  $('ring-label').textContent = `${state.stage}/4`;
  $('ring-progress').setAttribute('stroke-dasharray', `${state.stage * 25} 100`);
  const text = String(data.text || '');
  $('stage-text').textContent = text ? text + '…' : '';
}

function appendLog(text) {
  const log = $('adv-log');
  const nearBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 24;
  log.value = log.value ? log.value + '\n' + text : text;
  if (log.value.length > 200000) log.value = log.value.slice(-150000);
  if (nearBottom) log.scrollTop = log.scrollHeight;
}

/* ── イベント ─────────────────────────────────────────────────── */

async function onScan(data) {
  state.scanned = true;
  state.scan.blender = Array.isArray(data.blender) ? data.blender : [];
  state.scan.nusamai = data.nusamai || null;
  const path = String(state.form.blender || '').trim();
  let ok = false;
  if (path) {
    try {
      const status = await state.api.blender_status(path);
      ok = Boolean(status && status.ok);
    } catch (error) {
      ok = false;
    }
  }
  /* 欄が空か、実在しないファイルの時だけ、見つかった最初の Blender で置き換える */
  if (!ok && state.scan.blender.length) state.form.blender = state.scan.blender[0];
  if (!state.form.nusamai && state.scan.nusamai) state.form.nusamai = state.scan.nusamai;
  syncAdvanced();
  await updateBanner();
  appendLog(`Blender: ${state.scan.blender[0] || '見つかりません'} / nusamai: ${state.scan.nusamai || '見つかりません'}`);
}

function onDone(data) {
  const message = typeof data === 'string' ? data : String((data && data.message) || '');
  const output = data && typeof data === 'object' ? data.output : null;
  const job = state.job;
  finishJob();
  if (job && job.kind === 'generate') {
    const saved = output || state.result;
    if (!saved) { appendLog('[DONE] ' + message); return; }
    state.result = saved;
    $('done-name').textContent = fileName(saved);
    $('done-name').title = saved;
    $('done-folder').textContent = folderName(saved);
    $('done-folder').title = folderName(saved);
    showOpenResultError('');
    showView('done');
    return;
  }
  appendLog('[DONE] ' + message);
}

function showOpenResultError(message) {
  const error = $('open-result-error');
  error.hidden = !message;
  error.textContent = message;
}

async function openResult() {
  const button = $('btn-open-result');
  if (button.getAttribute('aria-disabled') === 'true') return;
  const saved = state.result;
  const fallback = '保存した .blend を開けませんでした。「保存したフォルダを開く」からファイルを確認してください。';
  button.setAttribute('aria-disabled', 'true');
  showOpenResultError('');
  let message = '';
  try {
    const result = await state.api.open_result();
    if (!result?.ok) message = result?.error || fallback;
  } catch (error) {
    appendLog('.blend を開く要求の結果を確認できませんでした: ' + error);
    message = fallback;
  } finally {
    button.removeAttribute('aria-disabled');
  }
  if (state.result === saved) showOpenResultError(message);
}

function onCancelled(message) {
  const job = state.job;
  finishJob();
  hideRunError();
  if (job && job.kind === 'generate') {
    $('cancelled-text').textContent = message || state.meta.cancelled_message || '';
    showView('cancelled');
    return;
  }
  appendLog('[CANCELLED] ' + message);
}

function onError(data) {
  const message = typeof data === 'string' ? data
    : String((data && data.message) || '');
  const recovery = data && typeof data === 'object' ? String(data.recovery || '') : '';
  const job = state.job;
  finishJob();
  if (job && job.kind === 'generate') {
    paintRecovery(recovery);
    if (recovery === 'place') {
      $('failed-title').textContent = message;
      $('failed-text').textContent = '';
    } else {
      $('failed-title').textContent = '作れませんでした';
      $('failed-text').textContent = message;
    }
    showView('failed');
    $('failed-title').focus();
    return;
  }
  appendLog('[ERROR] ' + message);
}

function paintRecovery(recovery) {
  const button = $('btn-retry-failed');
  const text = recovery === 'place' ? '場所を変える'
    : recovery === 'output' ? '保存先を変える' : 'もう一度作る';
  button.textContent = text;
  button.dataset.recovery = recovery === 'place' || recovery === 'output' ? recovery : '';
}

function fileName(path) {
  return String(path || '').split(/[\\/]/).pop() || String(path || '');
}

function folderName(path) {
  const text = String(path || '');
  const cut = Math.max(text.lastIndexOf('\\'), text.lastIndexOf('/'));
  return cut > 0 ? text.slice(0, cut) : '';
}

/* ── 詳細設定 ─────────────────────────────────────────────────── */

function openAdvanced() {
  state.advReturn = { view: state.view, step: state.step };
  syncAdvanced();
  showView('advanced');
  const log = $('adv-log');
  log.scrollTop = log.scrollHeight;
}

function closeAdvanced() {
  const back = state.advReturn || { view: 'input', step: 1 };
  if (back.view === 'input') {
    showView('input');
    showStep(back.step);
  } else {
    showView(back.view);
  }
}

function syncAdvanced() {
  $('adv-output').value = currentPlanPath() || state.form.output || '';
  $('adv-blender').value = state.form.blender || '';
  $('adv-nusamai').value = state.form.nusamai || '';
  $('adv-epsg').value = state.form.epsg || 'AUTO';
  $('adv-lod').value = state.form.lod || 'textured_max_lod';
  $('adv-zoom').value = String(state.form.zoom || '18');
  $('adv-dem').value = String(state.form.dem_res || '100');
  $('adv-axis').value = state.form.axis || 'auto';
  $('adv-clip').checked = Boolean(state.form.clip);
}

async function runAdvancedJob(kind, call) {
  if (state.job || state.pending) return;
  appendLog(kind === 'preview' ? '取得対象を確認しています…' : 'Blender自己診断を始めます…');
  try {
    await beginJob(kind, call);
  } catch (error) {
    appendLog('始められませんでした: ' + error);
  }
}

function bindAdvanced() {
  $('adv-output').addEventListener('input', event => {
    state.form.output = event.target.value;
    state.form.auto_name = false;
  });
  $('adv-blender').addEventListener('input', event => {
    state.form.blender = event.target.value;
    updateBanner();
  });
  $('adv-nusamai').addEventListener('input', event => { state.form.nusamai = event.target.value; });
  $('adv-epsg').addEventListener('input', event => { state.form.epsg = event.target.value; });
  $('adv-lod').addEventListener('change', event => { state.form.lod = event.target.value; });
  $('adv-zoom').addEventListener('change', event => { state.form.zoom = event.target.value; });
  $('adv-dem').addEventListener('change', event => { state.form.dem_res = event.target.value; });
  $('adv-axis').addEventListener('change', event => { state.form.axis = event.target.value; });
  $('adv-clip').addEventListener('change', event => { state.form.clip = event.target.checked; });

  $('adv-output-browse').addEventListener('click', async () => {
    const suggested = currentPlanPath() || state.form.output;
    const picked = await state.api.choose_output(suggested);
    if (!picked) return;
    state.form.output = String(picked);
    state.form.auto_name = false;
    syncAdvanced();
  });
  $('adv-blender-browse').addEventListener('click', async () => {
    const picked = await state.api.choose_file('blender');
    if (!picked) return;
    state.form.blender = String(picked);
    syncAdvanced();
    updateBanner();
  });
  $('adv-nusamai-browse').addEventListener('click', async () => {
    const picked = await state.api.choose_file('nusamai');
    if (!picked) return;
    state.form.nusamai = String(picked);
    syncAdvanced();
  });
  $('adv-rescan').addEventListener('click', () => state.api.scan());

  $('adv-preview').addEventListener('click', () => {
    runAdvancedJob('preview', () => state.api.preview_sources(state.form));
  });
  $('adv-diagnose').addEventListener('click', () => {
    if (state.job || state.pending) return;
    state.api.diagnose(state.form);
  });
  $('adv-smoke').addEventListener('click', () => {
    runAdvancedJob('smoke', () => state.api.smoke_test(state.form));
  });
  $('adv-manual').addEventListener('click', () => state.api.open_manual());
  $('adv-save-log').addEventListener('click', saveLog);
  $('adv-copy-log').addEventListener('click', copyLog);
}

async function saveLog() {
  const saved = await state.api.save_log(null);
  if (saved) appendLog('ログを保存しました: ' + saved);
  else appendLog('ログの保存を取りやめました。');
}

async function copyLog() {
  let text = '';
  try {
    text = await state.api.log_text();
  } catch (error) {
    text = $('adv-log').value;
  }
  try {
    await navigator.clipboard.writeText(text);
    appendLog('ログをコピーしました。貼り付け先で Ctrl+V できます。');
    return;
  } catch (error) {
    /* WebView2 で clipboard API が使えない時の控え */
  }
  const helper = document.createElement('textarea');
  helper.value = text;
  helper.style.position = 'fixed';
  helper.style.left = '-9999px';
  document.body.appendChild(helper);
  helper.select();
  let copied = false;
  try { copied = document.execCommand('copy'); } catch (error) { copied = false; }
  helper.remove();
  appendLog(copied ? 'ログをコピーしました。' : 'コピーできませんでした。ログ欄から選択してコピーしてください。');
}

/* ── クリックの配線 ───────────────────────────────────────────── */

function bind() {
  $('place-select').addEventListener('change', onPlaceSelect);

  $('area-input').addEventListener('input', onAreaInput);
  $('area-input').addEventListener('keydown', event => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    checkPlace();
  });
  $('check-place').addEventListener('click', checkPlace);
  $('maps-open').addEventListener('click', openMaps);
  $('modes').addEventListener('click', event => {
    const button = event.target.closest('button[data-mode]');
    if (!button) return;
    setMode(button.dataset.mode);
  });

  $('saveloc').addEventListener('click', event => {
    const button = event.target.closest('button[data-kind]');
    if (!button) return;
    chooseSaveLocation(button.dataset.kind);
  });
  $('choose-output').addEventListener('click', chooseOutput);
  $('retry-plan').addEventListener('click', refreshPlan);
  $('btn-next').addEventListener('click', onNext);
  $('btn-back').addEventListener('click', () => {
    if (state.step > 1) showStep(state.step - 1);
  });
  $('stepper').addEventListener('click', event => {
    const button = event.target.closest('button[data-step]');
    if (!button) return;
    const number = Number(button.dataset.step);
    if (number <= state.step) showStep(number);
  });

  $('btn-advanced').addEventListener('click', openAdvanced);
  $('btn-adv-back').addEventListener('click', closeAdvanced);

  $('btn-cancel').addEventListener('click', async () => {
    $('btn-cancel').disabled = true;
    hideRunError();
    appendLog('中止しています…');
    const jobId = state.job && state.job.id;
    const fallback = setTimeout(() => {
      if (state.job && state.job.id === jobId) {
        onCancelled('中止の応答がありません。処理が続いている可能性があります。アプリを閉じてください。');
      }
    }, 5000);
    try {
      await state.api.cancel();
    } catch (error) {
      appendLog('中止を送れませんでした: ' + error);
      $('btn-cancel').disabled = false;
      showRunError('中止を送れませんでした。もう一度押すか、アプリを閉じてください。');
    }
  });
  $('btn-run-log').addEventListener('click', openAdvanced);
  document.querySelectorAll('[data-open-log]').forEach(button => {
    button.addEventListener('click', openAdvanced);
  });

  $('btn-open-result').addEventListener('click', openResult);
  $('btn-open-folder').addEventListener('click', () => state.api.open_folder());
  $('btn-again').addEventListener('click', () => {
    finishJob();
    showView('input');
    showStep(1);
  });
  $('btn-retry-cancelled').addEventListener('click', async () => {
    finishJob();
    showView('input');
    await goStep(3);
  });
  $('btn-retry-failed').addEventListener('click', async () => {
    const recovery = $('btn-retry-failed').dataset.recovery || '';
    finishJob();
    showView('input');
    if (recovery === 'place') {
      showStep(1);
      $('area-input').focus();
      return;
    }
    await goStep(3);
    if (recovery === 'output') $('choose-output').focus();
  });
  $('btn-save-log').addEventListener('click', saveLog);

  $('get-blender').addEventListener('click', () => state.api.open_url('blender'));
  $('rescan').addEventListener('click', () => state.api.scan());

  bindAdvanced();
  bindRangePreview();
}

/* ── 範囲プレビュー（国土地理院 地理院タイル） ─────────────────── */

let rangeSequence = 0;

const GSI_STD_TILE = 'https://cyberjapandata.gsi.go.jp/xyz/std/';

function rangeVisible() {
  return state.view === 'input' && state.step === 2 && state.form.mode !== 'mesh';
}

function syncRangePreview() {
  const details = $('range-preview');
  const visible = rangeVisible();
  details.hidden = !visible;
  if (!visible && details.open) {
    details.open = false;
    $('view-input').classList.remove('range-open');
    rangeSequence++;
  }
}

function refreshRangePreview() {
  if (rangeVisible() && $('range-preview').open) renderRangePreview();
}

function showRangeError(text) {
  const status = $('range-status');
  status.hidden = false;
  status.textContent = text;
}

function hideRangeError() {
  const status = $('range-status');
  status.hidden = true;
  status.textContent = '';
}

function hideRangeLayers() {
  $('range-tiles').replaceChildren();
  $('range-rect').hidden = true;
  $('range-center').hidden = true;
}

function rangePercent(value, total) {
  return (value / total * 100) + '%';
}

function paintRange(data, seq) {
  const { viewport, rect, center } = data;
  const tiles = $('range-tiles');
  let failed = false;
  data.tiles.forEach(tile => {
    const img = document.createElement('img');
    img.alt = '';
    img.referrerPolicy = 'no-referrer';
    img.decoding = 'async';
    img.style.left = rangePercent(tile.left, viewport.width);
    img.style.top = rangePercent(tile.top, viewport.height);
    img.style.width = rangePercent(tile.width, viewport.width);
    img.style.height = rangePercent(tile.height, viewport.height);
    img.addEventListener('error', () => {
      img.hidden = true;
      if (failed || seq !== rangeSequence || !$('range-preview').open) return;
      failed = true;
      showRangeError('地図タイルを読み込めませんでした。範囲の枠はそのまま使えます。');
    });
    img.src = GSI_STD_TILE + tile.z + '/' + tile.x + '/' + tile.y + '.png';
    tiles.appendChild(img);
  });
  const box = $('range-rect');
  box.hidden = false;
  box.style.left = rangePercent(rect.left, viewport.width);
  box.style.top = rangePercent(rect.top, viewport.height);
  box.style.width = rangePercent(rect.width, viewport.width);
  box.style.height = rangePercent(rect.height, viewport.height);
  const marker = $('range-center');
  marker.hidden = false;
  marker.style.left = rangePercent(center.x, viewport.width);
  marker.style.top = rangePercent(center.y, viewport.height);
}

async function renderRangePreview() {
  const details = $('range-preview');
  const seq = ++rangeSequence;
  hideRangeError();
  hideRangeLayers();
  let data;
  try {
    data = await state.api.range_preview(state.form);
  } catch (error) {
    if (seq !== rangeSequence || !details.open) return;
    showRangeError('地図の範囲を確認できませんでした。広さの指定はそのまま使えます。');
    return;
  }
  if (seq !== rangeSequence || !details.open) return;
  if (!data || !data.ok) {
    showRangeError((data && data.error) || 'この範囲では地図を表示できません。');
    return;
  }
  paintRange(data, seq);
}

function bindRangePreview() {
  const details = $('range-preview');
  details.addEventListener('toggle', () => {
    const open = details.open && rangeVisible();
    $('view-input').classList.toggle('range-open', open);
    if (!open) {
      rangeSequence++;
      hideRangeError();
      hideRangeLayers();
      return;
    }
    renderRangePreview();
  });
}

bind();
