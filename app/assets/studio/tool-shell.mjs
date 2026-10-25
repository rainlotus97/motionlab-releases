import { enhanceChoices } from './controls.mjs'

document.body.classList.add('tool-page')
if (new URLSearchParams(location.search).get('embedded') === '1') document.body.classList.add('embedded')
for (const selector of ['.display-controls', '.display-options']) {
  const content = document.querySelector(selector)
  if (!content) continue
  const details = document.createElement('details'), summary = document.createElement('summary')
  details.className = 'tool-options'; summary.textContent = '显示选项'
  content.before(details); details.append(summary, content)
}
enhanceChoices()
const observer = new MutationObserver(() => enhanceChoices())
observer.observe(document.body, { childList: true, subtree: true })
window.addEventListener('pagehide', () => observer.disconnect(), { once: true })
