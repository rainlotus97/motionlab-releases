export function studioUrl(path) {
  const prefix = location.pathname.match(/^\/p\/[^/]+/)?.[0] || ''
  return typeof path === 'string' && path.startsWith('/') && !path.startsWith('//') ? prefix + path : path
}

export function studioFetch(path, options) {
  return fetch(studioUrl(path), options)
}

// Shared, framework-free controls. Also inlined in portable previews.
const choices = new Map()
let openChoice = null
let choiceTimer = null
let toastTimer = null

export function toast(message, error = false) {
  document.querySelector('.toast')?.remove()
  clearTimeout(toastTimer)
  const item = document.createElement('div')
  item.className = `toast${error ? ' error' : ''}`
  item.setAttribute('role', error ? 'alert' : 'status')
  item.textContent = message
  document.body.append(item)
  toastTimer = setTimeout(() => item.remove(), error ? 9000 : 3500)
}

export function refreshChoices() {
  for (const [select, control] of choices) {
    const text = select.selectedOptions[0]?.textContent || '选择'
    if (control.label.textContent !== text) control.label.textContent = text
    control.button.disabled = select.disabled
  }
}

export function enhanceChoices() {
  for (const select of document.querySelectorAll('select:not([hidden])')) {
    if (choices.has(select)) continue
    const root = document.createElement('div')
    root.className = 'choice'
    const button = document.createElement('button')
    button.type = 'button'
    button.className = 'choice-trigger'
    button.setAttribute('role', 'combobox')
    button.setAttribute('aria-haspopup', 'listbox')
    button.setAttribute('aria-expanded', 'false')
    button.setAttribute('aria-label', select.getAttribute('aria-label') || select.closest('label')?.childNodes[0]?.textContent?.trim() || '选择')
    const label = document.createElement('span'), arrow = document.createElement('span')
    arrow.className = 'chevron'
    button.append(label, arrow)
    root.append(button)
    select.after(root)
    choices.set(select, { button, label, root })
    function open() {
      if (select.disabled) return
      openChoice?.()
      const menu = document.createElement('div')
      menu.className = 'choice-menu'
      menu.id = `choice-${select.id || choices.size}`
      menu.setAttribute('role', 'listbox')
      button.setAttribute('aria-controls', menu.id)
      button.setAttribute('aria-expanded', 'true')
      const host = select.closest('dialog[open]') || document.body
      host.append(menu)
      const rect = button.getBoundingClientRect()
      menu.style.width = `${Math.max(rect.width, 150)}px`
      menu.style.left = `${Math.min(rect.left, innerWidth - Math.max(rect.width, 150) - 12)}px`
      const items = [...select.options].map(option => {
        const item = document.createElement('button')
        item.type = 'button'
        item.setAttribute('role', 'option')
        item.setAttribute('aria-selected', String(option.selected))
        item.textContent = option.textContent
        item.disabled = option.disabled
        item.onclick = event => {
          event.preventDefault()
          select.value = option.value
          select.dispatchEvent(new Event('change', { bubbles: true }))
          refreshChoices()
          close()
          button.focus()
        }
        menu.append(item)
        return item
      })
      const top = rect.bottom + 5
      menu.style.top = `${top + menu.offsetHeight > innerHeight - 12 ? Math.max(12, rect.top - menu.offsetHeight - 5) : top}px`
      function close() {
        menu.remove()
        button.setAttribute('aria-expanded', 'false')
        document.removeEventListener('pointerdown', outside, true)
        window.removeEventListener('resize', close)
        if (openChoice === close) openChoice = null
      }
      function outside(event) { if (!menu.contains(event.target) && !button.contains(event.target)) close() }
      menu.onkeydown = event => {
        const enabled = items.filter(item => !item.disabled)
        const current = enabled.indexOf(document.activeElement)
        if (event.key === 'Escape' || event.key === 'Tab') { close(); button.focus(); if (event.key === 'Escape') event.preventDefault() }
        if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
          event.preventDefault()
          const next = event.key === 'Home' ? 0 : event.key === 'End' ? enabled.length - 1 : (current + (event.key === 'ArrowDown' ? 1 : -1) + enabled.length) % enabled.length
          enabled[next]?.focus()
        }
      }
      document.addEventListener('pointerdown', outside, true)
      window.addEventListener('resize', close)
      openChoice = close
      ;(items[select.selectedIndex] || items[0])?.focus()
    }
    button.onclick = event => { event.preventDefault(); open() }
    button.onkeydown = event => { if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); open() } }
  }
  refreshChoices()
  if (!choiceTimer) choiceTimer = setInterval(refreshChoices, 250)
}

