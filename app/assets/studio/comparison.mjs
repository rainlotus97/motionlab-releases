import { studioFetch, studioUrl } from './controls.mjs'
import { reviewWindowState } from './sampling-review.mjs'
import { createComparisonView, drawArtwork, drawComparisonSkeleton, drawComparisonGuides, drawArtMarkers } from './comparison-renderer.mjs'

const ids = ['error', 'notice', 'comparison', 'relationship', 'reference-intent', 'refresh', 'dirty-count', 'target', 'art', 'target-readout', 'art-readout', 'art-status', 'play', 'previous', 'next', 'restart', 'speed', 'review-wrap', 'overlay', 'opacity', 'guides', 'ghost', 'labels', 'markers', 'background', 'alignment', 'seek', 'playback-info', 'count', 'frames', 'slot-badge', 'identity', 'art-link', 'master-link', 'master-info', 'sheet-link', 'sheet-info', 'provenance', 'review-dirty', 'review-status', 'tags', 'note', 'save-review', 'reset-review', 'review-info', 'download', 'copy', 'handoff']
const ui = Object.fromEntries(ids.map(id => [id, document.getElementById(id)]))
const statusLabels = { unreviewed: '待审阅', accepted: '本格人工通过', 'needs-fix': '需要修正' }
const tagLabels = { angle: '角度／透视', proportion: '人物比例', arms: '手臂／左右关系', legs: '迈腿／承重', cloth: '裙摆／头发／衣袖', alignment: '中心／脚底', loop: '前后格／首尾衔接', other: '其他' }
let state = { revision: 0, comparisons: [], clips: [], reviews: {} }
let comparison = null
let clip = null
let slots = []
let current = 0
let elapsed = 0
let playing = false
let finished = false
let reviewElapsed = null
let reviewFinished = false
let lastTime = performance.now()
let raf = 0
let disposed = false
let busy = false
let loading = false
let conflict = false
let loadTicket = 0
let images = new Map()
let imageErrors = new Map()
let pendingImages = new Set()
let frameButtons = []
let thumbnailCanvases = []
let shownReviewKey = ''
const drafts = new Map()
const overlayPreferences = new Map()
const pageController = new AbortController()
const downloadUrls = new Set()
const timers = new Set()
let catalogDraftsRestored = false

const clone = value => JSON.parse(JSON.stringify(value))
const mod = (n, d) => ((n % d) + d) % d
const fmt = n => Number.isFinite(n) ? Number(n.toFixed(5)).toString() : '—'
const notify = message => { ui.notice.textContent = message }
const clearError = () => { ui.error.hidden = true; ui.error.textContent = '' }
const showError = error => { ui.error.hidden = false; ui.error.textContent = error.message || String(error) }
const total = () => slots.reduce((sum, slot) => sum + slot.sample.durationMs, 0)
const reviewKey = slot => `${comparison.id}:${comparison.fingerprint}:${slot}`
const reviewFields = value => ({ status: value?.status || 'unreviewed', note: value?.note || '', tags: [...(value?.tags || [])].sort() })
const savedReview = slot => state.reviews?.[comparison.id]?.slots?.[String(slot)] || null
const applicableReview = slot => reviewFields(savedReview(slot)?.stale ? null : savedReview(slot))
const localReview = slot => drafts.get(reviewKey(slot)) || applicableReview(slot)
const isDirty = slot => JSON.stringify(localReview(slot)) !== JSON.stringify(applicableReview(slot))
const alignment = () => comparison.alignment
const degrees = () => comparison.planContract.plan.camera.degrees || 0
const sourceMetadata = () => comparison.planContract.source
const clearHandoff = () => { ui.handoff.hidden = true; ui.handoff.value = '' }
const relationship = item => item.metadata?.relationship || item.relationship || item.status || ''
const referenceIntent = item => ['rhythm', 'exact'].includes(item.metadata?.referenceIntent) ? item.metadata.referenceIntent : 'unspecified'
const referenceIntentText = item => ({
  rhythm: '阶段／节奏参考：骨架用于阶段、节奏和支撑参考，人物按自身比例、幅度与首尾连续性验收；关节差值仅用于诊断。',
  exact: '精确姿态转绘：按冻结方案严格核对姿态、关节位置与遮挡，隐藏或无法确认的关节仍需标明不确定。',
  unspecified: '参考精度按本次角色目标验收；本清单未指定阶段参考或精确姿态转绘，不自动要求逐点复现。'
})[referenceIntent(item)]

