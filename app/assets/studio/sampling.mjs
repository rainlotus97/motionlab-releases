import { studioFetch, studioUrl } from './controls.mjs'
import { createSkeletonRenderer, samplePose } from './skeleton-preview.mjs'
import { reviewWindowState } from './sampling-review.mjs'

const ids = ['error', 'notice', 'confirmation', 'confirmation-text', 'confirmation-accept', 'confirmation-cancel', 'source', 'profile', 'direction', 'degrees', 'source-info', 'continuous', 'sampled', 'continuous-readout', 'sampled-readout', 'preview-badge', 'empty-preview', 'play', 'restart', 'previous', 'next', 'speed', 'review-wrap', 'trails', 'ghost', 'labels', 'source-time', 'source-seek', 'frames', 'frame-count', 'phase-audit', 'wrap-audit', 'revision', 'base', 'copy-base', 'plans', 'adopted', 'new', 'refresh', 'dirty', 'name', 'save', 'duplicate', 'delete', 'adopt', 'cancel-adopt', 'presets', 'pair', 'timing', 'loop', 'timing-info', 'manual-index', 'manual-seek', 'add', 'replace', 'current-frame', 'duration', 'earlier', 'later', 'remove', 'export', 'copy', 'handoff']
const ui = Object.fromEntries(ids.map(id => [id, document.getElementById(id)]))
const directions = { E: 0, NE: 45, N: 90, NW: 135, W: 180, SW: 225, S: 270, SE: 315 }
let sources = []
let state = { revision: 0, plans: [], selectedPlanId: null }
let source = null
let draft = null
let baseline = ''
let renderers = []
let thumbnails = []
let frameButtons = []
let selectedSlot = 0
let manualIndex = 0
let camera = { direction: 'E', degrees: 0 }
let playing = true
let sourceElapsed = 0
let planElapsed = 0
let reviewStart = null
let reviewElapsed = 0
let reviewFinished = false
let previous = performance.now()
let raf = 0
let disposed = false
let loadTicket = 0
let loadController = null
let busy = false
let loading = false
let conflict = false
let draftError = ''
let projectPath = ''
const pageController = new AbortController()
const downloadUrls = new Set()
const revokeTimers = new Set()
let pendingConfirmation = null

const clone = value => JSON.parse(JSON.stringify(value))
const mod = (n, d) => ((n % d) + d) % d
const fmt = value => Number(value.toFixed(4)).toString()
const sameIndex = (a, b) => Math.abs(a - b) < 1e-8
const fields = plan => plan ? Object.fromEntries(['id', 'name', 'sourceId', 'profileId', 'sourceFingerprint', 'frames', 'timing', 'durationsMs', 'camera', 'loop'].filter(key => key in plan).map(key => [key, plan[key]])) : null
const dirty = () => !!draft && JSON.stringify(fields(draft)) !== baseline
const savedPlan = () => draft?.id ? state.plans.find(plan => plan.id === draft.id) : null
const total = () => draft?.durationsMs?.reduce((a, b) => a + b, 0) || source?.durationMs || 1

function notify(message = '') { ui.notice.textContent = message }
function showError(error) { ui.error.hidden = false; ui.error.textContent = error.message || String(error) }
function clearError() { ui.error.hidden = true; ui.error.textContent = '' }
function invalidateHandoff() { ui.handoff.value = ''; ui.handoff.hidden = true }

async function api(path, options) {
  const response = await studioFetch(path, { ...options, signal: options?.signal || pageController.signal })
  let value
  try { value = await response.json() } catch { throw new Error(`服务未返回 JSON（HTTP ${response.status}）`) }
  if (!response.ok) {
    const error = new Error(typeof value.error === 'string' ? value.error : value.error?.message || value.message || `HTTP ${response.status}`)
    error.status = response.status
    throw error
  }
  return value
}

function setConfirmationInert(inert) {
  for (const selector of ['header', '.source-bar', 'main']) {
    const surface = document.querySelector(selector)
    if (surface) surface.inert = inert
  }
}

function resolveConfirmation(accepted) {
  const pending = pendingConfirmation
  if (!pending) return
  pendingConfirmation = null
  ui.confirmation.hidden = true
  setConfirmationInert(false)
  if (!disposed) {
    playing = pending.wasPlaying
    pending.returnFocus?.focus?.()
  }
  pending.resolve(accepted)
}

function askConfirmation(message, acceptLabel = '确认', cancelLabel = '取消') {
  if (disposed || pendingConfirmation) return Promise.resolve(false)
  const wasPlaying = playing
  playing = false
  ui['confirmation-text'].textContent = message
  ui['confirmation-accept'].textContent = acceptLabel
  ui['confirmation-cancel'].textContent = cancelLabel
  ui.confirmation.hidden = false
  const returnFocus = document.activeElement
  setConfirmationInert(true)
  ui.confirmation.scrollIntoView({ block: 'center' })
  ui['confirmation-cancel'].focus()
  return new Promise(resolve => { pendingConfirmation = { resolve, wasPlaying, returnFocus } })
}

async function canDiscard() {
  return !dirty() || await askConfirmation('当前方案有未保存的修改。放弃这些修改并切换？', '放弃并切换', '保留修改')
}

ui['confirmation-accept'].onclick = () => resolveConfirmation(true)
ui['confirmation-cancel'].onclick = () => resolveConfirmation(false)
ui.confirmation.onkeydown = event => { if (event.key === 'Escape') { event.preventDefault(); resolveConfirmation(false) } }

