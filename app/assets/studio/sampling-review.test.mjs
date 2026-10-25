import assert from 'node:assert/strict'
import { reviewWindowState as review } from './sampling-review.mjs'

const holds = [167, 166, 167, 167, 166, 167, 166, 167]
assert.deepEqual(Array.from(review(holds, 0).slots), [6, 7, 0, 1])
const seen = []
for (let time = 0; time <= 1000; time++) {
  const state = review(holds, time)
  if (seen.at(-1) !== state.slot) seen.push(state.slot)
}
assert.deepEqual(seen, [6, 7, 0, 1])
const finished = review(holds, 1000)
assert.equal(finished.done, true)
assert.equal(finished.slot, 1)
assert.equal(finished.progress, 0)
assert.equal(finished.planTimeMs, holds[0])
assert(finished.planTimeMs >= holds[0] && finished.planTimeMs < holds[0] + holds[1])
assert.equal(review(holds, 1e9).slot, 1)
assert.equal(review(holds, finished.durationMs - .001).done, false)
assert.equal(review(holds, finished.durationMs).done, true)
assert.deepEqual(Array.from(review([100, 200], 0).slots), [1, 0])
assert.throws(() => review([100, 0], 10))
console.log('Review window passed: 7→8→1→2 once, then remains on2; no replay jump to7.')
