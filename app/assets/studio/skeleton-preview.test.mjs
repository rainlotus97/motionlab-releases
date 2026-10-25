import test from 'node:test'
import assert from 'node:assert/strict'
import { createSkeletonRenderer, projectPoint, samplePose } from './skeleton-preview.mjs'

function timedSource({ endpoint = false, loop = true } = {}) {
  return {
    names: ['Hips'], parents: [-1], rootIndex: 0,
    positions: (endpoint ? [0, 5, 1] : [0, 4, 8]).map(x => [[x, 0, 0]]),
    timesMs: endpoint ? [0, 50, 100] : [0, 40, 80],
    durationMs: 100, loop, includesEndpoint: endpoint
  }
}

test('sampling uses actual nonuniform source times without changing the source', () => {
  const source = timedSource()
  const before = JSON.stringify(source)
  assert.equal(samplePose(source, { timeMs: 20, interpolate: true })[0][0], 2)
  assert.equal(samplePose(source, { timeMs: 60, interpolate: true })[0][0], 6)
  assert.equal(samplePose(source, { timeMs: 60 })[0][0], 4)
  assert.equal(samplePose(source, { frameIndex: 2 })[0][0], 8)
  assert.equal(JSON.stringify(source), before)
})

test('an endpoint-exclusive loop interpolates over its real final interval', () => {
  const source = timedSource()
  assert.equal(samplePose(source, { timeMs: 90, interpolate: true })[0][0], 4)
  assert.equal(samplePose(source, { timeMs: -10, interpolate: true })[0][0], 4)
  assert.equal(samplePose(source, { timeMs: 100, interpolate: true })[0][0], 0)
})

test('an included endpoint is used approaching the seam and is directly inspectable', () => {
  const source = timedSource({ endpoint: true })
  assert.equal(samplePose(source, { timeMs: 75, interpolate: true })[0][0], 3)
  assert.equal(samplePose(source, { frameIndex: 2 })[0][0], 1)
  assert.equal(samplePose(source, { timeMs: 100, interpolate: true })[0][0], 0)
})

test('one-shot playback clamps to its actual first and last poses', () => {
  const source = timedSource({ endpoint: true, loop: false })
  assert.equal(samplePose(source, { timeMs: -10, interpolate: true })[0][0], 0)
  assert.equal(samplePose(source, { timeMs: 120, interpolate: true })[0][0], 1)
})

test('all eight camera angles preserve height; E and W are real orthographic views', () => {
  const point = [1, 2, 3]
  assert.deepEqual(projectPoint(point), [3, -2])
  assert.ok(Math.abs(projectPoint(point, 90)[0] - 1) < 1e-12)
  assert.ok(Math.abs(projectPoint(point, 180)[0] + 3) < 1e-12)
  assert.ok(Math.abs(projectPoint(point, 270)[0] + 1) < 1e-12)
  for (let degrees = 0; degrees < 360; degrees += 45) assert.equal(projectPoint(point, degrees)[1], -2)
})

function mockCanvas() {
  const operations = []
  const context = new Proxy({}, {
    get(target, key) {
      if (key in target) return target[key]
      return (...args) => operations.push({ kind: key, args, color: target.strokeStyle })
    },
    set(target, key, value) { target[key] = value; return true }
  })
  return {
    width: 480, height: 480, operations,
    getContext: () => context,
    getBoundingClientRect: () => ({ width: 480, height: 480 })
  }
}

function walkingSource() {
  return {
    names: ['Hips', 'Head', 'HeadEnd', 'LeftWrist', 'RightWrist', 'LeftToeEnd', 'RightToeEnd'],
    parents: [-1, 0, 1, 0, 0, 0, 0], rootIndex: 0,
    positions: [
      [[0, 80, 0], [0, 140, 0], [0, 160, 0], [10, 70, -15], [-10, 70, 20], [10, 0, 30], [-10, 0, -30]],
      [[0, 84, 0], [0, 144, 0], [0, 164, 0], [10, 72, 40], [-10, 72, -40], [10, 0, -25], [-10, 0, 25]]
    ],
    timesMs: [0, 50], durationMs: 100, loop: true, includesEndpoint: false
  }
}

test('renderer fits the entire source once per direction, never each current pose', () => {
  const canvas = mockCanvas()
  const renderer = createSkeletonRenderer(canvas, walkingSource())
  const first = renderer.draw({ frameIndex: 0 })
  const second = renderer.draw({ frameIndex: 1, ghostFrameIndices: [0], showTrail: true, showLabels: true })
  assert.equal(first.scale, second.scale)
  assert.deepEqual(first.bounds, second.bounds)
  assert.equal(first.floorY, 0)
  assert.equal(first.bounds[0], -40)
  assert.equal(first.bounds[2], 40)
  assert.equal(first.bounds[1], -164)
  const colors = canvas.operations.filter(item => item.kind === 'stroke' && ['#b56d58', '#287fa0'].includes(item.color)).slice(0, 4).map(item => item.color)
  assert.deepEqual(colors, ['#b56d58', '#b56d58', '#287fa0', '#287fa0'])
  renderer.dispose()
  assert.equal(renderer.draw(), null)
})

test('renderer rejects malformed source data rather than drawing misleading limbs', () => {
  const source = walkingSource()
  source.timesMs = [0, 0]
  assert.throws(() => createSkeletonRenderer(mockCanvas(), source), /strictly increase/)
})


test('fractional frame indices retain fractional poses including frame 20.25', () => {
  const source = {
    names: ['Hips'], parents: [-1],
    positions: Array.from({ length: 24 }, (_, index) => [[index * 4, 0, 0]]),
    timesMs: Array.from({ length: 24 }, (_, index) => index * 10),
    durationMs: 240, loop: true, includesEndpoint: false
  }
  assert.equal(samplePose(source, { frameIndex: 20.25 })[0][0], 81)
  assert.equal(samplePose(source, { frameIndex: 23.5 })[0][0], 46)
  assert.equal(samplePose(source, { frameIndex: -.5 })[0][0], 46)
  assert.equal(samplePose(source, { frameIndex: 24 })[0][0], 0)
  const included = { ...source, includesEndpoint: true }
  assert.equal(samplePose(included, { frameIndex: 23.5 })[0][0], 92)
  assert.equal(samplePose(included, { frameIndex: -.5 })[0][0], 0)
})
