/** Shared, timer-free orthographic skeleton renderer for motion sampling plans.
 * The caller owns playback and supplies an already in-place, normalized source.
 */

const TAU = Math.PI * 2
const COLORS = { left: '#b56d58', right: '#287fa0', center: '#475b62' }
const FINGER = /(?:finger|thumb|index|middle|pinky|ring)(?:\b|_|\d)/i
const finite = (value, fallback) => Number.isFinite(value) ? value : fallback
const clamp = (value, low, high) => Math.max(low, Math.min(high, value))

/** Anatomical side; it never means the left/right half of the image. */
function sideOf(name) {
  if (/^Left|_l$|\.L$/i.test(name)) return 'left'
  if (/^Right|_r$|\.R$/i.test(name)) return 'right'
  return 'center'
}

/** Strict orthographic E=0 projection, without a depth contribution to height. */
export function projectPoint(point, directionDegrees = 0) {
  const angle = finite(directionDegrees, 0) * Math.PI / 180
  return [point[2] * Math.cos(angle) + point[0] * Math.sin(angle), -point[1]]
}

function assertSource(source) {
  if (!source || !Array.isArray(source.positions) || !source.positions.length ||
      !Array.isArray(source.timesMs) || source.timesMs.length !== source.positions.length ||
      !Array.isArray(source.names) || !Array.isArray(source.parents) ||
      source.names.length !== source.parents.length || !source.names.length) {
    throw new Error('Expected normalized skeleton names, parents, positions and timesMs')
  }
  let previous = -Infinity
  for (const time of source.timesMs) {
    if (!Number.isFinite(time) || time <= previous) throw new Error('Source timesMs must strictly increase')
    previous = time
  }
  for (const [index, frame] of source.positions.entries()) {
    if (!Array.isArray(frame) || frame.length !== source.names.length || frame.some(point =>
      !Array.isArray(point) || point.length !== 3 || point.some(value => !Number.isFinite(value)))) {
      throw new Error(`Invalid skeleton positions at frame ${index}`)
    }
  }
  if (source.parents.some((parent, index) => !Number.isInteger(parent) || parent < -1 ||
      parent >= source.names.length || parent === index)) throw new Error('Invalid skeleton joint parent')
}

/**
 * Return [joint][x,y,z] at an exact/fractional source frame or a time in milliseconds.
 * Fractional frameIndex always interpolates; interpolate controls time sampling only.
 * A loop with an endpoint uses that endpoint approaching the cycle boundary;
 * an endpoint-exclusive loop interpolates its last pose toward its first pose.
 * At the boundary, time playback starts the next cycle without a duplicate hold.
 */
export function samplePose(source, { frameIndex = 0, timeMs, interpolate = false } = {}) {
  const frames = source.positions
  const times = source.timesMs
  if (!frames?.length || !times?.length) throw new Error('Cannot sample an empty skeleton source')
  if (!Number.isFinite(timeMs)) {
    const requested = finite(frameIndex, 0)
    const index = source.loop && !source.includesEndpoint
      ? ((requested % frames.length) + frames.length) % frames.length
      : clamp(requested, 0, frames.length - 1)
    const previous = Math.floor(index)
    const weight = index - previous
    if (!weight) return frames[previous]
    const next = (previous + 1) % frames.length
    return frames[previous].map((point, joint) => point.map((value, axis) => value + (frames[next][joint][axis] - value) * weight))
  }
  if (frames.length === 1) return frames[0]
  const first = times[0]
  const last = times.at(-1)
  const duration = finite(source.durationMs, last - first)
  const end = first + Math.max(0, duration)
  let time = timeMs
  if (source.loop && duration > 0) time = first + ((time - first) % duration + duration) % duration
  else time = clamp(time, first, last)
  if (time <= first) return frames[0]
  let low = 0
  let high = times.length - 1
  while (low + 1 < high) {
    const middle = (low + high) >> 1
    if (times[middle] <= time) low = middle
    else high = middle
  }
  if (time >= last) {
    if (!source.loop || source.includesEndpoint || !(end > last) || !interpolate) return frames.at(-1)
    const weight = clamp((time - last) / (end - last), 0, 1)
    return frames.at(-1).map((point, joint) => point.map((value, axis) => value + (frames[0][joint][axis] - value) * weight))
  }
  if (!interpolate || time === times[low]) return frames[low]
  const weight = (time - times[low]) / (times[high] - times[low])
  return frames[low].map((point, joint) => point.map((value, axis) => value + (frames[high][joint][axis] - value) * weight))
}