function sourceFrameLabel(sample) {
  if (sample.sourceFrameId !== null && sample.sourceFrameId !== undefined) return `${sample.sourceFrameId}（原生采样）`
  if (sample.contributors?.length) return `插值：${sample.contributors.map(item => `${item.sourceFrameId} × ${fmt(item.weight)}`).join(' + ')}`
  return '未提供原生帧贡献；不根据人物编号推断'
}

async function api(path, options) {
  const response = await studioFetch(path, { ...options, signal: pageController.signal })
  let body
  try { body = await response.json() } catch { throw new Error(`服务未返回 JSON（HTTP ${response.status}）`) }
  if (!response.ok) {
    const error = new Error(typeof body.error === 'string' ? body.error : body.error?.message || `HTTP ${response.status}`)
    error.status = response.status
    throw error
  }
  return body
}

const guarded = fn => async (...args) => {
  try { await fn(...args) } catch (error) { if (error.name !== 'AbortError') showError(error) }
}

function setLink(element, info, fallbackUrl) {
  const url = info?.available === false ? null : info?.url || fallbackUrl
  element.hidden = !url
  element.setAttribute('aria-disabled', String(!url))
  if (url) element.href = new URL(url, document.baseURI).href
  else element.removeAttribute('href')
}

function mapSlots() {
  const contract = comparison.planContract
  const errors = []
  if (!contract?.samples?.length || !contract.source?.names || !contract.source?.parents) throw new Error('对比缺少冻结的方案姿态或关节元数据。')
  if (!alignment()?.canvas || !alignment()?.origin || !(alignment()?.pixelsPerSourceUnit > 0)) throw new Error('对比缺少明确的共用显示配准。')
  return contract.samples.map((sample, slot) => {
    const mapping = comparison.frames.find(frame => frame.slot === slot)
    if (!mapping) errors.push(`第 ${slot + 1} 格缺少人物映射`)
    const frameIndex = clip.frames.findIndex(frame => frame.id === mapping?.artFrameId)
    const frame = clip.frames[frameIndex]
    const artStepSlot = mapping?.artStepSlot ?? slot
    // artStepSlot is an index in the clip's current playback array. step.slot
    // may retain an older source order after a studio reorder.
    const step = clip.steps[artStepSlot]
    if (!frame) errors.push(`第 ${slot + 1} 格未找到人物帧 ${mapping?.artFrameId || '—'}`)
    if (!step || clip.frames[step.frame]?.id !== frame?.id) errors.push(`第 ${slot + 1} 格的播放段与人物帧ID不一致`)
    if (!Number.isFinite(sample.durationMs) || sample.durationMs <= 0) throw new Error('方案姿态时长无效。')
    if (slot === contract.samples.length - 1 && errors.length) showError(new Error(errors.join('；')))
    return { sample, mapping, frame, step, slot }
  })
}

function relationLabel(item) {
  const value = relationship(item)
  if (value === 'historical-unmatched' || /historical|unmatched/.test(value)) return '历史人物帧 · 尚未按此目标骨架匹配'
  if (value === 'candidate-not-reviewed' || /candidate|unreviewed/.test(value)) return '新候选 · 待人工逐帧审阅'
  return value || '目标映射已配置 · 动作效果仍需审阅'
}

function renderPicker() {
  ui.comparison.replaceChildren(...state.comparisons.map(item => {
    const option = document.createElement('option')
    option.value = item.id
    option.textContent = `${item.name}${item.stale ? ' · 来源有变化' : ''}`
    return option
  }))
  if (comparison) ui.comparison.value = comparison.id
}