function indexAtTime(time) {
  const times = source.timesMs
  const t = source.loop ? mod(time, source.durationMs) : Math.max(0, Math.min(time, times.at(-1)))
  if (t <= times[0]) return 0
  if (t >= times.at(-1)) {
    if (source.loop && !source.includesEndpoint && source.durationMs > times.at(-1)) return times.length - 1 + (t - times.at(-1)) / (source.durationMs - times.at(-1))
    return times.length - 1
  }
  let lo = 0
  let hi = times.length - 1
  while (lo + 1 < hi) {
    const mid = (lo + hi) >> 1
    if (times[mid] <= t) lo = mid
    else hi = mid
  }
  return lo + (t - times[lo]) / (times[hi] - times[lo])
}

function uniformFrames(count) {
  // Loop phases exclude 1.0; a one-shot samples the full real source interval
  // including its final captured pose. Never round the inverse time mapping.
  const end = source.loop ? source.durationMs : source.timesMs.at(-1)
  const divisor = source.loop ? count : Math.max(1, count - 1)
  return Array.from({ length: count }, (_, i) => indexAtTime(i * end / divisor))
}



function alignReviewSource(window) {
  const time = timeAtIndex(draft.frames[window.slot])
  const next = timeAtIndex(draft.frames[(window.slot + 1) % draft.frames.length])
  const span = mod(next - time, source.durationMs)
  sourceElapsed = mod(time + span * Math.min(window.progress, 1 - 1e-7), source.durationMs)
}

function timeAtIndex(index) {
  const lo = Math.floor(index)
  const weight = index - lo
  if (!weight) return source.timesMs[lo]
  const end = lo + 1 < source.timesMs.length ? source.timesMs[lo + 1] : source.durationMs
  return source.timesMs[lo] + (end - source.timesMs[lo]) * weight
}

function maxIndex() {
  if (source.loop && !source.includesEndpoint) return source.frameCount - 0.000001
  return source.frameCount - 1 - (source.loop && source.includesEndpoint ? 0.000001 : 0)
}

function allocateEven(count) {
  const ms = Math.round(source.durationMs)
  if (ms < count) throw new Error('周期太短，无法为每个姿态分配正整数毫秒。')
  return Array.from({ length: count }, (_, i) => Math.round((i + 1) * ms / count) - Math.round(i * ms / count))
}

function updateDurations() {
  draftError = ''
  if (!draft) return
  try {
    if (!draft.frames.length) throw new Error('至少保留一个姿态。')
    if (draft.timing === 'even') draft.durationsMs = allocateEven(draft.frames.length)
    if (draft.timing === 'source') {
      const times = draft.frames.map(timeAtIndex).map(Math.round)
      const ms = Math.round(source.durationMs)
      draft.durationsMs = times.map((time, i) => {
        if (i + 1 < times.length) return draft.loop ? mod(times[i + 1] - time, ms) : times[i + 1] - time
        if (draft.loop) return times.length === 1 ? ms : mod(times[0] - time, ms)
        const cadence = Math.max(1, Math.round(source.timesMs.at(-1) - (source.timesMs.at(-2) || 0)))
        return ms - time > 0 ? ms - time : cadence
      })
    }
    if (draft.durationsMs.length !== draft.frames.length || draft.durationsMs.some(ms => !Number.isInteger(ms) || ms < 1 || ms > 60000)) throw new Error('时长须为 1–60000 ms；源时序需保持合法顺序，太近的姿态请调整或改用自定义时长。')
    if (draft.frames.some(index => !Number.isFinite(index) || index < 0 || index > maxIndex())) throw new Error('源姿态索引超出当前来源范围。')
  } catch (error) { draftError = error.message }
}

function phasePairs() {
  if (!draft || !source?.loop) return { paired: false, pairs: [], pairCount: 0, oneForwardCycle: false }
  const phases = draft.frames.map(index => timeAtIndex(index) / source.durationMs)
  const pairs = []
  const half = phases.length / 2
  const errors = []
  if (Number.isInteger(half)) {
    for (let i = 0; i < half; i++) {
      const error = Math.abs(mod(phases[i + half] - phases[i], 1) - .5)
      errors.push(error)
      if (error < 1e-9) pairs.push([i, i + half])
    }
  }
  const span = phases.length === 1 ? 1 : phases.reduce((sum, phase, i) => sum + mod(phases[(i + 1) % phases.length] - phase, 1), 0)
  const oneForwardCycle = Math.abs(span - 1) < 1e-7
  return { paired: oneForwardCycle && pairs.length * 2 === phases.length, pairs, pairCount: errors.length, oneForwardCycle, maxHalfCycleErrorMs: errors.length ? Math.max(...errors) * source.durationMs : null }
}

function renderSources() {
  ui.source.replaceChildren(...sources.map(item => {
    const option = document.createElement('option')
    option.value = item.id
    option.textContent = `${item.name} · ${item.id}${item.available === false ? '（缺少文件）' : ''}`
    option.disabled = item.available === false
    return option
  }))
}

function renderPlans() {
  ui.revision.textContent = `revision ${state.revision}`
  ui.base.setAttribute('aria-current', String(!draft))
  ui.plans.replaceChildren(...state.plans.map(plan => {
    const button = document.createElement('button')
    button.className = 'plan-button'
    button.disabled = busy || loading
    button.dataset.adopted = String(plan.id === state.selectedPlanId)
    button.setAttribute('aria-current', String(plan.id === draft?.id))
    button.append(document.createTextNode(plan.name))
    const small = document.createElement('small')
    small.textContent = `${plan.frames.length} 帧 · ${plan.camera.direction} · ${plan.timing}${plan.id === state.selectedPlanId ? ' · 已采用' : ' · 已保存'}${plan.stale ? ' · 来源已变' : ''}`
    button.append(small)
    button.onclick = action(async () => { if (await canDiscard()) await openPlan(plan) })
    return button
  }))
  const adopted = state.plans.find(plan => plan.id === state.selectedPlanId)
  ui.adopted.textContent = adopted ? `后续制作采用：${adopted.name}` : '尚未采用自定义方案。'
}