function headGeometry(source, pose, options) {
  const names = source.names
  const head = Number.isInteger(options.headIndex) ? options.headIndex : names.findIndex(name => /^Head$/i.test(name))
  if (head < 0 || !pose[head]) return null
  const tip = names.findIndex((name, index) => source.parents[index] === head && /^Head(?:End|_?end|_?leaf)$/i.test(name))
  if (tip >= 0) {
    const base = pose[head]
    const end = pose[tip]
    const distance = Math.hypot(...end.map((value, axis) => value - base[axis]))
    if (distance > 0) return {
      center: base.map((value, axis) => (value + end[axis]) / 2),
      radiusY: distance / 2,
      radiusX: distance * .36,
      head,
      end
    }
  }
  const parent = source.parents[head]
  const neckLength = parent >= 0 ? Math.hypot(...pose[head].map((value, axis) => value - pose[parent][axis])) : 0
  const radius = Math.max(.001, finite(options.headRadius, neckLength * 1.15 || 1))
  return { center: [pose[head][0], pose[head][1] + radius * .6, pose[head][2]], radiusY: radius, radiusX: radius * .8, head }
}

function projectedBounds(source, directionDegrees, visible, options) {
  const bounds = [Infinity, Infinity, -Infinity, -Infinity]
  let minimumY = Infinity
  function include(point, rx = 0, ry = 0) {
    const [x, y] = projectPoint(point, directionDegrees)
    bounds[0] = Math.min(bounds[0], x - rx)
    bounds[1] = Math.min(bounds[1], y - ry)
    bounds[2] = Math.max(bounds[2], x + rx)
    bounds[3] = Math.max(bounds[3], y + ry)
  }
  for (const pose of source.positions) {
    for (const index of visible) {
      include(pose[index])
      minimumY = Math.min(minimumY, pose[index][1])
    }
    const head = headGeometry(source, pose, options)
    if (head) include(head.center, head.radiusX * 1.5, head.radiusY)
  }
  const floorY = finite(options.floorY, Math.min(0, minimumY))
  include([0, floorY, 0])
  return { bounds, floorY }
}

/**
 * Options: padding (CSS px), background, headRadius (source units), headIndex,
 * floorY (source units), showAxes (default true), showFingers (default false).
 * No animation frames, observers or event handlers are installed by this module.
 */