function renderIdentity() {
  if (!slots[current]) return
  const { sample, frame, mapping, step } = slots[current]
  const plan = comparison.planContract.plan
  const saved = savedReview(current)
  const review = localReview(current)
  ui['slot-badge'].textContent = `${current + 1} / ${slots.length}`
  const rows = [
    ['源索引', fmt(sample.sourceIndex)], ['源相位', Number.isFinite(sample.phase) ? `${fmt(sample.phase)} / ${(sample.phase * 100).toFixed(2)}%` : '—'],
    ['源时刻', `${fmt(sample.sourceTimeMs)} ms`], ['源帧贡献', sourceFrameLabel(sample)], ['目标停留', `${sample.durationMs} ms`],
    ['人物帧ID', frame?.id || mapping?.artFrameId || '未映射'], ['人物原停留', step ? `${step.durationMs} ms` : '—'],
    ['方案ID', plan.id], ['源／比例', `${plan.sourceId} / ${plan.profileId}`], ['相机', `${plan.camera.direction} / ${plan.camera.degrees}°`]
  ]
  ui.identity.replaceChildren(...rows.map(([name, value]) => {
    const row = document.createElement('div'), dt = document.createElement('dt'), dd = document.createElement('dd')
    dt.textContent = name; dd.textContent = String(value); row.append(dt, dd); return row
  }))
  setLink(ui['art-link'], mapping?.artSource, frame?.url)
  setLink(ui['master-link'], mapping?.masterSource)
  setLink(ui['sheet-link'], mapping?.sheetSource)
  ui['master-info'].textContent = mapping?.masterSource?.available ? `母版格：${mapping.masterSource.path}${mapping.masterSource.stale ? '（源字节已变化）' : ''}` : `母版格：${mapping?.masterSource?.reason || '本映射未提供可追溯的母版格。'}`
  ui['sheet-info'].hidden = !mapping?.sheetSource?.available
  ui['sheet-info'].textContent = mapping?.sheetSource?.available ? `整张母图：${mapping.sheetSource.path} · 裁格区域[x,y,w,h] ${mapping.sheetRect ? JSON.stringify(mapping.sheetRect) : '未提供'}${mapping.sheetSource.stale ? '（源字节已变化）' : ''}` : ''
  ui.provenance.textContent = JSON.stringify({
    projectId: state.projectId, projectPath: state.projectPath, comparisonId: comparison.id,
    relationship: relationship(comparison), comparisonMetadata: comparison.metadata, comparisonFingerprint: comparison.fingerprint,
    comparisonSha256: comparison.comparisonSha256, planId: plan.id,
    planContractPath: comparison.planContractPath, planContractSha256: comparison.planContractSha256,
    sourceFingerprint: plan.sourceFingerprint, sourceProvenance: comparison.planContract.source.provenance,
    slot: current, sourceIndex: sample.sourceIndex, contributors: sample.contributors,
    artSource: mapping?.artSource, masterSource: mapping?.masterSource, sheetSource: mapping?.sheetSource, sheetRect: mapping?.sheetRect,
    alignment: alignment(), savedHumanReview: saved
  }, null, 2)
  const key = reviewKey(current)
  if (shownReviewKey !== key) {
    clearHandoff()
    ui['review-status'].value = review.status
    ui.note.value = review.note
    for (const input of ui.tags.querySelectorAll('input')) input.checked = review.tags.includes(input.value)
    shownReviewKey = key
  }
  ui['art-status'].textContent = `${statusLabels[review.status] || review.status}${isDirty(current) ? ' · 未存' : ''}`
  ui['art-status'].dataset.status = review.status
  ui['review-dirty'].textContent = isDirty(current) ? '本地未保存' : saved?.stale ? '旧素材记录' : '已与保存记录一致'
  ui['review-dirty'].dataset.dirty = String(isDirty(current))
  ui['review-info'].textContent = `${saved?.updatedAt ? `保存于 ${saved.updatedAt}。` : '本格尚无已保存结论。'}${saved?.stale ? '此记录绑定旧素材指纹，不能沿用作当前通过依据。' : ''}${conflict ? '出现保存冲突，请刷新并核对；本地文字保留。' : ''}`
  ui['save-review'].disabled = busy || loading || conflict || !isDirty(current) || !!comparison.stale
  ui['reset-review'].disabled = busy || !isDirty(current)
  ui.seek.max = slots.length - 1
  ui.seek.value = current
  ui['review-wrap'].disabled = !plan.loop || slots.length < 2 || loading
  ui['previous'].disabled = !slots.length
  ui.next.disabled = !slots.length
  ui['dirty-count'].textContent = drafts.size ? `${drafts.size} 格保有本地编辑（可下载）` : '已同步记录'
  frameButtons.forEach((button, i) => {
    button.setAttribute('aria-pressed', String(i === current))
    button.dataset.review = localReview(i).status
    button.dataset.dirty = String(isDirty(i))
  })
}