export function dialog({ title, detail = '', fields = [], accept = '保存', danger = false, onSubmit }) {
  const element = document.createElement('dialog')
  element.className = 'studio-dialog'
  const header = document.createElement('div')
  header.className = 'dialog-header'
  const heading = document.createElement('h2')
  heading.textContent = title
  const close = document.createElement('button')
  close.type = 'button'; close.textContent = '×'; close.setAttribute('aria-label', '关闭')
  close.onclick = () => element.close()
  header.append(heading, close)
  element.append(header)
  if (detail) { const p = document.createElement('p'); p.className = 'dialog-detail'; p.textContent = detail; element.append(p) }
  const form = document.createElement('form')
  const controls = new Map()
  const groups = new Map()
  for (const field of fields) {
    const label = document.createElement('label')
    label.textContent = field.label
    const input = document.createElement(field.type === 'textarea' ? 'textarea' : 'input')
    if (field.type !== 'textarea') input.type = field.type || 'text'
    input.value = field.value ?? ''
    input.required = field.required !== false
    if (field.min !== undefined) input.min = field.min
    if (field.max !== undefined) input.max = field.max
    if (field.maxLength) input.maxLength = field.maxLength
    if (field.pattern) input.pattern = field.pattern
    if (field.placeholder) input.placeholder = field.placeholder
    input.addEventListener('invalid', () => { const details = input.closest('details'); if (details) details.open = true })
    input.name = field.name
    label.append(input)
    if (field.group) {
      if (!groups.has(field.group)) {
        const details = document.createElement('details'), summary = document.createElement('summary')
        details.className = 'form-group'; summary.textContent = field.group; details.append(summary); form.append(details); groups.set(field.group, details)
      }
      groups.get(field.group).append(label)
    } else form.append(label)
    controls.set(field.name, input)
  }
  const error = document.createElement('p'); error.className = 'dialog-error'; error.setAttribute('role', 'alert')
  const actions = document.createElement('div'); actions.className = 'dialog-actions'
  const cancel = document.createElement('button'); cancel.type = 'button'; cancel.textContent = '取消'; cancel.onclick = () => element.close()
  const submit = document.createElement('button'); submit.type = 'submit'; submit.textContent = accept; submit.className = danger ? 'danger' : 'primary'
  actions.append(cancel, submit); form.append(error, actions); element.append(form)
  const initialValues = new Map([...controls].map(([key, input]) => [key, input.value]))
  element.hasPendingInput = () => submit.disabled || [...controls].some(([key, input]) => input.value !== initialValues.get(key))
  form.onsubmit = async event => {
    event.preventDefault(); submit.disabled = true; error.textContent = ''
    try { await onSubmit(Object.fromEntries([...controls].map(([key, input]) => [key, input.value]))); element.close() }
    catch (problem) { error.textContent = problem.message || String(problem) }
    finally { submit.disabled = false }
  }
  document.body.append(element)
  element.addEventListener('close', () => element.remove(), { once: true })
  element.showModal()
  return element
}

// Library identity comes from explicit metadata; the fallback keeps legacy projects readable.
export function clipLibrary(item, project) {
  const meta = { ...item, ...item.library }
  const parts = item.id.split('/')
  const action = meta.actionId || parts.at(-1)
  const match = action.match(/^(.*)-(NW|NE|SW|SE|N|S|E|W)$/)
  const actionId = match ? match[1] : action
  const direction = meta.direction || match?.[2] || ''
  const names = { walk: '八向跑动', run: '八向跑动', jump: '蹦跳', idle: '待机', rest: '歇息', done: '办妥', sweep: '扫庭' }
  return {
    characterId: meta.characterId || project.id,
    characterName: meta.characterName || project.name.split(' · ')[0],
    sourceId: meta.sourceId || parts[0],
    variantId: meta.variantId || (parts.length > 2 ? parts.at(-2) : 'default'),
    variantName: meta.variantName || (item.name.includes(' · ') ? item.name.split(' · ')[0] : '默认外观'),
    actionId,
    actionName: meta.actionName && meta.actionName !== actionId ? meta.actionName : (names[actionId] || item.name.split(' · ').at(-1)),
    direction
  }
}