export function createSkeletonRenderer(canvas, source, options = {}) {
  assertSource(source)
  const ctx = canvas.getContext('2d')
  const fallbackWidth = Math.max(1, finite(options.width, canvas.width || 480))
  const fallbackHeight = Math.max(1, finite(options.height, canvas.height || 480))
  if (!ctx) throw new Error('A 2D canvas context is required')
  const visible = source.names.map((name, index) => options.showFingers || !FINGER.test(name) ? index : -1).filter(index => index >= 0)
  const visibleSet = new Set(visible)
  const edges = visible.filter(index => source.parents[index] >= 0 && visibleSet.has(source.parents[index]) &&
    !/^(root|Body)$/i.test(source.names[source.parents[index]]))
  const sides = source.names.map(sideOf)
  const tips = visible.filter(index => /(?:Wrist|Ankle|ToeEnd|hand_[lr]|foot_[lr]|ball_leaf_[lr]|IK(?:Front|Back)Leg\.[LR])$/i.test(source.names[index]))
  const fitCache = new Map()
  let disposed = false

  function fit(direction, width, height) {
    const key = String(direction)
    if (!fitCache.has(key)) fitCache.set(key, projectedBounds(source, direction, visible, options))
    const raw = fitCache.get(key)
    const [minX, minY, maxX, maxY] = raw.bounds
    const padding = Math.min(width / 4, height / 4, Math.max(8, finite(options.padding, 34)))
    const scale = Math.max(.0001, Math.min((width - 2 * padding) / Math.max(.001, maxX - minX),
      (height - 2 * padding) / Math.max(.001, maxY - minY)))
    const centerX = (minX + maxX) / 2
    const centerY = (minY + maxY) / 2
    return { ...raw, scale, project: point => {
      const [x, y] = projectPoint(point, direction)
      return [width / 2 + (x - centerX) * scale, height / 2 + (y - centerY) * scale]
    } }
  }

  function strokePose(pose, camera, direction, alpha, labels) {
    const project = camera.project
    const angle = direction * Math.PI / 180
    // Far anatomical side is drawn first. E places right/blue nearest the viewer.
    const sideDepth = side => side === 'right' ? Math.cos(angle) : side === 'left' ? -Math.cos(angle) : 0
    const ordered = [...edges].sort((a, b) => sideDepth(sides[a]) - sideDepth(sides[b]))
    ctx.save()
    ctx.globalAlpha = alpha
    ctx.lineCap = 'round'
    ctx.lineJoin = 'round'
    for (const index of ordered) {
      const a = project(pose[source.parents[index]])
      const b = project(pose[index])
      ctx.strokeStyle = COLORS[sides[index]]
      ctx.lineWidth = sides[index] === 'center' ? 3.8 : 4.2
      ctx.beginPath()
      ctx.moveTo(...a)
      ctx.lineTo(...b)
      ctx.stroke()
    }
    const head = headGeometry(source, pose, options)
    if (head) {
      const center = project(head.center)
      let rotation = 0
      if (head.end) {
        const a = project(pose[head.head])
        const b = project(head.end)
        rotation = -Math.atan2(b[0] - a[0], a[1] - b[1])
      }
      ctx.strokeStyle = COLORS.center
      ctx.lineWidth = 2.4
      ctx.beginPath()
      ctx.ellipse(...center, head.radiusX * camera.scale, head.radiusY * camera.scale, rotation, 0, TAU)
      ctx.stroke()
      // A forward-facing landmark keeps E, W and oblique camera views explicit.
      const noseLength = head.radiusX * .45 * camera.scale * Math.cos(angle)
      if (Math.abs(noseLength) > .5) {
        const edgeX = center[0] + Math.sign(noseLength) * head.radiusX * camera.scale * .94
        ctx.beginPath()
        ctx.moveTo(edgeX, center[1] - head.radiusY * camera.scale * .12)
        ctx.lineTo(edgeX + noseLength, center[1] + head.radiusY * camera.scale * .1)
        ctx.lineTo(edgeX, center[1] + head.radiusY * camera.scale * .2)
        ctx.stroke()
      }
    }
    for (const index of visible) {
      if (index === source.rootIndex || /^(?:root|Body|HeadEnd)$/i.test(source.names[index])) continue
      const point = project(pose[index])
      ctx.fillStyle = COLORS[sides[index]]
      ctx.beginPath()
      ctx.arc(...point, tips.includes(index) ? 3.7 : 2.5, 0, TAU)
      ctx.fill()
      ctx.strokeStyle = '#f7f7f1'
      ctx.lineWidth = .7
      ctx.stroke()
      if (labels) {
        ctx.font = '10px system-ui, sans-serif'
        ctx.fillText(source.names[index], point[0] + 5, point[1] - 4)
      }
    }
    ctx.restore()
  }

  function draw({ frameIndex = 0, timeMs, interpolate = false, directionDegrees = 0,
    ghostFrameIndices = [], showLabels = false, showTrail = false } = {}) {
    if (disposed) return null
    const box = canvas.getBoundingClientRect?.()
    const ratio = Math.max(1, finite(globalThis.devicePixelRatio, 1))
    const width = Math.max(1, box?.width || fallbackWidth)
    const height = Math.max(1, box?.height || fallbackHeight)
    const pixelWidth = Math.max(1, Math.round(width * ratio))
    const pixelHeight = Math.max(1, Math.round(height * ratio))
    if (canvas.width !== pixelWidth) canvas.width = pixelWidth
    if (canvas.height !== pixelHeight) canvas.height = pixelHeight
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0)
    ctx.clearRect(0, 0, width, height)
    ctx.fillStyle = options.background || '#f4f5f1'
    ctx.fillRect(0, 0, width, height)
    const direction = ((finite(directionDegrees, 0) % 360) + 360) % 360
    const camera = fit(direction, width, height)
    const floor = camera.project([0, camera.floorY, 0])
    ctx.strokeStyle = '#bcc8c1'
    ctx.lineWidth = 1.2
    ctx.beginPath()
    ctx.moveTo(14, floor[1])
    ctx.lineTo(width - 14, floor[1])
    ctx.stroke()
    if (options.showAxes !== false) {
      ctx.save()
      ctx.setLineDash([4, 5])
      ctx.strokeStyle = '#d1d9d4'
      ctx.lineWidth = .9
      ctx.beginPath()
      ctx.moveTo(floor[0], 16)
      ctx.lineTo(floor[0], Math.min(height - 10, floor[1] + 12))
      ctx.stroke()
      ctx.restore()
    }
    if (showTrail) {
      ctx.save()
      ctx.globalAlpha = .23
      ctx.lineWidth = 1.6
      for (const index of tips) {
        ctx.strokeStyle = COLORS[sides[index]]
        ctx.beginPath()
        source.positions.forEach((pose, frame) => {
          const point = camera.project(pose[index])
          if (frame) ctx.lineTo(...point)
          else ctx.moveTo(...point)
        })
        // An actual unclosed capture remains open; do not invent a closing arc.
        ctx.stroke()
      }
      ctx.restore()
    }
    for (const ghost of ghostFrameIndices) {
      if (Number.isInteger(ghost) && ghost >= 0 && ghost < source.positions.length) {
        strokePose(source.positions[ghost], camera, direction, .13, false)
      }
    }
    const pose = samplePose(source, { frameIndex, timeMs, interpolate })
    strokePose(pose, camera, direction, 1, showLabels)
    return { width, height, scale: camera.scale, bounds: [...camera.bounds], floorY: camera.floorY, pose }
  }

  return { draw, dispose() { disposed = true; fitCache.clear() } }
}