function renderThumbnails() {
  ui.frames.replaceChildren()
  frameButtons = []
  thumbnailCanvases = []
  for (const item of slots) {
    const button = document.createElement('button')
    button.className = 'frame-button'
    button.setAttribute('aria-label', `第 ${item.slot + 1} 格，骨架与人物并排`)
    const canvas = document.createElement('canvas')
    canvas.width = 180; canvas.height = 172
    const label = document.createElement('span')
    label.textContent = `${String(item.slot + 1).padStart(2, '0')} · #${fmt(item.sample.sourceIndex)} · ${item.sample.durationMs}ms`
    button.append(canvas, label)
    button.onclick = () => seek(item.slot)
    ui.frames.append(button)
    frameButtons.push(button)
    thumbnailCanvases.push(canvas)
  }
  drawThumbnails()
  renderIdentity()
}

function drawMissing(view, message) {
  view.ctx.save(); view.ctx.fillStyle = '#a67c60'; view.ctx.textAlign = 'center'; view.ctx.font = '12px system-ui,sans-serif'; view.ctx.fillText(message, view.width / 2, view.height / 2); view.ctx.restore()
}

function drawThumbnails() {
  if (!comparison) return
  slots.forEach((item, i) => {
    const canvas = thumbnailCanvases[i]
    if (!canvas) return
    // Each thumbnail uses two fixed copies of the same logical canvas.
    const view = createComparisonView(canvas, [alignment().canvas[0] * 2, alignment().canvas[1]], 'light', 6)
    drawComparisonSkeleton(view, item.sample, sourceMetadata(), alignment(), degrees(), { opacity: .95 })
    const shift = { ...view, offset: [view.offset[0] + alignment().canvas[0] * view.zoom, view.offset[1]], map: p => [view.offset[0] + (p[0] + alignment().canvas[0]) * view.zoom, view.offset[1] + p[1] * view.zoom] }
    drawArtwork(shift, item.frame, item.step, images.get(item.frame?.url), clip.canvas)
    if (ui.guides.checked) { drawComparisonGuides(view, alignment()); drawComparisonGuides(shift, alignment()) }
  })
}

function draw() {
  if (!comparison || !slots[current]) return
  const item = slots[current]
  const background = ui.background.value
  const left = createComparisonView(ui.target, alignment().canvas, background)
  const right = createComparisonView(ui.art, alignment().canvas, background)
  const ghost = current > 0 ? current - 1 : comparison.planContract.plan.loop ? slots.length - 1 : null
  if (ui.ghost.checked && ghost !== null) {
    const previous = slots[ghost]
    drawComparisonSkeleton(left, previous.sample, sourceMetadata(), alignment(), degrees(), { opacity: .15 })
    drawArtwork(right, previous.frame, previous.step, images.get(previous.frame?.url), clip.canvas, .18)
  }
  drawComparisonSkeleton(left, item.sample, sourceMetadata(), alignment(), degrees(), { labels: ui.labels.checked })
  const shown = drawArtwork(right, item.frame, item.step, images.get(item.frame?.url), clip.canvas)
  if (!shown) drawMissing(right, loading ? '人物图片载入中…' : imageErrors.get(item.frame?.url) ? '人物图读取失败，仍可查看来源与记录' : '此格缺少人物图片')
  if (ui.overlay.checked) drawComparisonSkeleton(right, item.sample, sourceMetadata(), alignment(), degrees(), { opacity: Number(ui.opacity.value), labels: ui.labels.checked })
  if (ui.guides.checked) { drawComparisonGuides(left, alignment()); drawComparisonGuides(right, alignment()) }
  if (ui.markers.checked) drawArtMarkers(right, item.frame, item.step)
  ui['target-readout'].textContent = `第 ${current + 1}/${slots.length} 格 · 源 #${fmt(item.sample.sourceIndex)} · phase ${fmt(item.sample.phase)} · ${item.sample.durationMs}ms`
  ui['art-readout'].textContent = `人物 ${item.frame?.id || '未映射'} · 目标格 ${current + 1}${ui.overlay.checked ? ' · 彩色骨架为目标叠加' : ''}`
  ui.play.textContent = playing ? '暂停' : finished ? '重播' : '播放'
  ui['review-wrap'].textContent = reviewElapsed !== null ? '退出首尾审查' : reviewFinished ? '重播首尾片段' : '单次末→首审查'
  ui['playback-info'].textContent = `${slots.length} 格 · ${total()} ms${reviewElapsed !== null ? ' · 首尾审查中' : reviewFinished ? ' · 首尾审查已暂停' : ''}`
}

