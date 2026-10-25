import { projectPoint } from './skeleton-preview.mjs'

const colors = { Left: '#b96a51', Right: '#247fa3', Center: '#4c6268' }
const side = name => /^Left|_l$|\.L$/i.test(name) ? 'Left' : /^Right|_r$|\.R$/i.test(name) ? 'Right' : 'Center'
const finger = /(?:finger|thumb|index|middle|pinky|ring)(?:\b|_|\d)/i

export function logicalSkeletonPoint(point, alignment, degrees = 0) {
  const [x, y] = projectPoint(point, degrees)
  return [alignment.origin[0] + (x - (alignment.sourceOriginX || 0)) * alignment.pixelsPerSourceUnit,
    alignment.origin[1] + (y + (alignment.sourceFloorY || 0)) * alignment.pixelsPerSourceUnit]
}

export function createComparisonView(canvas, logicalCanvas, background = 'light', padding = 20) {
  const ctx = canvas.getContext('2d')
  const box = canvas.getBoundingClientRect()
  const width = box.width || canvas.width || 480
  const height = box.height || canvas.height || 480
  const dpr = globalThis.devicePixelRatio || 1
  const size = [Math.max(1, Math.round(width * dpr)), Math.max(1, Math.round(height * dpr))]
  if (canvas.width !== size[0]) canvas.width = size[0]
  if (canvas.height !== size[1]) canvas.height = size[1]
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.clearRect(0, 0, width, height)
  ctx.fillStyle = background === 'dark' ? '#17262c' : '#eef1e9'
  ctx.fillRect(0, 0, width, height)
  if (background === 'checker') {
    ctx.fillStyle = '#d4ded4'
    for (let y = 0; y < height; y += 20) for (let x = 0; x < width; x += 20) if ((x / 20 + y / 20) % 2 === 0) ctx.fillRect(x, y, 20, 20)
  }
  const zoom = Math.max(.001, Math.min((width - padding * 2) / logicalCanvas[0], (height - padding * 2) / logicalCanvas[1]))
  const offset = [(width - logicalCanvas[0] * zoom) / 2, (height - logicalCanvas[1] * zoom) / 2]
  return { ctx, width, height, zoom, offset, map: p => [offset[0] + p[0] * zoom, offset[1] + p[1] * zoom] }
}

export function drawArtwork(view, frame, step, image, logicalCanvas, opacity = 1) {
  if (!image || !frame) return false
  const [x, y, x2, y2] = frame.rect
  const [dx, dy, dw, dh] = frame.drawRect || [0, 0, ...logicalCanvas]
  const offset = step?.offset || [0, 0]
  const [left, top] = view.map([dx + offset[0], dy + offset[1]])
  view.ctx.save()
  view.ctx.globalAlpha = opacity
  view.ctx.drawImage(image, x, y, x2 - x, y2 - y, left, top, dw * view.zoom, dh * view.zoom)
  view.ctx.restore()
  return true
}

export function drawComparisonSkeleton(view, sample, metadata, alignment, degrees, { opacity = 1, labels = false } = {}) {
  const pose = sample?.positions
  if (!pose || !metadata?.names || !metadata?.parents) return
  const { ctx } = view
  const points = pose.map(p => view.map(logicalSkeletonPoint(p, alignment, degrees)))
  const depth = name => side(name) === 'Right' ? Math.cos(degrees * Math.PI / 180) : side(name) === 'Left' ? -Math.cos(degrees * Math.PI / 180) : 0
  const edges = metadata.names.map((name, i) => i).filter(i => metadata.parents[i] >= 0 && !finger.test(metadata.names[i]) && !/^(root|Body)$/i.test(metadata.names[metadata.parents[i]]))
  edges.sort((a, b) => depth(metadata.names[a]) - depth(metadata.names[b]))
  ctx.save()
  ctx.globalAlpha = opacity
  ctx.lineCap = 'round'
  ctx.lineJoin = 'round'
  for (const i of edges) {
    const parent = metadata.parents[i]
    ctx.strokeStyle = colors[side(metadata.names[i])]
    ctx.lineWidth = Math.max(1.5, view.zoom * 5)
    ctx.beginPath(); ctx.moveTo(...points[parent]); ctx.lineTo(...points[i]); ctx.stroke()
  }
  for (let i = 0; i < points.length; i++) {
    if (finger.test(metadata.names[i]) || /^(root|Body)$/i.test(metadata.names[i])) continue
    const [x, y] = points[i]
    const tip = /Wrist|Ankle|ToeEnd|hand_[lr]$|foot_[lr]$|ball_leaf_[lr]$/i.test(metadata.names[i])
    ctx.fillStyle = colors[side(metadata.names[i])]
    ctx.strokeStyle = '#f4f4e8'
    ctx.lineWidth = .8
    ctx.beginPath(); ctx.arc(x, y, Math.max(1.8, view.zoom * (tip ? 6 : 4)), 0, Math.PI * 2); ctx.fill(); ctx.stroke()
    if (labels) { ctx.font = '10px system-ui,sans-serif'; ctx.fillText(metadata.names[i], x + 5, y - 4) }
  }
  const head = metadata.names.findIndex(name => /^Head$/i.test(name))
  if (head >= 0) {
    const tip = metadata.names.findIndex((name, i) => metadata.parents[i] === head && /^Head(?:End|_?end|_?leaf)$/i.test(name))
    const parent = metadata.parents[head]
    const radius = tip >= 0 ? Math.hypot(...pose[tip].map((v, i) => v - pose[head][i])) * .5 : parent >= 0 ? Math.hypot(...pose[parent].map((v, i) => v - pose[head][i])) * 1.15 : 8
    const center = tip >= 0 ? pose[head].map((v, i) => (v + pose[tip][i]) / 2) : [pose[head][0], pose[head][1] + radius * .6, pose[head][2]]
    const xy = view.map(logicalSkeletonPoint(center, alignment, degrees))
    ctx.strokeStyle = colors.Center; ctx.lineWidth = Math.max(1, view.zoom * 3)
    ctx.beginPath(); ctx.ellipse(...xy, radius * .73 * alignment.pixelsPerSourceUnit * view.zoom, radius * alignment.pixelsPerSourceUnit * view.zoom, 0, 0, Math.PI * 2); ctx.stroke()
  }
  ctx.restore()
}

export function drawComparisonGuides(view, alignment) {
  const [x, y] = view.map(alignment.origin)
  const { ctx, width, height } = view
  ctx.save(); ctx.strokeStyle = '#97703e'; ctx.lineWidth = 1; ctx.setLineDash([5, 5])
  ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, height); ctx.moveTo(0, y); ctx.lineTo(width, y); ctx.stroke()
  ctx.setLineDash([]); ctx.strokeRect(x - 4, y - 4, 8, 8); ctx.restore()
}

export function drawArtMarkers(view, frame, step) {
  const { ctx } = view
  const offset = step?.offset || [0, 0]
  ctx.save()
  for (const [name, p] of Object.entries(frame?.markers || {})) {
    const [x, y] = view.map([p[0] + offset[0], p[1] + offset[1]])
    ctx.strokeStyle = '#986a25'; ctx.lineWidth = 1.5
    ctx.strokeRect(x - 4, y - 4, 8, 8)
    ctx.font = '10px system-ui,sans-serif'; ctx.fillStyle = '#81531b'; ctx.fillText(name, x + 7, y - 4)
  }
  ctx.restore()
}