function renderControls() {
  const active = !!draft
  const saved = savedPlan()
  const changed = dirty()
  if (changed) invalidateHandoff()
  const blocked = busy || loading
  ui.base.disabled = blocked
  for (const button of ui.plans.querySelectorAll('button')) button.disabled = blocked
  for (const button of frameButtons) button.disabled = blocked
  ui.source.disabled = blocked
  ui.profile.disabled = blocked
  ui.dirty.textContent = conflict ? '保存冲突 · 草稿保留' : !active ? '原版观察' : changed ? '未保存修改' : '已保存'
  ui.dirty.dataset.dirty = String(changed)
  ui.dirty.dataset.conflict = String(conflict)
  ui['preview-badge'].textContent = !active ? '尚未选帧' : `${draft.frames.length} 帧${changed ? ' · 本地草稿' : ' · 已保存'}`
  ui['empty-preview'].hidden = active
  ui.name.disabled = !active || blocked
  if (document.activeElement !== ui.name) ui.name.value = draft?.name || ''
  ui.save.disabled = !active || !changed || blocked || conflict || !!draftError || !!saved?.stale
  ui.duplicate.disabled = !active || blocked
  ui.delete.disabled = !saved || blocked
  ui.adopt.disabled = !saved || changed || blocked || saved.stale || saved.id === state.selectedPlanId
  ui.adopt.textContent = saved?.id === state.selectedPlanId ? '当前已采用此方案' : '采用已保存方案'
  ui.export.disabled = !saved || changed || blocked || saved.stale
  ui.copy.disabled = ui.export.disabled
  ui.new.disabled = !source || blocked
  ui['copy-base'].disabled = !source || blocked
  ui['cancel-adopt'].disabled = !state.selectedPlanId || blocked
  ui.refresh.disabled = blocked
  ui.timing.disabled = !active || blocked
  ui.timing.value = draft?.timing || 'even'
  ui.loop.disabled = !active || blocked
  ui.loop.checked = draft?.loop ?? source?.loop ?? true
  ui.pair.disabled = !active || !source?.loop || blocked
  ui.add.disabled = !source || blocked
  ui.replace.disabled = !active || blocked
  ui['review-wrap'].disabled = !active || draft.frames.length < 2 || !draft.loop || !source?.loop || !!draftError
  ui.previous.disabled = !active
  ui.next.disabled = !active
  ui.direction.value = camera.direction
  if (document.activeElement !== ui.degrees) ui.degrees.value = camera.degrees
  const current = active && draft.frames[selectedSlot] !== undefined
  ui.duration.disabled = !current || blocked
  if (document.activeElement !== ui.duration) ui.duration.value = current ? draft.durationsMs[selectedSlot] : ''
  ui.earlier.disabled = !current || selectedSlot === 0 || blocked
  ui.later.disabled = !current || selectedSlot === draft.frames.length - 1 || blocked
  ui.remove.disabled = !current || draft.frames.length <= 1 || blocked
  ui['current-frame'].textContent = current ? `第 ${selectedSlot + 1} 格 · 源索引 ${fmt(draft.frames[selectedSlot])} · 源时间 ${timeAtIndex(draft.frames[selectedSlot]).toFixed(2)} ms` : '点击左侧姿态后可调整时长或顺序。'
  ui['frame-count'].textContent = active ? `${draft.frames.length} 个姿态` : '0 帧'
  ui['timing-info'].textContent = draftError || (active ? `方案周期 ${total()} ms；原连续源周期 ${source.durationMs.toFixed(2)} ms。两侧按各自时序播放。` : '建立方案后选择时长方式。')
  const pairs = phasePairs()
  const audit = !changed ? saved?.phaseAudit : null
  const phase = audit || pairs
  const phasePaired = phase.paired
  const phaseError = phase.maxHalfCycleErrorMs
  const halves = active && draft.frames.length % 2 === 0 ? [draft.durationsMs.slice(0, draft.frames.length / 2).reduce((a, b) => a + b, 0), draft.durationsMs.slice(draft.frames.length / 2).reduce((a, b) => a + b, 0)] : null
  const timingAudit = audit?.playbackTimingAudit || (halves ? { halfDurationsMs: halves, maximumOppositeHoldDifferenceMs: Math.max(...draft.durationsMs.slice(0, draft.frames.length / 2).map((ms, i) => Math.abs(ms - draft.durationsMs[i + draft.frames.length / 2]))) } : null)
  const timingText = timingAudit?.halfDurationsMs ? ` 播放半周期 ${timingAudit.halfDurationsMs.join(' / ')} ms（差 ${Math.abs(timingAudit.halfDurationsMs[0] - timingAudit.halfDurationsMs[1])} ms），对侧停留最大差 ${timingAudit.maximumOppositeHoldDifferenceMs} ms。` : ''
  const orderWarning = phase.oneForwardCycle === false ? ' 播放顺序未形成单次正向源周期，请核对乱序或跨多圈。' : ''
  ui['phase-audit'].textContent = !active ? '保存方案后显示来源相位审计。' : !source.loop ? '一次性动作：按完整区间取样并保留终点，不作半周期配对判断。' : `${audit ? '已保存审计' : '当前草稿预检'}：${phasePaired ? '半周期相位配对齐全' : '半周期相位或顺序未通过'}；${phase.pairCount ?? phase.pairs?.length ?? 0} 对。${Number.isFinite(phaseError) ? `最大半周期误差 ${phaseError.toFixed(4)} ms。` : ''}${orderWarning}${timingText}`
  ui['wrap-audit'].textContent = active ? `环回段：第 ${draft.frames.length} 格 → 第 1 格。${draft.loop ? '使用“末→首慢放”和轨迹／叠影检查手足移动。' : '当前方案不循环，保留最后姿态。'}${changed ? ' 导出和采用前请先保存。' : ''}` : '原版连续保留来源原时序；不会覆盖为离散播放。'
  for (const button of ui.presets.querySelectorAll('button')) button.disabled = !source || blocked
}

