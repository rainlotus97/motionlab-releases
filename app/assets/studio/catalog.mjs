import { studioFetch, studioUrl } from './controls.mjs'
// The host page owns its edits. This panel only discovers and opens projects.
export async function mountAssetCatalog({ pending = () => null, pause = () => {} } = {}) {
  if (window.__MOTION_STUDIO_PROJECT__) return null
  const controller = new AbortController()
  let disposed = false, busy = false, catalog = null, browsing = null, confirmation = null
  const abortInitial = () => { disposed = true; controller.abort() }
  window.addEventListener('pagehide', abortInitial, { once: true })
  let browseTicket = 0
  const api = async (path, data) => {
    const response = await studioFetch(path, { signal: controller.signal, ...(data ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) } : {}) })
    let value
    try { value = await response.json() } catch { throw new Error(`目录服务未返回有效数据（HTTP ${response.status}）。`) }
    if (!response.ok) throw new Error(typeof value.error === 'string' ? value.error : value.error?.message || `目录操作失败（HTTP ${response.status}）。`)
    return value
  }
  // Portable previews and older servers have no catalog. Do not leave a dead entry.
  try { catalog = await api('/api/catalog') } catch { return null }
  if (disposed || !Array.isArray(catalog.projects)) return null
  const style = document.createElement('link')
  style.rel = 'stylesheet'; style.href = new URL('./catalog.css', import.meta.url).href
  document.head.append(style)
  const button = document.createElement('button')
  button.type = 'button'; button.textContent = '选择资产目录'; button.className = 'catalog-entry'
  button.setAttribute('aria-expanded', 'false'); button.setAttribute('aria-controls', 'asset-catalog')
  const panel = document.createElement('section')
  panel.id = 'asset-catalog'; panel.className = 'asset-catalog'; panel.hidden = true
  panel.setAttribute('aria-labelledby', 'asset-catalog-heading')
  panel.innerHTML = `<div class="catalog-heading"><div><h2 id="asset-catalog-heading">资产目录与工程</h2><p>选择资产上级目录，再打开对应工作台。各工程使用独立服务，素材、方案和问题记录分别保留。</p></div><button type="button" data-catalog="close">收起目录</button></div>
    <p class="catalog-current" data-catalog="current"></p>
    <div class="catalog-grid"><section><h3>浏览目录</h3><label>目录绝对路径<input type="text" data-catalog="path" spellcheck="false" autocomplete="off"></label><div class="catalog-actions"><button type="button" data-catalog="browse">浏览此路径</button><button type="button" data-catalog="parent">上一级</button><button type="button" data-catalog="use" class="primary">使用此目录扫描</button></div><p class="catalog-hint" data-catalog="browse-path"></p><div class="catalog-folders" data-catalog="folders"></div></section>
    <section><h3>已发现工程</h3><label>搜索人物、工程或路径<input type="search" data-catalog="search" autocomplete="off"></label><p class="catalog-hint" data-catalog="summary"></p><div class="catalog-projects" data-catalog="projects"></div><p class="catalog-hint">目前识别含 studio.project.json 的工程。裸 PNG 目录需先由技能建立资产清单；不会自动猜测动作顺序。</p></section></div>
    <div class="catalog-confirm" data-catalog="confirm" role="alertdialog" aria-labelledby="catalog-confirm-text" hidden><p id="catalog-confirm-text" data-catalog="confirm-text"></p><div class="catalog-actions"><button type="button" data-catalog="preserve" class="primary">保留本机草稿并打开</button><button type="button" data-catalog="cancel">取消切换</button></div></div>
    <p class="catalog-status" data-catalog="status" role="status" aria-live="polite"></p>`
  const ui = Object.fromEntries([...panel.querySelectorAll('[data-catalog]')].map(element => [element.dataset.catalog, element]))
  const nav = document.querySelector('header nav') || document.querySelector('header')
  nav?.append(button)
  document.querySelector('header')?.after(panel)
  const notify = text => { ui.status.textContent = text }
  const setBusy = value => {
    busy = value; panel.setAttribute('aria-busy', String(value))
    for (const control of panel.querySelectorAll('button,input')) control.disabled = value
    if (!value) ui.parent.disabled = !browsing?.parent
  }
  const guarded = fn => async (...args) => {
    if (busy || disposed) return
    setBusy(true)
    try { await fn(...args) } catch (error) { if (!disposed && error.name !== 'AbortError') notify(error.message || String(error)) }
    finally { if (!disposed) setBusy(false) }
  }
  function renderProjects() {
    const query = ui.search.value.trim().toLocaleLowerCase()
    const projects = catalog.projects.filter(item => `${item.name} ${item.relativePath} ${item.path}`.toLocaleLowerCase().includes(query))
    ui.projects.replaceChildren(...projects.map(item => {
      const row = document.createElement('button'); row.type = 'button'; row.className = 'catalog-project'
      const current = item.id === catalog.currentProjectId
      row.setAttribute('aria-current', String(current))
      const name = document.createElement('strong'); name.textContent = `${item.name}${current ? ' · 当前工程' : ''}`
      const path = document.createElement('span'); path.textContent = item.relativePath || item.path
      row.append(name, path); row.title = item.path
      row.onclick = () => { requestOpen(item).catch(error => notify(error.message || String(error))) }
      return row
    }))
    if (!projects.length) {
      const empty = document.createElement('p'); empty.className = 'catalog-hint'
      empty.textContent = catalog.projects.length ? '没有符合搜索条件的工程。' : '此目录暂未发现工程清单。可选择更上层的资产目录，或先由技能创建清单。'
      ui.projects.append(empty)
    }
    const limitations = [catalog.truncated ? '扫描达到范围限制，部分子目录未扫描；请缩小到角色资产目录。' : '', catalog.skippedCount ? `跳过 ${catalog.skippedCount} 项无法读取或不符合规则的内容。` : ''].filter(Boolean).join(' ')
    ui.summary.textContent = `${catalog.projects.length} 个工程${query ? ` · 当前显示 ${projects.length} 个` : ''}。${limitations}`
  }
  function renderCatalog(value) {
    catalog = value
    const current = catalog.projects.find(item => item.id === catalog.currentProjectId)
    ui.current.textContent = `扫描根目录：${catalog.root}${current ? `\n当前工程：${current.name}` : '\n当前工作台工程保持不变'}。`
    renderProjects()
  }
  async function browse(path) {
    if (!path?.trim()) throw new Error('请输入目录的绝对路径。')
    const ticket = ++browseTicket
    const value = await api(`/api/catalog/directories?${new URLSearchParams({ path: path.trim() })}`)
    if (disposed || ticket !== browseTicket) return
    browsing = value; ui.path.value = value.path
    ui['browse-path'].textContent = `正在浏览：${value.path}${value.truncated ? '（仅展示部分子目录，请输入更具体的路径）' : ''}`
    ui.folders.replaceChildren(...(value.directories || []).map(item => {
      const folder = document.createElement('button'); folder.type = 'button'; folder.textContent = item.name; folder.title = item.path
      folder.onclick = guarded(() => browse(item.path))
      return folder
    }))
    if (!value.directories?.length) {
      const empty = document.createElement('p'); empty.className = 'catalog-hint'; empty.textContent = '没有可浏览的子目录。'; ui.folders.append(empty)
    }
    ui.parent.disabled = !value.parent
  }
  function resolveConfirmation(value) {
    const resolve = confirmation; confirmation = null; ui.confirm.hidden = true
    ui.projects.inert = false
    resolve?.(value)
  }
  async function requestOpen(item) {
    if (busy || disposed || confirmation) return
    pause()
    const edit = pending()
    if (edit) {
      ui['confirm-text'].textContent = edit.message || '当前页面有未保存内容。先保留本机草稿，再打开其他工程。'
      ui.preserve.textContent = edit.label || '保留本机草稿并打开'
      ui.confirm.hidden = false; ui.projects.inert = true; ui.preserve.focus()
      const preserve = await new Promise(resolve => { confirmation = resolve })
      if (!preserve || disposed) { notify('已取消切换，当前内容保持不变。'); return }
      try { await edit.preserve() } catch (error) { notify(`草稿未能保留，已停止切换：${error.message || error}`); return }
    }
    await guarded(async () => {
      notify('正在打开所选工程…')
      const opened = await api('/api/catalog/open', { id: item.id })
      if (disposed) return
      const url = new URL(opened.url)
      if (!['http:', 'https:'].includes(url.protocol) || !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)) throw new Error('工程服务未返回可用的本机地址。')
      location.assign(url.href)
    })()
  }
  ui.preserve.onclick = () => resolveConfirmation(true)
  ui.cancel.onclick = () => { resolveConfirmation(false); button.focus() }
  ui.close.onclick = () => { resolveConfirmation(false); panel.hidden = true; button.setAttribute('aria-expanded', 'false'); button.focus() }
  button.onclick = () => {
    panel.hidden = !panel.hidden; button.setAttribute('aria-expanded', String(!panel.hidden))
    if (!panel.hidden && !browsing) guarded(() => browse(catalog.root))()
    else if (panel.hidden) resolveConfirmation(false)
  }
  ui.browse.onclick = guarded(() => browse(ui.path.value))
  ui.path.onkeydown = event => { if (event.key === 'Enter') { event.preventDefault(); ui.browse.click() } }
  ui.parent.onclick = guarded(() => browse(browsing?.parent))
  ui.use.onclick = guarded(async () => {
    const value = await api('/api/catalog/root', { path: ui.path.value.trim() })
    if (disposed) return
    renderCatalog(value); notify('目录扫描已更新；当前工程、动画和人工记录均未切换。')
  })
  ui.search.oninput = renderProjects
  renderCatalog(catalog); ui.path.value = catalog.root
  const dispose = () => { disposed = true; browseTicket++; controller.abort(); resolveConfirmation(false) }
  window.removeEventListener('pagehide', abortInitial)
  window.addEventListener('pagehide', dispose, { once: true })
  return { dispose }
}