export function mountChrome(api) {
  const find = id => document.getElementById(id)
  const { project } = api
  enhanceChoices()
  find('open-export').onclick = () => { api.pause(); find('export-dialog').showModal(); refreshChoices() }
  document.querySelectorAll('[data-close-dialog]').forEach(button => { button.onclick = () => button.closest('dialog').close() })
  for (const button of document.querySelectorAll('[data-inspector]')) button.onclick = () => {
    for (const tab of document.querySelectorAll('[data-inspector]')) tab.setAttribute('aria-selected', String(tab === button))
    for (const panel of document.querySelectorAll('[data-inspector-panel]')) panel.hidden = panel.dataset.inspectorPanel !== button.dataset.inspector
  }
  const list = find('motion-list')
  const thumbImages = new Map()
  let disposed = false
  async function thumbnail(canvas, item) {
    const frame = item.frames[item.steps[0].frame]
    if (!thumbImages.has(frame.url)) {
      const image = new Image(); image.src = frame.url
      thumbImages.set(frame.url, image.decode().then(() => image))
    }
    try {
      const image = await thumbImages.get(frame.url)
      if (disposed || !canvas.isConnected) return
      const c = canvas.getContext('2d'), [x,y,x2,y2] = frame.rect
      canvas.width = 96; canvas.height = 96
      const [dx,dy,dw,dh] = frame.drawRect || [0,0,...item.canvas]
      const scale = Math.min(96 / item.canvas[0], 96 / item.canvas[1])
      c.drawImage(image,x,y,x2-x,y2-y,dx*scale,dy*scale,dw*scale,dh*scale)
    } catch { canvas.setAttribute('aria-label', '缩略图不可用') }
  }
  const openFolders = new Map()
  let selectedIdentity = ''
  const directionNames = { NW: '西北', N: '北', NE: '东北', W: '西', E: '东', SW: '西南', S: '南', SE: '东南' }
  function choose(item) { find('clip').value = item.id; api.select(item.id) }
  function folder(key, label, level, count, active) {
    const details = document.createElement('details')
    details.className = `library-folder level-${level}`
    details.dataset.folder = key
    details.open = openFolders.get(key) ?? active
    const summary = document.createElement('summary')
    const icon = document.createElement('span'), name = document.createElement('span'), total = document.createElement('small')
    icon.className = 'folder-caret'; icon.setAttribute('aria-hidden', 'true')
    name.textContent = label; total.textContent = count
    summary.append(icon, name, total); details.append(summary)
    details.ontoggle = () => openFolders.set(key, details.open)
    return details
  }
  function renderList() {
    const query = find('motion-search').value.trim().toLowerCase()
    const current = api.current()
    const currentMeta = current && clipLibrary(current, project)
    const characters = new Map()
    for (const item of project.clips) {
      const meta = clipLibrary(item, project)
      if (query && !`${item.name} ${item.id} ${meta.characterName} ${meta.variantName} ${meta.actionName}`.toLowerCase().includes(query)) continue
      if (!characters.has(meta.characterId)) characters.set(meta.characterId, { meta, variants: new Map() })
      const character = characters.get(meta.characterId), variantKey = meta.variantId
      if (!character.variants.has(variantKey)) character.variants.set(variantKey, { meta, actions: new Map() })
      const variant = character.variants.get(variantKey)
      if (!variant.actions.has(meta.actionId)) variant.actions.set(meta.actionId, { meta, items: [] })
      variant.actions.get(meta.actionId).items.push({ item, meta })
    }
    list.replaceChildren()
    find('direction-options').replaceChildren()
    find('direction-control').hidden = true
    for (const [characterId, character] of characters) {
      const activeCharacter = currentMeta?.characterId === characterId
      const characterNode = folder(characterId, character.meta.characterName, 'character', character.variants.size + ' 件外观', activeCharacter || !!query)
      for (const [variantKey, variant] of character.variants) {
        const key = `${characterId}/${variantKey}`
        const activeVariant = activeCharacter && currentMeta.variantId === variant.meta.variantId
        const variantNode = folder(key, variant.meta.variantName, 'variant', variant.actions.size, activeVariant || !!query)
        const actionList = document.createElement('div'); actionList.className = 'library-actions'
        for (const [actionId, action] of variant.actions) {
          const active = activeVariant && currentMeta.actionId === actionId
          const item = action.items.find(entry => entry.item.id === current?.id)?.item || action.items.find(entry => entry.meta.direction === 'S')?.item || action.items[0].item
          const row = document.createElement('div'); row.className = 'library-action'; row.dataset.actionId = actionId
          const button = document.createElement('button'); button.className = 'motion-item'; button.dataset.clipId = item.id
          button.setAttribute('aria-current', String(active))
          const canvas = document.createElement('canvas'), text = document.createElement('span'), name = document.createElement('strong'), meta = document.createElement('small')
          name.textContent = action.meta.actionName
          meta.textContent = action.items.length > 1 ? `${action.items.length} 个方向` : `${item.steps.length} 段 · ${item.loop ? '循环' : '单次'}`
          text.append(name, meta); button.append(canvas, text); button.onclick = () => choose(item)
          thumbnail(canvas, item); row.append(button)
          if (active && action.items.some(entry => entry.meta.direction)) {
            const pad = document.createElement('div'); pad.className = 'direction-pad'; pad.setAttribute('role', 'group'); pad.setAttribute('aria-label', '动作朝向')
            for (const direction of ['NW','N','NE','W','','E','SW','S','SE']) {
              const entry = action.items.find(entry => entry.meta.direction === direction)
              if (!entry) { const space = document.createElement('span'); space.className = 'direction-space'; space.setAttribute('aria-hidden', 'true'); if (!direction) space.textContent = '朝向'; pad.append(space); continue }
              const control = document.createElement('button'); control.textContent = directionNames[direction] || direction
              control.title = entry.item.name; control.dataset.clipId = entry.item.id; control.setAttribute('aria-label', `${directionNames[direction] || direction}方向`)
              control.setAttribute('aria-pressed', String(entry.item.id === current.id)); control.onclick = () => choose(entry.item); pad.append(control)
            }
            find('direction-control').hidden = false
            find('current-direction').textContent = directionNames[currentMeta.direction] || currentMeta.direction
            find('direction-options').append(pad)
          }
          actionList.append(row)
        }
        variantNode.append(actionList); characterNode.append(variantNode)
      }
      list.append(characterNode)
    }
    if (!characters.size) { const empty = document.createElement('p'); empty.className = 'library-empty'; empty.textContent = query ? '没有匹配的资源' : '尚未添加角色动作'; list.append(empty) }
    find('clip-count').textContent = `${project.clips.length} 动作`
  }
  find('motion-search').oninput = renderList
  find('refresh-project').onclick = () => {
    if (api.pending()) dialog({ title: '刷新素材', detail: '当前有未保存调整', accept: '保存并刷新', onSubmit: async () => { await api.save(); location.reload() } })
    else location.reload()
  }
  find('empty-motion').hidden = !!project.clips.length
  find('open-export').disabled = !project.clips.length || !!project.exportReadOnly
  find('save').disabled = !project.clips.length
  if (project.exportReadOnly) {
    find('project-picker').disabled = true
    document.querySelector('[data-workspace="files"]').hidden = true
    find('browse-assets').hidden = true
    find('refresh-project').hidden = true
    find('rename-action').hidden = true
    find('remove-action').hidden = true
  }
  const update = () => {
    const current = api.current()
    if (current) {
      const meta = clipLibrary(current, project)
      find('active-name').textContent = meta.actionName
      find('active-breadcrumb').replaceChildren(...[meta.characterName, meta.variantName, meta.direction && directionNames[meta.direction]].filter(Boolean).map(label => { const span = document.createElement('span'); span.textContent = label; return span }))
      if (selectedIdentity !== current.id) {
        selectedIdentity = current.id
        openFolders.set(meta.characterId, true)
        openFolders.set(`${meta.characterId}/${meta.variantId}`, true)
        renderList()
      }
    }
    refreshChoices()
  }
  find('toggle-inspector').onclick = () => {
    const inspector = find('inspector'), open = inspector.hidden
    inspector.hidden = !open
    find('toggle-inspector').setAttribute('aria-expanded', String(open))
    document.body.classList.toggle('inspector-open', open)
    window.dispatchEvent(new Event('resize'))
  }
  renderList(); update()
  const keyboard = event => {
    if (event.target.closest('input,textarea,button,[role="combobox"]') || document.querySelector('dialog[open]')) return
    if (event.code === 'Space') { event.preventDefault(); find('play').click() }
    if (event.key === 'ArrowLeft') find('prev').click()
    if (event.key === 'ArrowRight') find('next').click()
  }
  document.addEventListener('keydown', keyboard)
  window.addEventListener('pagehide', () => { disposed = true; thumbImages.clear(); document.removeEventListener('keydown', keyboard) }, { once: true })
  return { update }
}

window.addEventListener('pagehide', () => { clearInterval(choiceTimer); choiceTimer = null; clearTimeout(toastTimer); openChoice?.() })
window.addEventListener('pageshow', event => { if (event.persisted) enhanceChoices() })