function disposeThumbnails() {
  for (const renderer of thumbnails) renderer.dispose()
  thumbnails = []
}

function renderFrames() {
  disposeThumbnails()
  frameButtons = []
  ui.frames.replaceChildren()
  if (!draft || !source) return
  const paired = new Set(phasePairs().pairs.flat())
  for (const [slot, index] of draft.frames.slice(0, 128).entries()) {
    const button = document.createElement('button')
    button.className = 'pose-button'
    button.dataset.pair = String(paired.has(slot))
    button.setAttribute('aria-label', `第 ${slot + 1} 格，源索引 ${fmt(index)}`)
    button.setAttribute('aria-pressed', String(slot === selectedSlot))
    const canvas = document.createElement('canvas')
    canvas.width = 128
    canvas.height = 132
    const label = document.createElement('span')
    label.textContent = `${String(slot + 1).padStart(2, '0')} · #${fmt(index)} / ${draft.durationsMs[slot]}ms`
    button.append(canvas, label)
    button.onclick = () => selectSlot(slot)
    ui.frames.append(button)
    const renderer = createSkeletonRenderer(canvas, source, { padding: 11 })
    thumbnails.push(renderer)
    renderer.draw({ frameIndex: index, directionDegrees: camera.degrees })
    frameButtons.push(button)
  }
  if (draft.frames.length > 128) {
    const note = document.createElement('p')
    note.textContent = '仅显示前128格缩略图；完整方案仍保留。'
    ui.frames.append(note)
  }
}

function redrawEditor() {
  invalidateHandoff()
  reviewFinished = false
  updateDurations()
  selectedSlot = Math.max(0, Math.min(selectedSlot, (draft?.frames.length || 1) - 1))
  if (!playing && draft?.frames.length) {
    planElapsed = draft.durationsMs.slice(0, selectedSlot).reduce((a, b) => a + b, 0)
    sourceElapsed = timeAtIndex(draft.frames[selectedSlot])
  }
  renderPlans()
  renderFrames()
  renderControls()
}

async function loadSource(id, profileId) {
  if (disposed) return false
  const ticket = ++loadTicket
  loadController?.abort()
  loadController = new AbortController()
  loading = true
  invalidateHandoff()
  renderControls()
  const query = new URLSearchParams({ id })
  if (profileId) query.set('profile', profileId)
  notify('正在读取来源骨架…')
  let next
  try { next = await api(`/api/sampling/source?${query}`, { signal: loadController.signal }) }
  catch (error) {
    if (ticket === loadTicket) {
      loading = false
      if (source) { ui.source.value = source.id; ui.profile.value = source.profileId }
      renderControls()
      notify('来源载入失败，当前已载入的预览与草稿保留。')
    }
    throw error
  }
  if (disposed || ticket !== loadTicket) return false
  source = next.source || next
  for (const renderer of renderers) renderer.dispose()
  renderers = [createSkeletonRenderer(ui.continuous, source), createSkeletonRenderer(ui.sampled, source)]
  ui.source.value = source.id
  ui.profile.replaceChildren(...source.profiles.map(profile => {
    const option = document.createElement('option')
    option.value = profile.id
    option.textContent = profile.name
    return option
  }))
  ui.profile.value = source.profileId
  ui['source-seek'].max = source.durationMs
  ui['manual-index'].max = maxIndex()
  ui['manual-seek'].max = maxIndex()
  sourceElapsed = 0
  planElapsed = 0
  manualIndex = 0
  setManualIndex(0, false)
  ui['source-info'].textContent = `${source.name} · ${source.frameCount} 个源采样 · ${source.durationMs.toFixed(2)} ms · ${source.loop ? '来源声明循环' : '一次性来源'} · ${source.includesEndpoint ? '包含周期终点' : '未重复终点'}。来源声明不代表动作或首尾已验收。`
  reviewStart = null
  notify('来源已载入；打开和编辑方案不会自动采用。')
  clearError()
  loading = false
  renderControls()
  return true
}

function makeDraft(count = 8) {
  draft = { name: `${source.name} · ${camera.direction} · ${count}帧`, sourceId: source.id, profileId: source.profileId, sourceFingerprint: source.fingerprint, frames: uniformFrames(count), timing: 'even', durationsMs: [], camera: clone(camera), loop: source.loop }
  baseline = ''
  conflict = false
  selectedSlot = 0
  planElapsed = 0
  reviewStart = null
  redrawEditor()
}

async function openPlan(plan) {
  if (plan.stale) throw new Error('来源文件已变化，无法把当前骨架冒充旧方案重放。方案记录保留；请重新选择来源、审阅并新建方案。')
  if (!await loadSource(plan.sourceId, plan.profileId)) return
  draft = clone(plan)
  camera = clone(plan.camera)
  baseline = JSON.stringify(fields(draft))
  conflict = false
  selectedSlot = 0
  redrawEditor()
}

function selectSlot(slot) {
  if (!draft?.frames.length) return
  selectedSlot = mod(slot, draft.frames.length)
  playing = false
  reviewStart = null
  reviewFinished = false
  planElapsed = draft.durationsMs.slice(0, selectedSlot).reduce((a, b) => a + b, 0)
  sourceElapsed = timeAtIndex(draft.frames[selectedSlot])
  setManualIndex(draft.frames[selectedSlot], false)
  renderControls()
}

