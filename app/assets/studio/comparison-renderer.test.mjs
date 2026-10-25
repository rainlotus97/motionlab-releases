import assert from 'node:assert/strict'
import { logicalSkeletonPoint, drawArtwork } from './comparison-renderer.mjs'

const alignment = { origin: [352, 710], pixelsPerSourceUnit: 3.2, sourceOriginX: 0, sourceFloorY: -1.599 }
assert.deepEqual(logicalSkeletonPoint([0, -1.599, 0], alignment), [352, 710])
const near = logicalSkeletonPoint([0, 100, 20], alignment)
assert.equal(near[0], 416)
assert(Math.abs(near[1] - (710 - 101.599 * 3.2)) < 1e-8)
const west = logicalSkeletonPoint([0, 100, 20], alignment, 180)
assert(Math.abs(west[0] - 288) < 1e-8)
// A trimmed atlas cell must restore its logical placement, never fill the canvas.
let argumentsSeen
const view = { zoom: .5, map: ([x, y]) => [10 + x * .5, 20 + y * .5], ctx: { save() {}, restore() {}, drawImage(...args) { argumentsSeen = args } } }
const image = { test: true }
assert.equal(drawArtwork(view, { rect: [12, 14, 32, 44], drawRect: [50, 60, 100, 200] }, { offset: [3, -4] }, image, [704, 768]), true)
assert.deepEqual(argumentsSeen, [image, 12, 14, 20, 30, 36.5, 48, 50, 100])
assert.equal(drawArtwork(view, null, null, null, [704, 768]), false)
console.log('Comparison renderer passed: shared source floor/axis, E/W projection, trimmed atlas restoration.')
