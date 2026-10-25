import Split from './vendor/split.es.mjs'
import { studioFetch, studioUrl, dialog, toast } from './controls.mjs'
import { mountFiles } from './file-console.mjs'

export async function mountWorkspace(api) {
  const find = id => document.getElementById(id)
  const controller = new AbortController()
  let disposed = false, catalog = null, browsing = null
  const toolFrames = new Map()
  const request = async (path, data) => {
    const response = await studioFetch(path, { signal: controller.signal, ...(data === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }) })
    let value
    try { value = await response.json() } catch { throw Error(`服务不可用（${response.status}）`) }
    if (!response.ok) throw Error(value.error?.message || value.error || `操作失败（${response.status}）`)
    return value
  }
  function guard(fn) { return async (...args) => { try { return await fn(...args) } catch (error) { if (!disposed && error.name !== 'AbortError') toast(error.message, true) } } }
  async function preserveAll() {
    const pending = [api.pending(), ...[...toolFrames.values()].map(frame => frame.contentWindow.motionStudioBridge?.pending?.())].filter(Boolean)
    for (const edit of pending) await edit.preserve()
  }
  function hasDialogInput() { return [...document.querySelectorAll('dialog[open]')].some(panel => panel.hasPendingInput?.()) }
  function hasPending() { return api.pending() || [...toolFrames.values()].some(frame => frame.contentWindow.motionStudioBridge?.pending?.()) }
  async function navigate(url) {
    if (hasPending()) {
      dialog({ title: '保存当前调整？', accept: '保存并打开', onSubmit: async () => { await preserveAll(); location.assign(url) } })
    } else location.assign(url)
  }
  async function openProject(item) {
    if (hasPending()) { await preserveAll() }
    const opened = await request('/api/catalog/open', { id: item.id })
    const url = new URL(opened.url)
    if (url.protocol !== 'http:' || !['127.0.0.1','localhost'].includes(url.hostname)) throw Error('工程地址无效')
    await navigate(url.href)
  }
  async function chooseProject() {
    api.pause()
    catalog = await request('/api/catalog')
    const panel = document.createElement('dialog')
    panel.className = 'studio-dialog project-dialog'
    panel.innerHTML = `<div class="dialog-header"><div><span class="eyebrow">PROJECTS</span><h2>打开资源工程</h2></div><button data-close aria-label="关闭">×</button></div><section class="project-catalog"><div class="project-recent-list"></div></section><details class="project-browser"><summary>从其他目录打开或创建</summary><div class="project-directory"><input data-path aria-label="目录路径" spellcheck="false"><button data-go>前往</button><button data-parent>上一级</button></div><div class="project-folder-list"></div><div class="project-bottom"><button data-create>在此创建工程</button><button data-use class="primary">选择此目录</button></div></details><p class="dialog-error" role="alert"></p>`
    const pathInput = panel.querySelector('[data-path]'), folders = panel.querySelector('.project-folder-list'), projects = panel.querySelector('.project-recent-list'), error = panel.querySelector('.dialog-error')
    let ticket = 0, closed = false
    function guarded(fn) { return async () => { error.textContent = ''; try { await fn() } catch (problem) { if (!closed) error.textContent = problem.message } } }
    function showProjects() {
      projects.replaceChildren(...catalog.projects.map(item => {
        const button = document.createElement('button'), title = document.createElement('span'), path = document.createElement('small')
        title.textContent = item.name; path.textContent = item.relativePath.replace(/\/studio\.project\.json$/, '') || '当前目录'
        button.append(title, path); button.onclick = guarded(() => openProject(item)); return button
      }))
      if (!catalog.projects.length) projects.textContent = '暂无工程'
    }
    async function browse(path) {
      const id = ++ticket
      const data = await request('/api/catalog/directories?' + new URLSearchParams({ path }))
      if (closed || id !== ticket) return
      browsing = data; pathInput.value = data.path
      panel.querySelector('[data-parent]').disabled = !data.parent
      folders.replaceChildren(...data.directories.map(item => {
        const button = document.createElement('button'); button.textContent = item.name + ' /'
        button.onclick = guarded(() => browse(item.path)); return button
      }))
      if (data.truncated) error.textContent = '目录项目过多，请缩小范围'
    }
    panel.querySelector('[data-close]').onclick = () => panel.close()
    panel.querySelector('[data-go]').onclick = guarded(() => browse(pathInput.value.trim()))
    pathInput.onkeydown = event => { if (event.key === 'Enter') panel.querySelector('[data-go]').click() }
    panel.querySelector('[data-parent]').onclick = guarded(() => browse(browsing.parent))
    panel.querySelector('[data-use]').onclick = guarded(async () => {
      catalog = await request('/api/catalog/root', { path: pathInput.value.trim() })
      showProjects()
      const direct = catalog.projects.find(item => item.path === catalog.root + '/studio.project.json')
      if (direct) await openProject(direct)
      else { panel.querySelector('.project-browser').open = false; if (!catalog.projects.length) panel.querySelector('[data-create]').click() }
    })
    panel.querySelector('[data-create]').onclick = () => dialog({ title: '创建工程', detail: pathInput.value, fields: [{ name: 'name', label: '工程名称', value: pathInput.value.split('/').filter(Boolean).at(-1), maxLength: 100 }], accept: '创建并打开', onSubmit: async ({ name }) => {
      const value = await request('/api/catalog/create', { path: pathInput.value.trim(), name })
      await navigate(value.url)
    } })
    document.body.append(panel); panel.showModal(); showProjects()
    panel.addEventListener('close', () => { closed = true; ticket++; panel.remove() }, { once: true })
    await browse(catalog.root)
  }
  const files = mountFiles({ request, project: api.project, currentClip: api.current, beforeProjectChanged: api.prepare, onProjectChanged: api.reload })
  find('rename-action').onclick=()=>{const clip=api.current();if(!clip)return;dialog({title:'重命名动作类型',fields:[{name:'name',label:'类型名称',value:clip.actionName || clip.library?.actionName || clip.name}],onSubmit:async({name})=>{await api.prepare();await request('/api/files/action',{operation:'rename',scope:'family',clipId:clip.id,name,fingerprint:api.project.fingerprint});await api.reload(clip.id)}})}
  find('remove-action').onclick=()=>{const clip=api.current();if(!clip)return;dialog({title:'移除动作',detail:clip.name,accept:'移除',danger:true,onSubmit:async()=>{await api.prepare();await request('/api/files/action',{operation:'remove',clipId:clip.id,fingerprint:api.project.fingerprint});await api.reload('')}})}
  function setMode(mode) {
    api.pause()
    for (const frame of toolFrames.values()) frame.contentWindow.motionStudioBridge?.pause?.()
    find('settings-dialog').close()
    find('return-motion').hidden = mode === 'motion'
    find('workbench').dataset.mode = mode
    find('workspace-name').textContent = { motion: '动作', files: '工程文件', sampling: '骨骼参考', comparison: '逐帧对照' }[mode]
    find('toggle-inspector').hidden = ['sampling', 'comparison'].includes(mode)
    for (const button of document.querySelectorAll('[data-workspace]')) button.setAttribute('aria-current', String(button.dataset.workspace === mode))
    find('motion-workspace').hidden = mode !== 'motion'
    find('files-workspace').hidden = mode !== 'files'
    find('tool-workspace').hidden = !['sampling', 'comparison'].includes(mode)
    find('file-inspector').hidden = mode !== 'files'
    find('inspector').querySelector('.inspector-tabs').hidden = mode !== 'motion'
    for (const section of document.querySelectorAll('[data-inspector-panel]')) section.hidden = mode !== 'motion' || section.dataset.inspectorPanel !== document.querySelector('[data-inspector][aria-selected=true]')?.dataset.inspector
    if (mode === 'files') guard(files.refresh)()
    find('tool-empty').hidden = true
    if (['sampling','comparison'].includes(mode)) {
      const configured = mode === 'sampling' ? api.project.samplingConfigured : api.project.comparisonConfigured
      if (!configured) {
        for (const frame of toolFrames.values()) frame.hidden = true
        find('tool-empty').hidden = false
        find('tool-empty').querySelector('h2').textContent = mode === 'sampling' ? '全局骨骼参考库' : '逐帧对照'
        find('tool-empty').querySelector('p').textContent = mode === 'sampling' ? '参考库由工作台统一管理。把动作来源登记到资源库 studio.tools.json，即可供所有角色使用。' : '选择已配置冻结方案与人物帧映射的工程进行对照。对照工具通用，映射和审阅结果保存在对应工程内。'
        return
      }
      for (const frame of toolFrames.values()) frame.hidden = true
      if (!toolFrames.has(mode)) {
        const frame = document.createElement('iframe')
        frame.title = mode === 'sampling' ? '骨骼选帧' : '逐帧对照'
        frame.src = studioUrl(`/${mode}.html?embedded=1`)
        find('tool-workspace').append(frame); toolFrames.set(mode, frame)
      }
      toolFrames.get(mode).hidden = false
    }
    window.dispatchEvent(new Event('resize'))
  }
  find('tool-frame').remove()
  find('project-picker').onclick = guard(chooseProject)
  find('browse-assets').onclick = guard(async () => { setMode('files'); await files.importSources() })
  find('empty-import').onclick = find('browse-assets').onclick
  for (const button of document.querySelectorAll('[data-workspace]')) button.onclick = () => setMode(button.dataset.workspace)
  find('tool-open-project').onclick = guard(chooseProject)
  find('return-motion').onclick = () => setMode('motion')
  const settings = find('settings-dialog')
  function openSettings() { api.pause(); settings.showModal() }
  find('open-settings').onclick = openSettings
  find('close-settings').onclick = () => settings.close()
  find('settings-choose-project').onclick = guard(async () => { settings.close(); await chooseProject() })
  for (const button of document.querySelectorAll('[data-settings-tab]')) button.onclick = () => {
    for (const item of document.querySelectorAll('[data-settings-tab]')) item.setAttribute('aria-current', String(item === button))
    for (const panel of document.querySelectorAll('[data-settings-panel]')) panel.hidden = panel.dataset.settingsPanel !== button.dataset.settingsTab
  }
  for (const button of document.querySelectorAll('[data-settings-workspace]')) button.onclick = () => setMode(button.dataset.settingsWorkspace)
  window.motionStudioCommands = { async prepareClose() {
    if (hasDialogInput()) throw Error('请先保存或取消当前对话框中的编辑，再关闭工作台。')
    await preserveAll()
  }, run(name) {
    if (hasDialogInput()) return toast('请先保存或取消当前对话框中的编辑。', true)
    if (['motion', 'files', 'sampling', 'comparison'].includes(name)) return setMode(name)
    if (name === 'settings') return openSettings()
    if (name === 'projects') { settings.close(); return guard(chooseProject)() }
    const id = { save: 'save', export: 'open-export', import: 'browse-assets', inspector: 'toggle-inspector', refresh: 'refresh-project' }[name]
    if (id) { settings.close(); find(id).click() }
  } }

  try {
    const listing = await request('/api/catalog')
    find('settings-root').textContent = listing.root
    find('project-list').replaceChildren(...listing.projects.map(item => {
      const button = document.createElement('button')
      button.textContent = item.name
      button.setAttribute('aria-current', String(item.id === listing.currentProjectId))
      button.onclick = guard(() => openProject(item))
      return button
    }))
  } catch (error) { toast(error.message, true) }
  let split = null, splitKey = ''
  function resizePanels() {
    const key = innerWidth > 1000 ? `${find('inspector').hidden ? 'two' : 'three'}/${find('workbench').dataset.mode || 'motion'}` : ''
    if (key === splitKey) return
    split?.destroy(); split = null; splitKey = key
    if (!key) return
    const panels = find('inspector').hidden ? ['#asset-sidebar', '#center-pane'] : ['#asset-sidebar', '#center-pane', '#inspector']
    split = Split(panels, { sizes: panels.length === 2 ? [21, 79] : [21, 58, 21], minSize: panels.length === 2 ? [220, 330] : [220, 330, 230], gutterSize: 6,
      onDrag: () => window.dispatchEvent(new Event('resize')),
      elementStyle: (_dimension, size, gutterSize) => ({ width: `calc(${size}% - ${gutterSize}px)`, flex: 'none' }) })
  }
  resizePanels()
  window.addEventListener('resize', resizePanels)
  try {
    const listing = await request('/api/files')
    find('project-root').textContent = listing.root
    find('project-picker').title = listing.root
  } catch (error) { toast(error.message, true) }
  window.addEventListener('pagehide', () => { delete window.motionStudioCommands; disposed = true; controller.abort(); window.removeEventListener('resize', resizePanels); split?.destroy(); files.dispose() }, { once: true })
  return { setMode, chooseProject }
}