function seek(slot) {
  if (!slots.length) return
  playing = false; finished = false; reviewElapsed = null; reviewFinished = false
  current = Math.max(0, Math.min(slots.length - 1, slot))
  elapsed = slots.slice(0, current).reduce((sum, item) => sum + item.sample.durationMs, 0)
  shownReviewKey = ''
  clearHandoff()
  renderIdentity(); draw()
}

function currentAtTime(time) {
  let cursor = 0
  for (let i = 0; i < slots.length; i++) {
    cursor += slots[i].sample.durationMs
    if (time < cursor) return i
  }
  return slots.length - 1
}

function tick(now) {
  if (disposed) return
  const delta = Math.max(0, Math.min(now - lastTime, 100)) * Number(ui.speed.value)
  lastTime = now
  if (comparison && playing && !loading && !document.hidden) {
    if (reviewElapsed !== null) {
      reviewElapsed += delta
      const window = reviewWindowState(slots.map(item => item.sample.durationMs), reviewElapsed)
      elapsed = window.planTimeMs
      if (window.done) { reviewElapsed = null; reviewFinished = true; playing = false; notify('首尾片段已结束，两侧停在同一目标格；此处是审查暂停。') }
    } else {
      elapsed += delta
      if (elapsed >= total()) {
        if (comparison.planContract.plan.loop) elapsed = mod(elapsed, total())
        else { elapsed = total() - .0001; playing = false; finished = true }
      }
    }
    const next = currentAtTime(elapsed)
    if (next !== current) { current = next; shownReviewKey = ''; renderIdentity() }
  }
  draw()
  raf = requestAnimationFrame(tick)
}

async function chooseComparison(id) {
  const next = state.comparisons.find(item => item.id === id)
  if (!next) throw new Error('找不到此对比清单。')
  const found = state.clips.find(item => item.id === next.clipId)
  if (!found) throw new Error(`对比引用的动作不可用：${next.clipId}`)
  const ticket = ++loadTicket
  for (const image of pendingImages) image.src = ''
  pendingImages.clear()
  if (comparison) overlayPreferences.set(comparison.id, ui.overlay.checked)
  ui.overlay.checked = overlayPreferences.get(next.id) ?? referenceIntent(next) !== 'rhythm'
  comparison = next; clip = found
  for (const frame of clip.frames) frame.url = new URL(frame.url, document.baseURI).href
  images = new Map(); imageErrors = new Map(); loading = true; conflict = false
  current = 0; elapsed = 0; playing = false; finished = false; reviewElapsed = null; reviewFinished = false
  shownReviewKey = ''
  clearHandoff()
  clearError()
  slots = []
  try { slots = mapSlots() }
  catch (error) {
    loading = false
    ui.target.width = ui.target.width
    ui.art.width = ui.art.width
    ui.frames.replaceChildren(); ui.identity.replaceChildren()
    ui['target-readout'].textContent = '当前对比映射不可用'
    ui['art-readout'].textContent = '未显示旧帧，以免误认当前映射'
    ui['art-link'].hidden = true; ui['master-link'].hidden = true; ui['sheet-link'].hidden = true; ui['sheet-info'].hidden = true
    throw error
  }
  ui.comparison.value = id
  ui.relationship.textContent = `${relationLabel(comparison)}${comparison.stale ? ' · 素材指纹已变化' : ''}`
  ui['reference-intent'].textContent = { rhythm: '节奏参考', exact: '精确姿态', unspecified: '参考精度未指定' }[referenceIntent(comparison)]
  const a = alignment()
  ui.alignment.textContent = `共用逻辑画布 ${a.canvas.join('×')} · 比较中轴 x=${a.origin[0]}／脚线 y=${a.origin[1]} · ${a.pixelsPerSourceUnit} px/源单位；${a.method || '人工显示配准'}。仅用于观察，不自动校正人物关节。`
  ui.count.textContent = `${slots.length} 个明确映射格`
  renderThumbnails()
  if (comparison.stale) showError(new Error('对比的来源或素材字节已变化。当前显示冻结的目标快照；不能沿用旧通过结论，保存新结论前需重新核对配置。'))
  notify('')
  const url = new URL(location.href); url.searchParams.set('id', id); history.replaceState(null, '', url)
  const urls = [...new Set(slots.map(item => item.frame?.url).filter(Boolean))]
  await Promise.allSettled(urls.map(async url => {
    const image = new Image()
    pendingImages.add(image)
    try { image.src = url; await image.decode(); if (!disposed && ticket === loadTicket) images.set(url, image) }
    catch (error) { if (!disposed && ticket === loadTicket) imageErrors.set(url, error.message || '图片读取失败') }
    finally { pendingImages.delete(image) }
  }))
  if (disposed || ticket !== loadTicket) return
  loading = false
  renderIdentity(); drawThumbnails(); draw()
  if (imageErrors.size) showError(new Error(`${imageErrors.size} 张人物图片未能读取；骨架、来源和人工记录仍可使用。`))
}

