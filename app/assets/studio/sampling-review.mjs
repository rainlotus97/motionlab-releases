/** Single-pass tail-to-head review clock. Playback never replays the review window implicitly. */
export function reviewWindowState(durationsMs, elapsedMs) {
  if (!Array.isArray(durationsMs) || durationsMs.length < 2 || durationsMs.some(ms => !Number.isFinite(ms) || ms <= 0) || !Number.isFinite(elapsedMs)) throw new Error('Expected positive pose durations and finite review time')
  const count = durationsMs.length
  const tail = Math.min(2, Math.floor(count / 2))
  const slots = [...Array.from({ length: tail }, (_, i) => count - tail + i), ...Array.from({ length: tail }, (_, i) => i)]
  // Pause on arrival at the last reviewed pose, so continuous and held
  // previews freeze at the same source time instead of one pose apart.
  const durationMs = slots.slice(0, -1).reduce((sum, slot) => sum + durationsMs[slot], 0)
  const elapsed = Math.max(0, Math.min(elapsedMs, durationMs))
  const done = elapsedMs >= durationMs
  let offset = 0
  let slot = slots.at(-1)
  let progress = 0
  for (const candidate of slots) {
    const end = offset + durationsMs[candidate]
    if (elapsed < end) {
      slot = candidate
      progress = (elapsed - offset) / durationsMs[candidate]
      break
    }
    offset = end
  }
  const poseStart = durationsMs.slice(0, slot).reduce((sum, ms) => sum + ms, 0)
  // Keep the final reviewed pose visible on arrival; never enter another pose
  // or replay the window. The source freezes at this same sample time.
  const planTimeMs = poseStart + Math.min(durationsMs[slot] * progress, durationsMs[slot] - 1e-7)
  return { slots, slot, progress, durationMs, elapsedMs: elapsed, planTimeMs, done }
}
