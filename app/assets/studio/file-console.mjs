import { studioFetch, studioUrl, clipLibrary, dialog, toast } from './controls.mjs'

export function mountFiles({ request, project, currentClip, beforeProjectChanged, onProjectChanged }) {
  const find = id => document.getElementById(id)
  let current = '', entries = [], selected = null, selectedInfo = null, ticket = 0, disposed = false
  const checked = new Set()
  const labelBytes = count => count < 1024 ? `${count} B` : count < 1048576 ? `${(count/1024).toFixed(1)} KB` : `${(count/1048576).toFixed(1)} MB`
  const assetUrl = path => studioUrl('/project/' + path.split('/').map(encodeURIComponent).join('/'))
  const join = name => current ? `${current}/${name}` : name
  const guard = fn => async (...args) => { try { await fn(...args) } catch (error) { if (!disposed && error.name !== 'AbortError') toast(error.message, true) } }
  function updateSelection() {
    find('asset-selection-count').textContent = checked.size ? `已选择 ${checked.size} 张` : `${entries.length} 项`
    find('asset-sequence').disabled = !checked.size
  }
  async function inspect(entry) {
    selected = entry; selectedInfo = null
    find('inspector').hidden = false
    find('toggle-inspector').setAttribute('aria-expanded', 'true')
    document.body.classList.add('inspector-open')
    window.dispatchEvent(new Event('resize'))
    const id = ++ticket
    find('file-name').textContent = entry.name
    find('file-meta').textContent = ''
    find('file-preview').replaceChildren()
    find('file-actions').hidden = entry.kind === 'directory'
    for (const button of find('file-actions').querySelectorAll('button')) button.disabled = true
    if (entry.kind === 'directory') {
      find('file-meta').textContent = entry.referenced ? '工程引用中' : '目录'
      find('file-actions').hidden = false
      find('file-rename').disabled = entry.referenced
      find('file-delete').disabled = entry.referenced
      return
    }
    const data = await request('/api/files/inspect?' + new URLSearchParams({ path: entry.path }))
    if (disposed || id !== ticket) return
    selectedInfo = data
    if (entry.kind === 'image') { const image = new Image(); image.src = assetUrl(entry.path); image.alt = entry.name; find('file-preview').append(image) }
    if (data.text !== undefined) { const pre = document.createElement('pre'); pre.textContent = data.text.slice(0,3000); find('file-preview').append(pre) }
    find('file-meta').textContent = `${labelBytes(data.bytes)}${data.size ? ' · ' + data.size.join(' × ') : ''}${data.referenced ? ' · 工程引用中' : ''}`
    find('file-edit').disabled = data.text === undefined || data.referenced
    find('file-rename').disabled = data.referenced
    find('file-delete').disabled = data.referenced
  }
  function render() {
    const query = find('asset-search').value.trim().toLowerCase()
    const grid = find('asset-grid')
    grid.replaceChildren(...entries.filter(item => item.name.toLowerCase().includes(query)).map(entry => {
      const card = document.createElement('article')
      card.className = 'asset-card'; card.tabIndex = 0; card.setAttribute('role','button'); card.setAttribute('aria-label', entry.name)
      card.setAttribute('aria-selected', String(checked.has(entry.path)))
      if (entry.kind === 'image') {
        const image = new Image(); image.src = assetUrl(entry.path); image.alt = ''; image.loading = 'lazy'; card.append(image)
        const check = document.createElement('input'); check.type = 'checkbox'; check.checked = checked.has(entry.path); check.setAttribute('aria-label', `选择 ${entry.name}`)
        check.onclick = event => event.stopPropagation()
        check.onchange = () => { check.checked ? checked.add(entry.path) : checked.delete(entry.path); card.setAttribute('aria-selected',String(check.checked)); updateSelection() }
        card.append(check)
      } else {
        const symbol = document.createElement('span'); symbol.className = `file-symbol ${entry.kind === 'directory' ? 'directory-symbol' : 'document-symbol'}`; symbol.textContent = entry.kind === 'directory' ? '/' : entry.name.split('.').at(-1).toUpperCase().slice(0,5); card.append(symbol)
      }
      const name = document.createElement('strong'), meta = document.createElement('small')
      name.textContent = entry.name; name.title = entry.name
      meta.textContent = entry.kind === 'directory' ? '目录' : `${labelBytes(entry.bytes)}${entry.referenced ? ' · 已引用' : ''}`
      card.append(name,meta)
      if (entry.kind === 'directory') {
        const manage = document.createElement('button'); manage.className = 'directory-manage'; manage.textContent = '···'; manage.title = '目录属性'; manage.setAttribute('aria-label', `${entry.name} 目录属性`)
        manage.onclick = guard(async event => { event.stopPropagation(); await inspect(entry) }); card.append(manage)
      }
      card.onclick = guard(async () => { if (entry.kind === 'directory') { current = entry.path; checked.clear(); await refresh() } else await inspect(entry) })
      card.ondblclick = event => event.preventDefault()
      card.onkeydown = event => { if (event.key === 'Enter') card.click() }
      return card
    }))
    if (!grid.children.length) { const empty = document.createElement('p'); empty.className='muted'; empty.textContent = query ? '没有匹配的文件' : '目录为空'; grid.append(empty) }
    const crumbs = [{ label: '资源', path: '' }]
    const parts = current.split('/').filter(Boolean)
    parts.forEach((part,index) => crumbs.push({ label:part,path:parts.slice(0,index+1).join('/') }))
    find('asset-breadcrumb').replaceChildren(...crumbs.map(crumb => { const button=document.createElement('button');button.textContent=crumb.label;button.onclick=guard(async()=>{current=crumb.path;checked.clear();await refresh()});return button }))
    find('asset-parent').disabled = !current
    find('asset-upload').title = `导入到 ${current || '工程根目录'}`
    updateSelection()
  }
  async function refresh() {
    const id = ++ticket
    const data = await request('/api/files?' + new URLSearchParams({ path: current }))
    if (disposed || id !== ticket) return
    entries = data.entries; current = data.path === '.' ? '' : data.path
    for (const name of checked) if (!entries.some(e=>e.path===name)) checked.delete(name)
    render()
  }
  find('asset-search').oninput=render
  find('asset-parent').onclick=guard(async()=>{current=current.split('/').slice(0,-1).join('/');checked.clear();await refresh()})
  find('asset-upload').onclick=()=>find('asset-upload-input').click()
  find('asset-upload-input').onchange=guard(async()=>{
    const input=find('asset-upload-input'), files=[...input.files]
    let imported=0
    try {
      for(const file of files) {
        if(file.size>24*1048576)throw Error(`${file.name} 超过 24 MiB`)
        const content=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=()=>reject(Error('文件读取失败'));reader.readAsDataURL(file)})
        await request('/api/files/upload',{path:join(file.name),content});imported++
        find('asset-selection-count').textContent=`导入 ${imported} / ${files.length}`
      }
      toast(`已导入 ${imported} 个文件`)
    } finally {input.value='';await refresh()}
  })
  find('asset-new-folder').onclick=()=>{
    const popup=dialog({title:'新建目录',fields:[{name:'name',label:'名称',value:'新目录'}],accept:'创建',onSubmit:async({name})=>{await request('/api/files/mkdir',{path:join(name)});await refresh()}})
    const file=document.createElement('button');file.type='button';file.textContent='新建文本文件'
    file.onclick=()=>{popup.close();dialog({title:'新建文件',fields:[{name:'name',label:'名称',value:'notes.md'},{name:'text',label:'内容',type:'textarea',value:'',required:false}],accept:'创建',onSubmit:async({name,text})=>{await request('/api/files/write',{path:join(name),text});await refresh()}})}
    popup.querySelector('.dialog-actions').prepend(file)
  }
  find('file-edit').onclick=()=>{
    const entry=selected,info=selectedInfo
    if(!info||info.referenced)return
    dialog({title:'编辑文件',detail:entry.path,fields:[{name:'text',label:'内容',type:'textarea',value:info.text,required:false}],onSubmit:async({text})=>{await request('/api/files/write',{path:entry.path,text,sha256:info.sha256});await refresh();await inspect(entry);toast('已保存文件')}})
  }
  find('file-rename').onclick=()=>{
    const entry=selected,info=selectedInfo
    if(!entry||entry.referenced)return
    dialog({title:'重命名',fields:[{name:'name',label:'名称',value:entry.name}],onSubmit:async({name})=>{const target=entry.path.split('/').slice(0,-1).concat(name).join('/');await request('/api/files/rename',{path:entry.path,target,sha256:info?.sha256});selected=null;await refresh();find('file-name').textContent=name}})
  }
  find('file-delete').onclick=()=>{
    const entry=selected,info=selectedInfo
    if(!entry||entry.referenced)return
    dialog({title:'移至回收站',detail:entry.path,accept:'移入',danger:true,onSubmit:async()=>{await request('/api/files/trash',{path:entry.path,sha256:info?.sha256});selected=null;find('file-preview').replaceChildren();find('file-name').textContent='';find('file-meta').textContent='';find('file-actions').hidden=true;await refresh();toast('已移至回收站')}})
  }
  find('asset-trash').onclick=guard(async()=>{
    const data=await request('/api/files/trash')
    const panel=document.createElement('dialog');panel.className='studio-dialog';panel.innerHTML='<div class="dialog-header"><h2>回收站</h2><button aria-label="关闭">×</button></div><div class="trash-list"></div><p class="dialog-error" role="alert"></p>'
    panel.querySelector('button').onclick=()=>panel.close()
    const list=panel.querySelector('.trash-list')
    for(const item of data.entries){const row=document.createElement('div');row.className='trash-row';const name=document.createElement('span');name.textContent=`${item.path}${item.operation==='replace'?' · 修改前版本':''}`;const restore=document.createElement('button');restore.textContent='恢复';restore.onclick=()=>dialog({title:'恢复文件',fields:[{name:'target',label:'恢复路径',value:item.path}],accept:'恢复',onSubmit:async({target})=>{await request('/api/files/restore',{id:item.id,target});row.remove();await refresh()}});row.append(name,restore);list.append(row)}
    if(!data.entries.length)list.textContent='回收站为空'
    document.body.append(panel);panel.showModal();panel.onclose=()=>panel.remove()
  })
  find('asset-sequence').onclick=()=>{
    const files=entries.filter(entry=>checked.has(entry.path)).map(entry=>entry.path).sort((a,b)=>a.localeCompare(b,undefined,{numeric:true}))
    const active = currentClip?.()
    const meta = active ? clipLibrary(active, project) : { characterId: project.id, characterName: project.name, variantId: 'default', variantName: '默认外观' }
    const group = '角色、衣装与动作标识'
    const machine = { group, pattern: '[a-z0-9]+(-[a-z0-9]+)*', maxLength: 64 }
    dialog({ title: '创建动作', detail: `${files.length} 张序列帧`, fields: [
      { name: 'name', label: '动作名称', value: current.split('/').at(-1) || '新动作', maxLength: 100 },
      { name: 'duration', label: '每帧时长 · ms', type: 'number', min: 1, max: 60000, value: 120 },
      { ...machine, name: 'characterId', label: '角色标识', value: meta.characterId },
      { group, name: 'characterName', label: '角色名称', value: meta.characterName, maxLength: 100 },
      { ...machine, name: 'variantId', label: '衣装标识', value: meta.variantId },
      { group, name: 'variantName', label: '衣装名称', value: meta.variantName, maxLength: 100 },
      { ...machine, name: 'actionId', label: '动作标识', value: 'custom-action' },
      { group, name: 'direction', label: '朝向（可留空）', value: '', required: false, pattern: 'N|NE|E|SE|S|SW|W|NW', placeholder: 'N / NE / E / SE / S / SW / W / NW' }
    ], accept: '创建', onSubmit: async ({ name, duration, characterId, characterName, variantId, variantName, actionId, direction }) => {
      await beforeProjectChanged()
      const metadata = { characterId, characterName, variantId, variantName, actionId, actionName: name, direction: direction || null }
      const result = await request('/api/files/sequence', { files, name, durationMs: Number(duration), fingerprint: project.fingerprint, metadata })
      await onProjectChanged(result.clipId)
    } })
  }
  async function importSources() {
    let path = ''
    for (const name of ['sources', 'imports']) {
      const listing = await request('/api/files?' + new URLSearchParams({ path }))
      const entry = listing.entries.find(entry => entry.name === name)
      const next = path ? `${path}/${name}` : name
      if (!entry) await request('/api/files/mkdir', { path: next })
      else if (entry.kind !== 'directory') throw Error(`${next} 已被文件占用，请选择其他导入目录`)
      path = next
    }
    current = path; checked.clear(); await refresh()
    find('asset-upload-input').click()
  }
  return {refresh,importSources,dispose:()=>{disposed=true;ticket++}}
}