async function loadState(preferredId) {
  busy = true
  ui.refresh.disabled = true
  try {
    const value = await api('/api/comparison')
    if (disposed) return
    state = value
    restoreCatalogDrafts()
    renderPicker()
    const requested = preferredId || new URLSearchParams(location.search).get('id')
    const initial = state.comparisons.find(item => item.id === requested) || state.comparisons.find(item => !item.stale) || state.comparisons[0]
    if (!initial) throw new Error('项目尚未配置骨架与人物的明确对比映射。')
    await chooseComparison(initial.id)
  } finally { busy = false; ui.refresh.disabled = false; if (comparison) renderIdentity() }
}

const catalogDraftKey = () => `character-motion-comparison-drafts:${state.projectId}:${state.projectPath || ''}`
function restoreCatalogDrafts() {
  if (catalogDraftsRestored) return
  catalogDraftsRestored = true
  try {
    const saved = JSON.parse(localStorage.getItem(catalogDraftKey()))
    if (saved?.version !== 1 || saved.projectId !== state.projectId || !Array.isArray(saved.drafts)) return
    for (const [key, value] of saved.drafts) {
      if (typeof key !== 'string' || !state.comparisons.some(item => key.startsWith(`${item.id}:${item.fingerprint}:`))) continue
      if (!Object.hasOwn(statusLabels, value?.status) || typeof value.note !== 'string' || value.note.length > 4000 || !Array.isArray(value.tags) || value.tags.some(tag => !Object.hasOwn(tagLabels, tag))) continue
      drafts.set(key, reviewFields(value))
    }
    if (drafts.size) notify(`已恢复 ${drafts.size} 格本机人工记录草稿；尚未写入项目，保存前请核对当前素材。`)
  } catch { /* Invalid local drafts cannot replace project records. */ }
}
function catalogPending() {
  if (!drafts.size) return null
  return {
    message: `当前对比页有 ${drafts.size} 格本地编辑。保留本机草稿后再打开；返回同一工程时自动恢复为未保存记录，不会代替项目中的人工结论。`,
    preserve: () => localStorage.setItem(catalogDraftKey(), JSON.stringify({ version: 1, projectId: state.projectId, revision: state.revision, drafts: [...drafts] }))
  }
}
function syncPreservedCatalogDrafts() {
  try {
    if (!localStorage.getItem(catalogDraftKey())) return
    if (drafts.size) localStorage.setItem(catalogDraftKey(), JSON.stringify({ version: 1, projectId: state.projectId, revision: state.revision, drafts: [...drafts] }))
    else localStorage.removeItem(catalogDraftKey())
  } catch { notify('项目记录未受影响；本机草稿无法更新，请在离开前下载人工问题 JSON。') }
}