function setManualIndex(index, seek = true) {
  if (!source) return
  manualIndex = Math.max(0, Math.min(index, maxIndex()))
  ui['manual-index'].value = fmt(manualIndex)
  ui['manual-seek'].value = manualIndex
  if (seek) { sourceElapsed = timeAtIndex(manualIndex); playing = false; reviewStart = null; reviewFinished = false }
}

function replaceFrames(frames) {
  const old = new Map(draft.frames.map((index, slot) => [index, draft.durationsMs[slot]]))
  draft.frames = frames
  selectedSlot = 0
  if (draft.timing === 'custom') draft.durationsMs = frames.map(index => old.get(index) || Math.max(1, Math.round(source.durationMs / frames.length)))
  planElapsed = 0
  reviewStart = null
  redrawEditor()
}

async function mutate(operation, payload) {
  busy = true
  invalidateHandoff()
  clearError()
  renderControls()
  try {
    const next = await api('/api/sampling/mutate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ operation, payload, expectedRevision: state.revision }) })
    state = next.state || next
    return state
  } catch (error) {
    if (error.status === 409) {
      conflict = true
      try { const next = await api('/api/sampling/state'); state = next.state || next } catch { /* Keep local draft and original failure. */ }
      error.message += '。本地草稿保留；请刷新并重新打开已保存方案核对。'
    }
    throw error
  } finally { busy = false; renderPlans(); renderControls() }
}

function action(fn) {
  return async (...args) => {
    try { await fn(...args) } catch (error) { if (error.name !== 'AbortError') showError(error) }
  }
}

ui.source.onchange = action(async () => {
  const requested = ui.source.value
  if (!await canDiscard()) { ui.source.value = source.id; return }
  if (await loadSource(requested)) { draft = null; baseline = ''; conflict = false; redrawEditor() }
})
ui.profile.onchange = action(async () => {
  const requested = ui.profile.value
  if (!await canDiscard()) { ui.profile.value = source.profileId; return }
  if (await loadSource(source.id, requested)) { draft = null; baseline = ''; conflict = false; redrawEditor() }
})
ui.base.onclick = action(async () => {
  if (!await canDiscard()) return
  draft = null
  baseline = ''
  conflict = false
  reviewStart = null
  redrawEditor()
})
ui.new.onclick = action(async () => { if (source && await canDiscard()) makeDraft() })
ui['copy-base'].onclick = action(async () => {
  if (!source || !await canDiscard()) return
  if (source.selectableFrameCount > 1024) throw new Error('完整原版超过1024个姿态的方案上限。请使用相位预设或手选，不会静默减少原版采样。')
  draft = { name: `${source.name} · 原版连续副本`.slice(0, 120), sourceId: source.id, profileId: source.profileId, sourceFingerprint: source.fingerprint, frames: Array.from({ length: source.selectableFrameCount }, (_, i) => i), timing: 'source', durationsMs: [], camera: clone(camera), loop: source.loop }
  baseline = ''
  conflict = false
  selectedSlot = 0
  planElapsed = 0
  reviewStart = null
  redrawEditor()
  notify('已建立原版连续的本地副本；保留源采样和时序，尚未保存或采用。')
})
ui.refresh.onclick = action(async () => {
  if (!await canDiscard()) return
  const next = await api('/api/sampling/state')
  state = next.state || next
  const saved = savedPlan()
  conflict = false
  if (saved) await openPlan(saved)
  else { draft = null; baseline = ''; redrawEditor() }
  notify('已刷新保存记录。')
})
ui.name.oninput = () => { if (draft) { draft.name = ui.name.value; renderControls() } }
ui.save.onclick = action(async () => {
  if (!draft || draftError || !draft.name.trim()) throw new Error(draftError || '请输入方案名称。')
  const previousIds = new Set(state.plans.map(plan => plan.id))
  const id = draft.id
  await mutate(id ? 'update' : 'create', fields(draft))
  const saved = state.plans.find(plan => id ? plan.id === id : !previousIds.has(plan.id))
  if (!saved) throw new Error('保存返回中缺少方案记录，请刷新检查。')
  draft = clone(saved)
  baseline = JSON.stringify(fields(draft))
  conflict = false
  redrawEditor()
  notify('方案已保存；是否采用由“采用已保存方案”单独决定。')
})
ui.duplicate.onclick = action(async () => {
  if (!draft) return
  const values = fields(draft)
  values.name = `${draft.name} · 副本`.slice(0, 120)
  const previousIds = new Set(state.plans.map(plan => plan.id))
  // A dirty/new local draft is copied explicitly, preserving its current edits.
  const operation = draft.id ? 'duplicate' : 'create'
  await mutate(operation, values)
  const saved = state.plans.find(plan => !previousIds.has(plan.id))
  if (!saved) throw new Error('复制返回中缺少方案，请刷新检查。')
  draft = clone(saved)
  baseline = JSON.stringify(fields(draft))
  redrawEditor()
  notify('副本已保存，当前采用方案未改变。')
})
ui.delete.onclick = action(async () => {
  const saved = savedPlan()
  if (!saved || !await askConfirmation(`删除已保存方案“${saved.name}”？原始骨架不会被修改。`, '确认删除', '取消')) return
  await mutate('delete', { id: saved.id })
  draft = null
  baseline = ''
  conflict = false
  redrawEditor()
  notify('方案已删除。')
})
ui.adopt.onclick = action(async () => {
  const saved = savedPlan()
  if (!saved || dirty()) throw new Error('先保存当前修改，再明确采用方案。')
  await mutate('select', { id: saved.id })
  notify(`已采用“${saved.name}”，后续制作可按此方案导出姿态。`)
})
ui['cancel-adopt'].onclick = action(async () => {
  const preserveDraft = dirty()
  await mutate('select', { id: null })
  if (!preserveDraft) { draft = null; baseline = ''; reviewStart = null; redrawEditor() }
  notify(`已取消采用，后续恢复原版默认。${preserveDraft ? '当前未保存草稿仍保留。' : ''}`)
})
ui.presets.onclick = event => {
  const button = event.target.closest('button[data-count]')
  if (!button || !source) return
  const count = Number(button.dataset.count)
  if (!draft) makeDraft(count)
  else {
    draft.timing = 'even'
    replaceFrames(uniformFrames(count))
    notify(source.loop ? `已按 i×源周期/${count} 精确取相位；尚未保存或采用。` : `已对完整源区间取 ${count} 个姿态并保留终点；尚未保存或采用。`)
  }
}
ui.pair.onclick = () => {
  if (!draft || !source.loop) return
  const frames = [...draft.frames]
  for (const index of draft.frames) {
    const opposite = indexAtTime(mod(timeAtIndex(index) + source.durationMs / 2, source.durationMs))
    if (!frames.some(existing => sameIndex(existing, opposite))) frames.push(opposite)
  }
  replaceFrames(frames.sort((a, b) => timeAtIndex(a) - timeAtIndex(b)))
  notify('已补齐半周期相位并按源时间排列；没有镜像或改写身体动作。')
}
ui.timing.onchange = () => { if (draft) { draft.timing = ui.timing.value; redrawEditor() } }
ui.loop.onchange = () => { if (draft) { draft.loop = ui.loop.checked; redrawEditor() } }
ui.direction.onchange = () => {
  camera = { direction: ui.direction.value, degrees: directions[ui.direction.value] }
  if (draft) draft.camera = clone(camera)
  renderFrames()
  renderControls()
}
ui.degrees.onchange = () => {
  const value = Number(ui.degrees.value)
  if (!Number.isFinite(value) || value < -360 || value > 360) { showError(new Error('相机角度需在 -360° 至 360°。')); return }
  camera.degrees = value
  if (draft) draft.camera = clone(camera)
  renderFrames()
  renderControls()
}
ui['manual-index'].onchange = () => { const value = Number(ui['manual-index'].value); if (Number.isFinite(value)) setManualIndex(value) }
ui['manual-seek'].oninput = () => setManualIndex(Number(ui['manual-seek'].value))
ui.add.onclick = () => {
  if (!source) return
  if (!draft) {
    draft = { name: `${source.name} · 手选方案`, sourceId: source.id, profileId: source.profileId, sourceFingerprint: source.fingerprint, frames: [manualIndex], timing: 'even', durationsMs: [], camera: clone(camera), loop: source.loop }
    baseline = ''
    conflict = false
  } else if (draft.frames.some(index => sameIndex(index, manualIndex))) { notify('此源姿态已在方案中。'); return }
  else draft.frames.push(manualIndex)
  if (draft.timing === 'custom') draft.durationsMs.push(Math.max(1, Math.round(source.durationMs / draft.frames.length)))
  selectedSlot = draft.frames.length - 1
  redrawEditor()
  selectSlot(selectedSlot)
}
ui.replace.onclick = () => {
  if (!draft) return
  if (draft.frames.some((index, slot) => slot !== selectedSlot && sameIndex(index, manualIndex))) { showError(new Error('此源姿态已经在另一格中，不能重复替换。')); return }
  draft.frames[selectedSlot] = manualIndex
  draft.timing = 'custom'
  reviewStart = null
  redrawEditor()
  selectSlot(selectedSlot)
  notify('已替换选中姿态并保留原逐帧时长；现为自定义时长，尚未保存。')
}
ui.duration.onchange = () => {
  if (!draft) return
  const value = Number(ui.duration.value)
  if (!Number.isInteger(value) || value < 1 || value > 60000) { showError(new Error('停留时长须为 1–60000 的整数毫秒。')); return }
  draft.timing = 'custom'
  draft.durationsMs[selectedSlot] = value
  redrawEditor()
}
ui.duration.onfocus = () => { playing = false }
function reorder(delta) {
  const next = selectedSlot + delta
  if (!draft || next < 0 || next >= draft.frames.length) return
  ;[draft.frames[selectedSlot], draft.frames[next]] = [draft.frames[next], draft.frames[selectedSlot]]
  ;[draft.durationsMs[selectedSlot], draft.durationsMs[next]] = [draft.durationsMs[next], draft.durationsMs[selectedSlot]]
  selectedSlot = next
  redrawEditor()
  selectSlot(next)
}
ui.earlier.onclick = () => reorder(-1)
ui.later.onclick = () => reorder(1)
ui.remove.onclick = () => {
  if (!draft || draft.frames.length <= 1) return
  draft.frames.splice(selectedSlot, 1)
  draft.durationsMs.splice(selectedSlot, 1)
  redrawEditor()
  selectSlot(selectedSlot)
}
ui.play.onclick = () => {
  if (!playing && ((draft && !draft.loop && planElapsed >= total()) || (!draft && source && !source.loop && sourceElapsed >= source.durationMs))) {
    sourceElapsed = 0
    planElapsed = 0
  }
  reviewFinished = false
  playing = !playing
}
ui.restart.onclick = () => { sourceElapsed = 0; planElapsed = 0; reviewElapsed = 0; reviewStart = null; reviewFinished = false; playing = true }
ui.previous.onclick = () => selectSlot(selectedSlot - 1)
ui.next.onclick = () => selectSlot(selectedSlot + 1)
ui['source-seek'].oninput = () => {
  sourceElapsed = Number(ui['source-seek'].value)
  setManualIndex(indexAtTime(sourceElapsed), false)
  playing = false
  reviewStart = null
  reviewFinished = false
}
ui['review-wrap'].onclick = () => {
  if (!draft || draft.frames.length < 2 || !draft.loop || !source.loop) return
  if (reviewStart !== null) { reviewStart = null; reviewFinished = false; notify('已退出首尾片段审查，继续完整方案播放。'); return }
  const window = reviewWindowState(draft.durationsMs, 0)
  reviewStart = window.planTimeMs
  reviewElapsed = 0
  reviewFinished = false
  planElapsed = window.planTimeMs
  alignReviewSource(window)
  ui.speed.value = '0.5'
  playing = true
  notify(`单次查看最后 ${window.slots.length / 2} 格 → 最前 ${window.slots.length / 2} 格，播完暂停；左侧按当前格的源相位区间对齐。片段结束暂停不是真实动作停顿。`)
}

async function exportContract() {
  const saved = savedPlan()
  if (!saved || dirty()) throw new Error('请先保存方案再导出。')
  return api(`/api/sampling/export?${new URLSearchParams({ id: saved.id })}`)
}
ui.export.onclick = action(async () => {
  const contract = await exportContract()
  const blob = new Blob([JSON.stringify(contract, null, 2) + '\n'], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  downloadUrls.add(url)
  const link = document.createElement('a')
  link.href = url
  link.download = `${savedPlan().id}.sampling-plan.json`
  link.click()
  const timer = setTimeout(() => { URL.revokeObjectURL(url); downloadUrls.delete(url); revokeTimers.delete(timer) }, 1000)
  revokeTimers.add(timer)
  notify('已下载保存方案的生成合同；当前采用关系没有改变。')
})
ui.copy.onclick = action(async () => {
  const contract = await exportContract()
  const plan = contract.plan
  const instruction = `请使用 $character-motion，按项目清单 ${projectPath || '（请一并提供实际 studio.project.json 路径）'}（项目 ${contract.projectId}）中的骨骼选帧方案“${plan.name}”（${plan.id}，修订 ${contract.storeRevision}）继续制作。本次明确指定此方案；${state.selectedPlanId === plan.id ? '它也是当前已采用方案。' : '即使它尚未设为全局采用，本次仍按此明确方案ID使用，不改变全局采用关系。'}来源 ${plan.sourceId}／身体 ${plan.profileId}，相机 ${plan.camera.direction}（${plan.camera.degrees}°），按导出的 sampling-plan JSON 中 ${plan.frames.length} 个姿态、左右解剖身份、顺序和时长绘制。先复核手足轨迹、首尾、衣装与配准，再接入现有工作台；不要镜像补动作、不要自动扩展其他方向。该方案只代表选帧，不代表人物动作已验收。`
  ui.handoff.hidden = false
  ui.handoff.value = instruction
  try { await navigator.clipboard.writeText(instruction); notify('会话指令已复制。请同时提供导出的方案 JSON。') }
  catch { ui.handoff.focus(); ui.handoff.select(); notify('浏览器未允许自动复制；已选中下方完整指令，可直接复制。') }
})

function activeSlot() {
  if (!draft?.frames.length || draftError) return 0
  const duration = total()
  const time = draft.loop ? mod(planElapsed, duration) : Math.min(planElapsed, duration - .0001)
  let cursor = 0
  for (let i = 0; i < draft.durationsMs.length; i++) {
    cursor += draft.durationsMs[i]
    if (time < cursor) return i
  }
  return draft.frames.length - 1
}

function render(now) {
  if (disposed) return
  const delta = Math.max(0, Math.min(now - previous, 100)) * Number(ui.speed.value || 1)
  previous = now
  if (source) {
    if (playing) {
      sourceElapsed += delta
      if (draft) {
        if (reviewStart !== null) {
          reviewElapsed += delta
          const window = reviewWindowState(draft.durationsMs, reviewElapsed)
          planElapsed = window.planTimeMs
          alignReviewSource(window)
          if (window.done) {
            reviewStart = null
            reviewFinished = true
            selectedSlot = window.slot
            playing = false
            renderControls()
            notify(`首尾片段已播完，进入第 ${window.slot + 1} 格后暂停，两侧定位同一源姿态。此处是审查暂停，不是真实动作停顿；可点击“重播首尾片段”。`)
          }
        } else planElapsed += delta
        if (!draft.loop && planElapsed >= total()) { planElapsed = total(); playing = false }
      } else if (!source.loop && sourceElapsed >= source.durationMs) { sourceElapsed = source.durationMs; playing = false }
    }
    const time = source.loop ? mod(sourceElapsed, source.durationMs) : Math.min(sourceElapsed, source.durationMs)
    const view = { directionDegrees: camera.degrees, showLabels: ui.labels.checked, showTrail: ui.trails.checked }
    renderers[0]?.draw({ ...view, timeMs: time, interpolate: true })
    ui['source-seek'].value = time
    ui['source-time'].textContent = `${time.toFixed(1)} / ${source.durationMs.toFixed(1)} ms`
    ui['continuous-readout'].textContent = `${reviewStart !== null ? '源相位对齐审查' : reviewFinished ? '审查时刻暂停' : '连续源'} · #${fmt(indexAtTime(time))} · ${(time / source.durationMs * 100).toFixed(1)}% 周期`
    if (draft) {
      const slot = activeSlot()
      const index = draft.frames[slot]
      // Explicit source sampling makes fractional indices real interpolation,
      // while the selected preview itself holds each chosen pose discretely.
      samplePose(source, { frameIndex: index })
      renderers[1]?.draw({ ...view, frameIndex: index, ghostFrameIndices: ui.ghost.checked ? [draft.frames[mod(slot - 1, draft.frames.length)]] : [] })
      ui['sampled-readout'].textContent = `第 ${slot + 1}/${draft.frames.length} 格 · #${fmt(index)} · 停留 ${draft.durationsMs[slot]} ms${reviewStart !== null ? ' · 单次末→首审查' : reviewFinished ? ' · 首尾片段结束，已暂停' : ''}`
      if (selectedSlot !== slot && playing) { selectedSlot = slot; renderControls() }
      frameButtons.forEach((button, i) => button.setAttribute('aria-pressed', String(i === slot)))
    } else {
      renderers[1]?.draw({ ...view, frameIndex: 0 })
      ui['sampled-readout'].textContent = '尚无离散方案 · 原版连续不受编辑影响'
    }
  }
  ui.play.textContent = playing ? '暂停' : '播放'
  ui['review-wrap'].textContent = reviewStart !== null ? '退出环回审查' : reviewFinished ? '重播首尾片段' : '末→首慢放'
  raf = requestAnimationFrame(render)
}

window.addEventListener('pagehide', () => {
  disposed = true
  resolveConfirmation(false)
  loadTicket++
  loadController?.abort()
  pageController.abort()
  cancelAnimationFrame(raf)
  for (const timer of revokeTimers) clearTimeout(timer)
  for (const url of downloadUrls) URL.revokeObjectURL(url)
  revokeTimers.clear()
  downloadUrls.clear()
  for (const renderer of renderers) renderer.dispose()
  disposeThumbnails()
})
window.addEventListener('pageshow', event => { if (event.persisted) location.reload() })

async function initialize() {
  const [catalog, saved] = await Promise.all([api('/api/sampling/sources'), api('/api/sampling/state')])
  if (disposed) return
  sources = catalog.sources || catalog
  projectPath = catalog.projectPath || ''
  state = saved.state || saved
  installCatalogDraftRestore()
  renderSources()
  renderPlans()
  const candidate = state.plans.find(plan => plan.id === state.selectedPlanId && !plan.stale) || state.plans.find(plan => !plan.stale)
  if (candidate) {
    await openPlan(candidate)
    notify(`当前预演已保存方案“${candidate.name}”；${state.selectedPlanId === candidate.id ? '此方案此前已被采用。' : '尚未采用，点击“采用已保存方案”才会用于后续制作。'}`)
    return
  }
  const initial = sources.find(item => item.format === '100style' && item.available !== false) || sources.find(item => item.available !== false)
  if (!initial) throw new Error('参考库暂无可用动作来源。请在资源库 studio.tools.json 登记 sampling 来源后刷新。')
  await loadSource(initial.id)
  redrawEditor()
}

function installCatalogDraftRestore() {
  const key = `character-motion-sampling-draft:${projectPath}`
  let stored
  try { stored = JSON.parse(localStorage.getItem(key)) } catch { return }
  if (stored?.version !== 1 || stored.projectPath !== projectPath || !stored.draft) return
  const button = document.createElement('button')
  button.type = 'button'; button.textContent = '恢复切换前的本机草稿'
  ui.save.after(button)
  button.onclick = action(async () => {
    if (busy || loading || pendingConfirmation) return
    if (!await canDiscard()) return
    const value = stored.draft
    if (typeof value.name !== 'string' || !Array.isArray(value.frames) || !value.frames.length || value.frames.length > 1024 || value.frames.some(index => !Number.isFinite(index) || index < 0) || !Array.isArray(value.durationsMs) || value.durationsMs.length !== value.frames.length || value.durationsMs.some(ms => !Number.isInteger(ms) || ms < 1 || ms > 60000) || !['even', 'source', 'custom'].includes(value.timing) || typeof value.loop !== 'boolean' || !Object.hasOwn(directions, value.camera?.direction) || !Number.isFinite(value.camera?.degrees)) throw new Error('本机选帧草稿格式无效；当前方案保持不变。')
    // Validate before replacing the currently loaded source or local edits.
    const result = await api(`/api/sampling/source?${new URLSearchParams({ id: value.sourceId, profile: value.profileId })}`)
    const available = result.source || result
    if (available.fingerprint !== value.sourceFingerprint) throw new Error('本机草稿引用的骨架来源已变化，不能恢复为当前方案。原草稿仍保留在本机。')
    const maximum = available.loop && !available.includesEndpoint ? available.frameCount - .000001 : available.frameCount - 1 - (available.loop && available.includesEndpoint ? .000001 : 0)
    if (value.frames.some(index => index > maximum)) throw new Error('本机草稿含超出当前来源范围的姿态；当前方案保持不变。')
    if (!await loadSource(value.sourceId, value.profileId)) return
    draft = clone(value)
    if (draft.id && !state.plans.some(plan => plan.id === draft.id)) delete draft.id
    camera = clone(draft.camera)
    const saved = savedPlan()
    baseline = saved ? JSON.stringify(fields(saved)) : ''
    conflict = false; selectedSlot = 0; playing = false; redrawEditor()
    notify('已恢复切换前的本机选帧草稿；尚未写入正式方案，也未改变采用关系。')
  })
}

raf = requestAnimationFrame(render)
renderControls()
initialize().catch(showError)
window.motionStudioBridge = {
  pause: () => { playing = false },
  pending: () => dirty() ? {
    message: '当前骨骼选帧方案有未保存修改。保留本机草稿后再打开；返回此工程时会提供恢复入口，正式方案与采用关系保持不变。',
    preserve: () => localStorage.setItem(`character-motion-sampling-draft:${projectPath}`, JSON.stringify({ version: 1, projectPath, draft: fields(draft) }))
  } : null
}
