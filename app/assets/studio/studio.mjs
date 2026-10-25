import { studioFetch, studioUrl, clipLibrary, mountChrome, refreshChoices, toast } from './controls.mjs'

const $ = id => document.getElementById(id)
const ctx = $('canvas').getContext('2d')
let project, clips, clip, index = 0, elapsed = 0, playing = false, previous = null, raf = 0, disposed = false, loadId = 0
let images = new Map()
let finished = false
let chrome = null
const pageRequests = new AbortController()
let exported = null, previewMode = 'original', exportAbort = null, exportTicket = 0
const blobUrls = new Set()
const notice = text => { $('notice').textContent = text; if (/失败|无效|不能|错误|已变化/.test(text)) toast(text, true) }
const clone = value => JSON.parse(JSON.stringify(value))
const stateKey = () => `character-motion-studio:${project.id}:${project.fingerprint}`
function editable(c) {
  return { loop: c.loop, steps: c.steps.map(s => ({ slot: s.slot, durationMs: s.durationMs, offset: [...s.offset] })) }
}
function draft() {
  return { version: 1, projectId: project.id, fingerprint: project.fingerprint,
    clips: Object.fromEntries(clips.map(c => [c.id, editable(c)])) }
}
function pendingForm() {
  if (!clip) return null
  const step = clip.steps[index]
  const values = [$('duration').value, $('dx').value, $('dy').value]
  if (values.every((value, i) => Number(value) === [step.durationMs, ...step.offset][i])) return null
  return { clipId: clip.id, index, values }
}
async function saveLocalDraft() {
  const data = { ...draft(), pendingForm: pendingForm() }
  localStorage.setItem(stateKey(), JSON.stringify(data))
  if (!project.exportReadOnly) {
    const response = await studioFetch('/api/files/draft', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data), signal: pageRequests.signal })
    if (!response.ok) { const result = await response.json(); throw Error(result.error || '工程保存失败') }
  }
}
async function restoreLocalDraft() {
  let data = null
  if (!project.exportReadOnly) {
    const response = await studioFetch('/api/files/draft', { signal: pageRequests.signal })
    if (response.ok) data = await response.json()
  }
  if (!data) { const saved = localStorage.getItem(stateKey()); if (saved) data = JSON.parse(saved) }
  if (!data) throw Error('没有可恢复的调整')
  await acceptDraft(data)
  const form = data.pendingForm
  if (form && clips.some(item => item.id === form.clipId) && Number.isInteger(form.index) && form.index >= 0 && Array.isArray(form.values) && form.values.length === 3) {
    await selectClip(form.clipId)
    seek(Math.min(form.index, clip.steps.length - 1))
    for (const [i, id] of ['duration', 'dx', 'dy'].entries()) $(id).value = form.values[i]
    notice('已恢复本机草稿与尚未应用的本段输入；请核对后应用。')
  } else notice('已恢复草稿。')
}
function catalogPending() {
  if (!project || !clips) return null
  const value = { ...draft(), pendingForm: pendingForm() }
  const source = Object.fromEntries(project.clips.map(item => [item.id, editable(item)]))
  if (!value.pendingForm && JSON.stringify(value.clips) === JSON.stringify(source)) return null
  try { if (JSON.stringify(JSON.parse(localStorage.getItem(stateKey()))) === JSON.stringify(value)) return null } catch { /* A new save will report storage errors. */ }
  return { message: '当前人物工作台有未保存调整或尚未应用的本段输入。保留到此工程的本机草稿后再打开；返回时点击“恢复本机草稿”继续。', preserve: saveLocalDraft }
}
function markEdited() {
  if (!exported) return
  const wasExport = previewMode === 'export'
  exported.stale = true
  previewMode = 'original'
  $('export-result').dataset.stale = 'true'
  $('export-status').textContent = '调整已变化。请重新导出后比较；上次图包仍可下载。'
  updateComparison()
  if (wasExport) loadShownImages()
}
function activeClip() { return previewMode === 'export' && !exported?.stale ? exported?.project.clips.find(c => c.id === clip.id) || clip : clip }
function normalizeProject(value, base = document.baseURI) {
  const data = clone(value)
  for (const c of data.clips || []) for (const f of c.frames) f.url = new URL(f.url, base).href
  for (const link of data.links || []) link.url = new URL(link.url, base).href
  return data
}
function updateComparison() {
  const available = !exported?.stale && exported?.project.clips.some(c => c.id === clip?.id)
  $('compare-bar').hidden = !available
  if (!available) previewMode = 'original'
  $('view-original').setAttribute('aria-pressed', String(previewMode === 'original'))
  $('view-export').setAttribute('aria-pressed', String(previewMode === 'export'))
  $('compare-label').textContent = exported?.label || ''
}
function sourcePaths() {
  $('project-path').textContent = project.projectPath || project.sourcePath || project.rootDirectory || `项目 ${project.id}`
  $('clip-identity').textContent = `动作 ID：${clip.id}`
  $('source-paths').replaceChildren(...[...new Set(clip.frames.map(f => f.sourcePath || f.file || f.url))].map(path => {
    const li = document.createElement('li'); li.textContent = path; return li
  }))
}
function acceptDraft(data) {
  if (data.version !== 1 || data.projectId !== project.id || data.fingerprint !== project.fingerprint) throw Error('项目或源图已变化，不能沿用旧调整。')
  if (!data.clips || Object.keys(data.clips).sort().join('|') !== project.clips.map(c => c.id).sort().join('|')) throw Error('动作列表不匹配。')
  const next = clone(project.clips)
  for (const c of next) {
    const edit = data.clips[c.id]
    if (typeof edit?.loop !== 'boolean' || !Array.isArray(edit.steps) || edit.steps.length !== c.steps.length) throw Error('无效动作草稿。')
    const slots = edit.steps.map(s => s.slot)
    if (slots.some(s => !Number.isInteger(s)) || [...slots].sort((a,b) => a-b).some((s,i) => s !== i)) throw Error('播放段丢失或重复。')
    if (!c.canReorder && slots.some((s,i) => s !== i)) throw Error('带事件或根运动的动作不能直接重排。')
    c.steps = edit.steps.map(s => {
      if (!Number.isInteger(s.durationMs) || s.durationMs < 1 || s.durationMs > 60000 || !Array.isArray(s.offset) || s.offset.length !== 2 || s.offset.some(n => !Number.isFinite(n) || Math.abs(n) > Math.max(...c.canvas))) throw Error('时长或偏移无效。')
      return { ...c.steps[s.slot], durationMs: s.durationMs, offset: [...s.offset] }
    })
    c.loop = edit.loop
  }
  clips = next
  markEdited()
  return selectClip($('clip').value)
}
function sync() {
  const s = clip.steps[index]
  $('duration').value = s.durationMs
  $('dx').value = s.offset[0]
  $('dy').value = s.offset[1]
  $('loop').checked = clip.loop
  $('seek').max = clip.steps.length - 1
  $('seek').value = index
  $('earlier').disabled = !clip.canReorder || index === 0
  $('later').disabled = !clip.canReorder || index === clip.steps.length - 1
  $('source').textContent = `源姿态 ${clip.frames[s.frame].id} · 原播放段 ${s.slot + 1}`
  $('frame-number').textContent = String(index + 1).padStart(2, '0')
  chrome?.update()
  renderTimeline()
  render()
}
function renderTimeline() {
  if (!clip) return
  const shown = activeClip()
  $('timeline').replaceChildren(...shown.steps.map((s,i) => {
    const b = document.createElement('button')
    const thumb = document.createElement('canvas'), label = document.createElement('span')
    thumb.width = 96; thumb.height = 96
    const frame = shown.frames[s.frame], image = images.get(frame.url)
    if (image) {
      const [x,y,x2,y2] = frame.rect, [dx,dy,dw,dh] = frame.drawRect || [0,0,...shown.canvas]
      const scale = Math.min(96/shown.canvas[0],96/shown.canvas[1])
      thumb.getContext('2d').drawImage(image,x,y,x2-x,y2-y,(dx+s.offset[0])*scale,(dy+s.offset[1])*scale,dw*scale,dh*scale)
    }
    label.textContent = `${i+1} · ${s.durationMs} ms`
    b.append(thumb,label)
    b.setAttribute('aria-current', String(i === index))
    b.onclick = () => { seek(i); if ($('inspector').hidden) $('toggle-inspector').click() }
    return b
  }))
}
function seek(i) { finished = false; playing = false; index = Math.max(0,Math.min(clip.steps.length-1,i)); elapsed = 0; sync() }
async function selectClip(id) {
  finished = false; playing = false; index = 0; elapsed = 0
  clip = clips.find(c => c.id === id)
  if (!clip) throw Error('找不到此动作。')
  updateComparison()
  sourcePaths()
  sync()
  await loadShownImages()
}
async function loadShownImages() {
  const ticket = ++loadId
  images = new Map()
  render()
  const pending = new Map()
  try {
    await Promise.all([...new Set(activeClip().frames.map(f => f.url))].map(async url => {
      const im = new Image()
      im.src = url
      await im.decode()
      pending.set(url,im)
    }))
    if (disposed || ticket !== loadId) return
    images = pending
    renderTimeline()
    render()
  } catch (error) { if (ticket === loadId) notice(`图片读取失败：${error.message}`) }
}
function render() {
  if (!clip) return
  const view = $('stage').getBoundingClientRect(), dpr = devicePixelRatio || 1
  const canvas = $('canvas'), w = view.width, h = view.height
  if (canvas.width !== Math.round(w*dpr) || canvas.height !== Math.round(h*dpr)) { canvas.width = Math.round(w*dpr); canvas.height = Math.round(h*dpr) }
  ctx.setTransform(dpr,0,0,dpr,0,0)
  ctx.fillStyle = $('background').value === 'dark' ? '#26352f' : $('background').value === 'light' ? '#f1ecdb' : '#e9eadd'
  ctx.fillRect(0,0,w,h)
  if ($('background').value === 'checker') {
    ctx.fillStyle = '#d9dfd1'
    for (let y=0;y<h;y+=24) for(let x=0;x<w;x+=24) if ((x/24+y/24)%2 === 0) ctx.fillRect(x,y,24,24)
  }
  const shown = activeClip()
  const zoom = $('size').value === 'auto' ? Math.min(w/shown.canvas[0]*.92,h/shown.canvas[1]*.92) : Math.min(Number($('size').value)/shown.canvas[1],w/shown.canvas[0]*.98,h/shown.canvas[1]*.98)
  const bx = (w-shown.canvas[0]*zoom)/2, by = (h-shown.canvas[1]*zoom)/2
  function draw(i, alpha) {
    const s=shown.steps[i], f=shown.frames[s.frame], im=images.get(f.url)
    if (!im) return
    const [x,y,x2,y2]=f.rect
    const [dx,dy,dw,dh]=f.drawRect || [0,0,...shown.canvas]
    ctx.globalAlpha=alpha
    ctx.drawImage(im,x,y,x2-x,y2-y,bx+(dx+s.offset[0])*zoom,by+(dy+s.offset[1])*zoom,dw*zoom,dh*zoom)
    ctx.globalAlpha=1
  }
  if ($('ghost').checked) draw(index ? index-1 : clip.loop ? clip.steps.length-1 : 0,.2)
  draw(index,1)
  const s=shown.steps[index], f=shown.frames[s.frame]
  const originColor='#956b32', markerColor=name=>name.startsWith('Left')?'#bd6148':name.startsWith('Right')?'#167ea2':'#3c6754'
  const markers=Object.entries(f.markers || {})
  const originLabel=shown.originLabel || '导出原点'
  const markerLabel=name=>shown.markerLabels?.[name] || name
  const coordinate=p=>`(${p.map(n=>Number(n.toFixed(1))).join(', ')})`
  const legend=[]
  function legendItem(label,p,color,type) {
    const row=document.createElement('div'), symbol=document.createElement('span')
    symbol.className=`coordinate-symbol ${type}`;symbol.style.color=color
    const text=document.createElement('span');text.textContent=`${label} ${coordinate(p)}`
    row.append(symbol,text);legend.push(row)
  }
  function labelAt(text,x,y,color) {
    ctx.save();ctx.font='12px system-ui, sans-serif'
    const width=ctx.measureText(text).width+12
    const left=Math.max(4,Math.min(w-width-4,x)),top=Math.max(4,Math.min(h-24,y))
    ctx.fillStyle='#edf0e8ed';ctx.fillRect(left,top,width,22)
    ctx.fillStyle=color;ctx.textBaseline='middle';ctx.fillText(text,left+6,top+11);ctx.restore()
  }
  if ($('guides').checked) {
    const ox=bx+shown.origin[0]*zoom,oy=by+shown.origin[1]*zoom
    ctx.save();ctx.strokeStyle=originColor;ctx.lineWidth=1;ctx.setLineDash([5,5]);ctx.beginPath()
    ctx.moveTo(ox,0);ctx.lineTo(ox,h);ctx.moveTo(0,oy);ctx.lineTo(w,oy);ctx.stroke()
    ctx.setLineDash([]);ctx.strokeRect(ox-4,oy-4,8,8);ctx.restore()
    labelAt(originLabel,ox+10,oy+9,originColor)
    legendItem(originLabel,shown.origin,originColor,'origin')
  }
  if ($('markers').checked) {
    for (const [name,p] of markers) {
      const pos=[p[0]+s.offset[0],p[1]+s.offset[1]],color=markerColor(name)
      const x=bx+pos[0]*zoom,y=by+pos[1]*zoom
      ctx.fillStyle=color;ctx.strokeStyle='#edf0e8';ctx.lineWidth=1.5
      ctx.beginPath();ctx.arc(x,y,4,0,Math.PI*2);ctx.fill();ctx.stroke()
      if (markers.length<=4) labelAt(markerLabel(name),x+12,y-28,color)
      legendItem(markerLabel(name),pos,color,'marker')
    }
  }
  $('coordinate-legend').replaceChildren(...legend)
  $('coordinate-legend').hidden=!legend.length
  if ($('guides').checked || s.offset.some(v=>v!==0)) {
    ctx.strokeStyle=s.offset.some(v=>v!==0)?'#c27a64':'#9aafa3'
    ctx.strokeRect(bx,by,shown.canvas[0]*zoom,shown.canvas[1]*zoom)
  }
  $('play').textContent=playing?'暂停':finished?'重播':'播放'
  $('readout').textContent=`${index+1} / ${clip.steps.length} · ${clip.steps.reduce((a,b)=>a+b.durationMs,0)} ms`
}
function tick(time) {
  const delta=previous===null?0:Math.min(time-previous,250)
  previous=time
  if (playing && !document.hidden && images.size) {
    elapsed+=delta*Number($('speed').value)
    while(elapsed>=clip.steps[index].durationMs) {
      elapsed-=clip.steps[index].durationMs
      if(index<clip.steps.length-1) index++
      else if(clip.loop) index=0
      else {playing=false;finished=true;elapsed=0;break}
      sync()
    }
    render()
  }
  if(!disposed) raf=requestAnimationFrame(tick)
}
function download() {
  const url=URL.createObjectURL(new Blob([JSON.stringify(draft(),null,2)],{type:'application/json'}))
  blobUrls.add(url)
  const a=document.createElement('a');a.href=url;a.download=`${project.id}.studio-draft.json`;a.click()
  setTimeout(()=>{URL.revokeObjectURL(url);blobUrls.delete(url)},1000)
  notice('已下载调整 JSON；源图和项目文件未修改。')
}
function guarded(fn) { return async () => { try {await fn()} catch(error) {notice(error.message)} } }
function bytes(value) {
  if (!Number.isFinite(value)) return '—'
  if (value < 1024) return `${Math.round(value)} B`
  const unit = value >= 1048576 ? 1048576 : 1024
  return `${(value/unit).toLocaleString('zh-CN',{maximumFractionDigits:1})} ${unit === 1024?'KB':'MB'}`
}
function exportOptions() {
  const preset = document.querySelector('[name="export-preset"]:checked').value
  const quality = $('export-quality').disabled ? 100 : Number($('export-quality').value)
  if (!Number.isInteger(quality) || quality < 1 || quality > 100) throw Error('WebP 质量请填 1 到 100 的整数。')
  const scope = $('export-scope').value
  const current = clipLibrary(clip, project)
  const selected = scope === 'current' ? [clip] : scope === 'variant' ? clips.filter(item => { const meta = clipLibrary(item, project); return meta.characterId === current.characterId && meta.variantId === current.variantId }) : clips
  return {preset, clipIds: selected.map(item => item.id),
    scale:Number($('export-scale').value),format:$('export-format').value,quality,
    maxPageSize:Number($('export-page-size').value),includeFrames:$('export-frames').checked,
    includePreview:$('export-preview-file').checked,includeProofs:$('export-proofs').checked}
}
function metricRow(label, before, after) {
  const row=document.createElement('tr')
  for (const text of [label,bytes(before),bytes(after)]) {const cell=document.createElement('td');cell.textContent=text;row.append(cell)}
  return row
}
function showExportResult(data, options, stale) {
  const wasExport=previewMode==='export'
  previewMode='original'
  const base=new URL(data.manifestUrl,document.baseURI)
  exported={...data,project:normalizeProject(data.previewProject,base),stale,
    label:`${options.format.toUpperCase()} · ${Math.round(options.scale*100)}% · ${options.preset==='lossless'||options.format==='png'?'无损':`质量 ${options.quality}`}`}
  const report=data.report || {}, table=document.createElement('table')
  table.className='metric-table';table.setAttribute('aria-label','原图与压缩图包的体积比较')
  const head=document.createElement('thead'), row=document.createElement('tr')
  for (const label of ['','源资产','导出图集']) {const th=document.createElement('th');th.textContent=label;th.scope='col';row.append(th)}
  head.append(row);table.append(head)
  const body=document.createElement('tbody')
  body.append(metricRow('文件体积',report.sourceFileBytes,report.outputAtlasBytes))
  body.append(metricRow('解码 RGBA 估算',report.sourceDecodedRGBABytes,report.estimatedDecodedAtlasRGBABytes))
  table.append(body)
  const summary=document.createElement('p');summary.className='export-counts'
  summary.textContent=`${report.clipCount ?? options.clipIds.length} 个动作 · ${report.uniqueSpriteCount ?? '—'} 个独立图块 · ${report.pageCount ?? '—'} 页图集${Number.isFinite(report.zipBytes)?` · ZIP ${bytes(report.zipBytes)}`:''}`
  const note=document.createElement('p');note.className='hint'
  note.textContent=`${Number.isFinite(report.atlasFileReductionFraction)?`图集文件${report.atlasFileReductionFraction>=0?'减少':'增加'} ${Math.abs(report.atlasFileReductionFraction*100).toFixed(1)}%。` : ''}解码估算按纹理尺寸 × 4；运行时实际占用由引擎决定。${report.optionalFrameBytes?`独立 PNG 另占 ${bytes(report.optionalFrameBytes)}。`:''}`
  $('export-metrics').replaceChildren(table,summary)
  for(const [id,key] of [['export-download','downloadUrl'],['export-preview','previewUrl'],['export-manifest','manifestUrl']]) {
    $(id).hidden = !data[key]
    if (data[key]) $(id).href=new URL(data[key],document.baseURI).href
  }
  $('export-download').download=''
  $('export-location').textContent=`导出目录：${data.outputDirectory}`
  $('export-report').textContent=JSON.stringify(report,null,2)
  $('export-result').hidden=false;$('export-result').dataset.stale=String(stale)
  $('export-status').textContent=stale?'生成期间调整已变化。此包对应导出时的草稿；重新导出后可在本页对比。':'图包已生成。可在画面上方切换原图与压缩效果。'
  updateComparison()
  if(wasExport)loadShownImages()
}
function setExportBusy(value) {
  $('export-package').disabled=value
  $('export-package').textContent=value?'正在导出…':'生成图包'
  $('export-progress').hidden=!value
  $('cancel-export').hidden=!value
  $('export-section').setAttribute('aria-busy',String(value))
}
$('export-package').onclick=async()=>{
  if(project.exportReadOnly || !clip)return
  const ticket=++exportTicket, controller=new AbortController(), started=performance.now()
  exportAbort?.abort();exportAbort=controller;setExportBusy(true)
  $('export-status').dataset.error='false'
  $('export-status').textContent='准备导出…'
  try {
    if(pendingForm())applyCurrent()
    const options=exportOptions(),snapshot=draft(),signature=JSON.stringify(snapshot)
    const response=await studioFetch('/api/exports',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({draft:snapshot,options}),signal:controller.signal})
    let job
    try {job=await response.json()} catch {throw Error(`导出服务不可用（HTTP ${response.status}）`)}
    if(!response.ok)throw Error(job.error || `导出失败（HTTP ${response.status}）`)
    while(['queued','running'].includes(job.status)) {
      if(disposed || ticket!==exportTicket)return
      $('export-status').textContent=`${job.phase} · ${Math.floor((performance.now()-started)/1000)} 秒`
      await new Promise((resolve,reject)=>{
        const abort=()=>{clearTimeout(timer);reject(new DOMException('Cancelled','AbortError'))}
        const timer=setTimeout(()=>{controller.signal.removeEventListener('abort',abort);resolve()},450)
        controller.signal.addEventListener('abort',abort,{once:true})
      })
      const poll=await studioFetch(`/api/exports/${encodeURIComponent(job.id)}`,{signal:controller.signal})
      job=await poll.json()
      if(!poll.ok)throw Error(job.error || '导出状态读取失败')
    }
    if(job.status==='failed')throw Error(job.error || '导出失败')
    if(job.status!=='complete'||!job.result)throw Error('导出服务返回了无效结果')
    if(disposed || ticket!==exportTicket)return
    showExportResult(job.result,options,JSON.stringify(draft())!==signature)
    $('export-status').textContent='导出完成'
    toast('图包已生成')
  } catch(error) {
    if(!disposed && ticket===exportTicket) {
      $('export-status').dataset.error='true'
      $('export-status').textContent=error.name==='AbortError'?'已停止等待':`导出失败：${error.message}`
      if(error.name!=='AbortError')toast(`导出失败：${error.message}`,true)
    }
  } finally {if(ticket===exportTicket){exportAbort=null;setExportBusy(false)}}
}
$('cancel-export').onclick=()=>{++exportTicket;exportAbort?.abort();exportAbort=null;setExportBusy(false);$('export-status').textContent='已取消本次等待。'}
for(const mode of ['original','export']) $(`view-${mode}`).onclick=guarded(async()=>{
  if(mode==='export' && (exported?.stale || !exported?.project.clips.some(c=>c.id===clip.id)))return
  playing=false;previous=null;previewMode=mode;updateComparison();await loadShownImages()
})
function setPreset(preset) {
  const values={lossless:[1,100],balanced:[.5,82],compact:[.25,65]}[preset]
  $('export-scale').value=values[0];$('export-quality').value=values[1];$('export-format').value='webp'
  updateQuality()
  refreshChoices()
}
function updateQuality() {
  const disabled=$('export-format').value==='png' || document.querySelector('[name="export-preset"]:checked').value==='lossless'
  $('export-quality').disabled=disabled
}
for(const input of document.querySelectorAll('[name="export-preset"]'))input.onchange=()=>setPreset(input.value)
$('export-format').onchange=updateQuality
setPreset('balanced')
$('play').onclick=()=>{if(finished){index=0;elapsed=0;finished=false;sync()}playing=!playing;previous=null;render()}
for(const id of ['duration','dx','dy']) $(id).onfocus=()=>{playing=false;previous=null;render()}
$('prev').onclick=()=>seek(index-1)
$('next').onclick=()=>seek((index+1)%clip.steps.length)
$('seek').oninput=()=>seek(Number($('seek').value))
$('clip').onchange=()=>selectClip($('clip').value)
for(const id of ['size','background','guides','markers','ghost']) $(id).onchange=render
$('loop').onchange=()=>{clip.loop=$('loop').checked;markEdited();render()}
function applyCurrent() {
  const ms=Number($('duration').value),offset=[Number($('dx').value),Number($('dy').value)]
  if(!Number.isInteger(ms)||ms<1||ms>60000||offset.some(v=>!Number.isFinite(v)||Math.abs(v)>Math.max(...clip.canvas)))throw Error('时长或偏移无效')
  playing=false;elapsed=0
  Object.assign(clip.steps[index],{durationMs:ms,offset});markEdited();sync()
}
$('apply').onclick=guarded(()=>{applyCurrent();notice('当前帧已更新')})
for(const [id,delta] of [['earlier',-1],['later',1]]) $(id).onclick=()=>{
  const next=index+delta
  if(!clip.canReorder||next<0||next>=clip.steps.length)return
  ;[clip.steps[index],clip.steps[next]]=[clip.steps[next],clip.steps[index]]
  markEdited()
  seek(next)
}
$('save').onclick=guarded(async()=>{if(pendingForm())applyCurrent();await saveLocalDraft();notice('调整已保存');toast('调整已保存')})
$('restore').onclick=guarded(restoreLocalDraft)
$('download').onclick=download
$('import').onchange=guarded(async()=>{const file=$('import').files[0];if(file){await acceptDraft(JSON.parse(await file.text()));notice('已导入匹配的调整。')}$('import').value=''})
$('reset').onclick=()=>{const id=clip.id;clips[clips.findIndex(c=>c.id===id)]=clone(project.clips.find(c=>c.id===id));markEdited();selectClip(id);notice('当前动作恢复到源数据。')}
window.addEventListener('resize',render)
document.addEventListener('visibilitychange',()=>{previous=null;if(document.hidden){playing=false;render()}})
window.addEventListener('pagehide',()=>{disposed=true;pageRequests.abort();loadId++;exportTicket++;exportAbort?.abort();exportAbort=null;setExportBusy(false);cancelAnimationFrame(raf);images.clear();for(const url of blobUrls)URL.revokeObjectURL(url);blobUrls.clear()})
window.addEventListener('pageshow',e=>{if(e.persisted){disposed=false;previous=null;selectClip($('clip').value);raf=requestAnimationFrame(tick)}})
try {
  if(window.__MOTION_STUDIO_PROJECT__)project=normalizeProject(window.__MOTION_STUDIO_PROJECT__)
  else {const response=await studioFetch('/api/project');if(!response.ok)throw Error(`HTTP ${response.status}`);project=normalizeProject(await response.json())}
  clips=clone(project.clips)
  $('export-package').disabled=!clips.length
  $('export-section').hidden=!!project.exportReadOnly
  $('title').textContent=project.name
  for(const link of project.links || []){const a=document.createElement('a');a.href=link.url;a.textContent=link.name;a.target='_blank';a.rel='noopener';$('links').append(a)}
  for(const c of clips){const option=document.createElement('option');option.value=c.id;option.textContent=c.name;$('clip').append(option)}
  const studioApi={project,current:()=>clip,select:selectClip,pause:()=>{playing=false;previous=null;render()},pending:catalogPending,save:saveLocalDraft,
    prepare:async()=>{if(pendingForm())applyCurrent();await saveLocalDraft()},
    reload:async wanted=>{
      const next=normalizeProject(await (await studioFetch('/api/project',{signal:pageRequests.signal})).json())
      const updates=Object.fromEntries(next.clips.map(c=>[c.id,editable(c)]))
      for(const c of clips) {
        const fresh=next.clips.find(item=>item.id===c.id),original=project.clips.find(item=>item.id===c.id)
        if(fresh&&JSON.stringify(fresh.frames)===JSON.stringify(original.frames)&&JSON.stringify(fresh.steps)===JSON.stringify(original.steps))updates[c.id]=editable(c)
      }
      const rebased={version:1,projectId:next.id,fingerprint:next.fingerprint,clips:updates}
      const response=await studioFetch('/api/files/draft',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(rebased),signal:pageRequests.signal})
      if(!response.ok)throw Error('调整保存失败，请下载调整后刷新')
      sessionStorage.setItem('studio-next-clip',wanted||'')
      location.reload()
    }}
  chrome=mountChrome(studioApi)
  if(clips.length){
    let wanted=null
    try { wanted=sessionStorage.getItem('studio-next-clip'); sessionStorage.removeItem('studio-next-clip') } catch { /* Portable file previews can have no storage. */ }
    const first=clips.find(c=>c.id===wanted)||clips[0]
    $('clip').value=first.id
    await selectClip(first.id)
    if(!project.exportReadOnly) {
      const response=await studioFetch('/api/files/draft',{signal:pageRequests.signal})
      const saved=response.ok?await response.json():null
      if(saved?.fingerprint===project.fingerprint)await restoreLocalDraft()
      else if(saved)toast('源资产已更新，旧调整未应用')
    }
    raf=requestAnimationFrame(tick);notice(`${clips.length} 个动作`)
  } else {
    for(const id of ['play','prev','next','apply','earlier','later','reset','restore'])$(id).disabled=true
    notice('工程已打开')
  }
  if(!project.exportReadOnly) {
    const {mountWorkspace}=await import('./workspace.mjs')
    await mountWorkspace(studioApi)
  }

} catch(error) {notice(`工作台载入失败：${error.message}`)}