function captureReview() {
  if (!comparison) return
  playing = false
  const value = { status: ui['review-status'].value, note: ui.note.value, tags: [...ui.tags.querySelectorAll('input:checked')].map(input => input.value).sort() }
  if (JSON.stringify(value) === JSON.stringify(applicableReview(current))) drafts.delete(reviewKey(current))
  else drafts.set(reviewKey(current), value)
  syncPreservedCatalogDrafts()
  clearHandoff()
  renderIdentity()
}

ui.comparison.onchange = guarded(async () => { await chooseComparison(ui.comparison.value) })
ui.refresh.onclick = guarded(async () => { await loadState(comparison?.id) })
ui.previous.onclick = () => seek(mod(current - 1, slots.length))
ui.next.onclick = () => seek(mod(current + 1, slots.length))
ui.seek.oninput = () => seek(Number(ui.seek.value))
ui.play.onclick = () => {
  if (!comparison || loading) return
  if (finished) { current = 0; elapsed = 0; finished = false }
  reviewFinished = false
  playing = !playing
}
ui.restart.onclick = () => { seek(0); playing = true }
ui['review-wrap'].onclick = () => {
  if (!comparison || slots.length < 2 || !comparison.planContract.plan.loop) return
  if (reviewElapsed !== null) { reviewElapsed = null; notify('已退出首尾片段审查，继续完整方案。'); return }
  reviewElapsed = 0; reviewFinished = false; finished = false
  const window = reviewWindowState(slots.map(item => item.sample.durationMs), 0)
  elapsed = window.planTimeMs; current = window.slot
  ui.speed.value = '0.5'; playing = true; shownReviewKey = ''
  renderIdentity()
  notify('单次查看最后两格→最前两格；进入第2格后暂停，两侧始终是同一目标格。')
}
for (const id of ['overlay', 'opacity', 'guides', 'ghost', 'labels', 'markers', 'background']) ui[id].oninput = () => { if (id === 'overlay' && comparison) overlayPreferences.set(comparison.id, ui.overlay.checked); draw(); if (id === 'guides') drawThumbnails() }
ui.note.onfocus = () => { playing = false }
ui.note.oninput = captureReview
ui['review-status'].onfocus = () => { playing = false }
ui['review-status'].onchange = captureReview
for (const input of ui.tags.querySelectorAll('input')) { input.onfocus = () => { playing = false }; input.onchange = captureReview }
ui['reset-review'].onclick = () => { drafts.delete(reviewKey(current)); syncPreservedCatalogDrafts(); shownReviewKey = ''; renderIdentity() }
ui['save-review'].onclick = guarded(async () => {
  if (!comparison || comparison.stale) throw new Error('请先核对来源变化，不能保存当前对比为通过。')
  playing = false; busy = true; clearError(); renderIdentity()
  const id = comparison.id, fingerprint = comparison.fingerprint, slot = current, key = reviewKey(slot)
  const value = clone(localReview(slot))
  try {
    const result = await api('/api/comparison/review', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ expectedRevision: state.revision, comparisonId: id, comparisonFingerprint: fingerprint, slot, ...value }) })
    if (disposed) return
    state.revision = result.revision; state.reviews = result.reviews
    if (JSON.stringify(drafts.get(key)) === JSON.stringify(value)) drafts.delete(key)
    syncPreservedCatalogDrafts()
    if (comparison.id === id && comparison.fingerprint === fingerprint) { shownReviewKey = ''; renderIdentity() }
    notify(`第 ${slot + 1} 格人工记录已保存；未生成或修改图片。`)
  } catch (error) {
    if (error.status === 409) { conflict = true; error.message += '。人工文字仍在本地，请刷新素材与记录后核对。' }
    throw error
  } finally { busy = false; if (!disposed) renderIdentity() }
})

function issueDocument() {
  return { version: 1, kind: 'character-motion-manual-comparison-review', projectId: state.projectId, projectPath: state.projectPath, revision: state.revision,
    comparisonId: comparison.id, comparisonFingerprint: comparison.fingerprint, planId: comparison.planContract.plan.id,
    planContractPath: comparison.planContractPath, planContractSha256: comparison.planContractSha256,
    sourceFingerprint: comparison.planContract.plan.sourceFingerprint,
    frames: slots.map((item, slot) => ({ slot, artFrameId: item.frame?.id, artSource: item.mapping?.artSource, masterSource: item.mapping?.masterSource, sheetSource: item.mapping?.sheetSource, sheetRect: item.mapping?.sheetRect, sourceIndex: item.sample.sourceIndex, phase: item.sample.phase, durationMs: item.sample.durationMs, savedReview: savedReview(slot), localReview: localReview(slot), unsaved: isDirty(slot) })),
    otherLocalDrafts: [...drafts].filter(([key]) => !key.startsWith(`${comparison.id}:${comparison.fingerprint}:`)).map(([key, value]) => ({ key, value, unsaved: true })),
    limitations: ['Human observations only. Target skeleton is not detected artwork anatomy.', 'Individual pose acceptance does not approve the entire animation or loop.'] }
}

ui.download.onclick = () => {
  if (!comparison) return
  const url = URL.createObjectURL(new Blob([JSON.stringify(issueDocument(), null, 2) + '\n'], { type: 'application/json' }))
  downloadUrls.add(url)
  const a = document.createElement('a'); a.href = url; a.download = `${comparison.id}.manual-review.json`; a.click()
  const timer = setTimeout(() => { URL.revokeObjectURL(url); downloadUrls.delete(url); timers.delete(timer) }, 1000)
  timers.add(timer)
  notify('已下载当前全部格的记录；本地未保存内容在 JSON 中明确标注。')
}
ui.copy.onclick = guarded(async () => {
  if (!comparison) return
  playing = false
  const item = slots[current], review = localReview(current), plan = comparison.planContract.plan
  const sheet = item.mapping?.sheetSource?.path ? `整张生成母图 ${item.mapping.sheetSource.path}，本格裁格区域[x,y,w,h] ${item.mapping.sheetRect ? JSON.stringify(item.mapping.sheetRect) : '未提供，不推测'}。` : ''
  const text = `请在项目 ${state.projectPath || state.projectId} 修正对比 ${comparison.id} 的第 ${current + 1} 格：人物帧 ${item.frame?.id || item.mapping?.artFrameId}，母版格 ${item.mapping?.masterSource?.path || '请从对比清单追溯'}。${sheet}参考意图 referenceIntent=${referenceIntent(comparison)}。${referenceIntentText(comparison)} 目标方案 ${plan.id}，快照 ${comparison.planContractPath}（SHA256 ${comparison.planContractSha256}），源索引 ${fmt(item.sample.sourceIndex)}、phase ${fmt(item.sample.phase)}、停留 ${item.sample.durationMs}ms。人工问题：${review.tags.map(tag => tagLabels[tag]).join('、') || '待进一步定位'}；${review.note || '请逐格对照骨架和人物后记录具体问题。'} 保持相机、人物比例、解剖左右与共用坐标；修改正确完整母图后接回本共享对比页，检查前后格和首尾。彩色骨架只是目标参考，不代表人物关节已经识别。${isDirty(current) ? '此说明包含尚未保存的本地人工记录。' : ''}`
  ui.handoff.value = text; ui.handoff.hidden = false
  try { await navigator.clipboard.writeText(text); notify('已复制本格修正说明；不会自动生图。') }
  catch { ui.handoff.focus(); ui.handoff.select(); notify('未获浏览器自动复制权限；已选中完整修正说明。') }
})

window.addEventListener('pagehide', () => {
  disposed = true; loadTicket++; pageController.abort(); cancelAnimationFrame(raf)
  for (const image of pendingImages) image.src = ''
  pendingImages.clear(); images.clear()
  for (const timer of timers) clearTimeout(timer)
  for (const url of downloadUrls) URL.revokeObjectURL(url)
  timers.clear(); downloadUrls.clear()
})
window.addEventListener('pageshow', event => { if (event.persisted) location.reload() })

raf = requestAnimationFrame(tick)
window.motionStudioBridge = { pending: catalogPending, pause: () => { playing = false } }
loadState().catch(error => { if (error.name !== 'AbortError') showError(error) })
