import { useEffect, useRef, useState } from 'react';
import { request, type ModelStatus, type Session, type Task } from './api';
import './styles.css';

const stages: Record<string, string> = { preparing: '准备开始', checking: '检查素材', subtitles: '提取字幕', audio: '处理音频', converting: '处理音频', downloading: '获取视频', validating: '检查素材', extracting: '提取字幕', transcoding: '处理音频', transcribing: '转写全文', completed: '提取完成', failed: '提取失败', cancelled: '任务已取消', queued: '准备开始' };
const statusLabels = { running: '正在处理', completed: '已完成', failed: '未完成', cancelled: '已取消' };
const errorMessage = (error: unknown) => error instanceof Error ? error.message : '操作未完成，请重试。';

export default function App({ pollInterval = 1200 }: { pollInterval?: number }) {
  const [session, setSession] = useState<Session | null>(null);
  const [model, setModel] = useState<ModelStatus | null>(null);
  const [task, setTask] = useState<Task | null>(null);
  const [mode, setMode] = useState<'link' | 'file'>('link');
  const [link, setLink] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [modelBusy, setModelBusy] = useState(false);
  const [error, setError] = useState('');
  const [pollError, setPollError] = useState('');
  const [notice, setNotice] = useState('');
  const [connectionAttempt, setConnectionAttempt] = useState(0);
  const [connecting, setConnecting] = useState(true);
  const [savedDraft, setSavedDraft] = useState('');
  const dirty = useRef(false);
  const version = useRef(0);
  const gutter = useRef<HTMLDivElement>(null);
  const linkTab = useRef<HTMLButtonElement>(null);
  const fileTab = useRef<HTMLButtonElement>(null);
  const token = session?.token;
  const active = task?.status === 'running';

  function receiveTask(next: Task) {
    setTask(next);
    if (!dirty.current) setText(next.text);
  }

  useEffect(() => {
    const controller = new AbortController();
    const currentVersion = ++version.current;
    setConnecting(true);
    setError('');
    request<Session>('/session', { signal: controller.signal }).then(next => {
      if (controller.signal.aborted || currentVersion !== version.current) return;
      setSession(next); setModel(next.model);
      if (next.active_task) {
        if (next.active_task.id !== task?.id) {
          if (dirty.current && text) setSavedDraft(text);
          dirty.current = false;
        }
        receiveTask(next.active_task);
      } else {
        setTask(null);
        if (task?.status === 'running' && !dirty.current) setText('');
        else if (text) setNotice('已保留文稿，可继续编辑或导出');
      }
      setPollError('');
    }).catch(e => {
      if (!controller.signal.aborted && currentVersion === version.current) {
        setSession(null); setModel(null); setError(errorMessage(e));
      }
    }).finally(() => {
      if (!controller.signal.aborted && currentVersion === version.current) setConnecting(false);
    });
    return () => controller.abort();
  }, [connectionAttempt]);

  function reconnect() {
    if (busy || modelBusy || connecting) return;
    ++version.current;
    setConnecting(true); setError(''); setPollError('');
    setConnectionAttempt(n => n + 1);
  }

  useEffect(() => {
    if (!token || connecting || model?.status !== 'downloading') return;
    const controller = new AbortController();
    const currentVersion = version.current;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const next = await request<ModelStatus>('/model', { signal: controller.signal });
        if (controller.signal.aborted || currentVersion !== version.current) return;
        setModel(next); setPollError('');
        if (next.status === 'downloading') timer = setTimeout(poll, pollInterval);
      } catch (e) { if (!controller.signal.aborted && currentVersion === version.current) setPollError(errorMessage(e)); }
    }
    timer = setTimeout(poll, pollInterval);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [token, connecting, model?.status, pollInterval]);

  useEffect(() => {
    if (!task || !active || busy || connecting) return;
    const id = task.id;
    const currentVersion = version.current;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const next = await request<Task>(`/tasks/${encodeURIComponent(id)}`, { signal: controller.signal });
        if (controller.signal.aborted || currentVersion !== version.current) return;
        receiveTask(next); setPollError('');
        if (next.status === 'running') timer = setTimeout(poll, pollInterval);
      } catch (e) { if (!controller.signal.aborted && currentVersion === version.current) setPollError(errorMessage(e)); }
    }
    timer = setTimeout(poll, pollInterval);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [task?.id, active, busy, connecting, token, pollInterval]);

  const toolsReady = session?.dependencies.ffmpeg && session.dependencies.ffprobe;
  const inputReady = mode === 'link' ? Boolean(link.trim()) : Boolean(file);
  const canStart = Boolean(token && toolsReady && model?.status === 'ready' && inputReady && !active && !busy && !connecting);

  async function submit() {
    if (!canStart) return;
    const currentVersion = ++version.current;
    setBusy(true); setError(''); setPollError(''); setNotice('');
    try {
      let next: Task;
      if (mode === 'file') {
        const body = new FormData(); body.append('file', file!);
        next = await request<Task>('/tasks/file', { method: 'POST', body }, token);
      } else {
        next = await request<Task>('/tasks/link', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ share_text: link.trim() }) }, token);
      }
      if (currentVersion !== version.current) return;
      dirty.current = false; setText(''); receiveTask(next);
    } catch (e) { if (currentVersion === version.current) setError(errorMessage(e)); }
    finally { if (currentVersion === version.current) setBusy(false); }
  }

  async function cancel() {
    if (!task || !token || busy || connecting) return;
    const currentVersion = ++version.current;
    setBusy(true); setError(''); setPollError('');
    try {
      const next = await request<Task>(`/tasks/${encodeURIComponent(task.id)}/cancel`, { method: 'POST' }, token);
      if (currentVersion === version.current) receiveTask(next);
    } catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }

  async function prepareModel() {
    if (!token || modelBusy || active || connecting) return;
    setModelBusy(true); setError(''); setPollError('');
    try { setModel(await request<ModelStatus>('/model/download', { method: 'POST' }, token)); }
    catch (e) { setError(errorMessage(e)); }
    finally { setModelBusy(false); }
  }

  async function copy() {
    try { await navigator.clipboard.writeText(text); setNotice('全文已复制'); }
    catch { setNotice('复制未完成，请选中文稿后使用 ⌘C 复制。'); }
  }

  function download(value = text, filename = '视频文稿.txt') {
    const url = URL.createObjectURL(new Blob([value], { type: 'text/plain;charset=utf-8' }));
    const anchor = document.createElement('a');
    anchor.href = url; anchor.download = filename; anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    setNotice('TXT 已导出');
  }

  const visibleError = error || task?.error || '';
  const lines = Math.max(14, text.split('\n').length);

  return <div className="app-shell">
    <header className="masthead">
      <a className="brand" href="/" aria-label="视频文案提取器首页"><span className="brand-mark" aria-hidden="true"><i /><i /><i /></span><span>视频文案提取器<small>本地工作台</small></span></a>
      <span className="local-badge"><span /> 在这台 Mac 上处理</span>
    </header>
    <main>
      <section className="intro" aria-labelledby="page-title">
        <div><p className="eyebrow">从声音，到文字</p><h1 id="page-title">把视频，变成可用的文字。</h1><p className="intro-description">导入一段视频，提取完整讲述。留给你校对、编辑和使用。</p></div>
        <p className="scope-note">普通话口播 / 30 分钟以内<br /><span>本地转写 · 文稿不上传</span></p>
      </section>
      <div className="workspace">
        <section className="source-panel panel" aria-labelledby="source-title">
          <div className="section-heading"><span className="section-index">01</span><div><h2 id="source-title">导入素材</h2><p>从链接或本地视频开始</p></div></div>
          <div className="input-tabs" role="tablist" aria-label="素材来源" onKeyDown={event => {
            if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
              event.preventDefault(); const next = mode === 'link' ? 'file' : 'link'; setMode(next); (next === 'link' ? linkTab : fileTab).current?.focus();
            }
          }}>
            <button ref={linkTab} role="tab" aria-selected={mode === 'link'} aria-controls="source-input" id="link-tab" tabIndex={mode === 'link' ? 0 : -1} onClick={() => setMode('link')}>抖音链接</button>
            <button ref={fileTab} role="tab" aria-selected={mode === 'file'} aria-controls="source-input" id="file-tab" tabIndex={mode === 'file' ? 0 : -1} onClick={() => setMode('file')}>本地 MP4</button>
          </div>
          <div id="source-input" role="tabpanel" aria-labelledby={`${mode}-tab`} className="input-area">
            {mode === 'link' ? <><label className="field-label" htmlFor="share-link">抖音分享链接</label><textarea id="share-link" value={link} onChange={e => setLink(e.target.value)} placeholder="粘贴单条视频链接，或整段分享内容…" disabled={active || busy || connecting} /><p className="field-hint">支持分享短链接与视频页面链接，一次提取一条。</p></> : <><label className={`file-picker ${file ? 'has-file' : ''}`}><span className="file-symbol" aria-hidden="true">↑</span><strong>{file ? file.name : '选择一个 MP4 视频'}</strong><span>{file ? `${(file.size / 1024 / 1024).toFixed(1)} MB · 点击重新选择` : '点击选择本地文件'}</span><input type="file" accept=".mp4,video/mp4" aria-label="选择 MP4 文件" disabled={active || busy || connecting} onChange={e => setFile(e.target.files?.[0] ?? null)} /></label><p className="field-hint">视频仅在本机处理，不会修改你的原文件。</p></>}
          </div>
          <div className={`model-card ${model?.status === 'ready' ? 'is-ready' : ''}`}>
            <div className="model-title"><span className={`status-dot ${model?.status === 'downloading' ? 'is-working' : ''}`} /><strong>本地识别模型</strong><span className="model-state">{!model ? (error ? '无法连接' : '检查中') : { ready: '已就绪', missing: '待准备', downloading: '准备中', error: '需重试' }[model.status]}</span></div>
            <p>{model?.detail || (error ? '连接本地服务后，即可检查模型。' : '正在检查本机环境…')}</p>
            {model?.status === 'downloading' && <progress aria-label="模型下载进度" aria-valuetext={model.progress === null ? undefined : `${Math.round(model.progress * 100)}%`} max={1} value={model.progress ?? undefined} />}
            {model && (model.status === 'missing' || model.status === 'error') && <><p className="model-explanation">首次使用需要联网下载模型，准备好后可离线转写本地视频。</p><button className="text-button" disabled={modelBusy || active || connecting} onClick={prepareModel}>{modelBusy ? '开始下载…' : model.status === 'error' ? '重新下载模型' : '下载模型'}<span aria-hidden="true"> ↗</span></button></>}
          </div>
          {session && !toolsReady && <p className="tool-warning" role="alert">缺少 FFmpeg 或 ffprobe。请先按启动说明安装本地音视频工具，再重新打开工作台。</p>}
          <button className="primary-button" disabled={!canStart} onClick={submit}>{busy && !active ? '正在提交…' : '开始提取'}<span aria-hidden="true">→</span></button>
          {model && model.status !== 'ready' && <p className="submit-hint">模型就绪后，即可开始提取。</p>}
          <div className="privacy-note"><span aria-hidden="true">⌁</span><p>内容留在本机<span>链接获取与模型下载需要联网。</span></p></div>
        </section>
        <section className="manuscript-panel panel" aria-labelledby="manuscript-title">
          <div className="manuscript-toolbar"><div className="section-heading"><span className="section-index">02</span><div><h2 id="manuscript-title">文稿</h2><p>{task?.source ? (task.source === 'subtitle' ? '字幕提取' : '语音转写') : '提取后，可直接在此校对'}</p></div></div><div className="export-actions"><button disabled={!text.trim()} onClick={copy}>复制全文</button><button disabled={!text.trim()} onClick={() => download()}>导出 TXT<span aria-hidden="true"> ↗</span></button></div></div>
          {task && <div className={`task-status task-${task.status}`} aria-live="polite"><div><span className={`status-dot ${active ? 'is-working' : ''}`} /><strong>{statusLabels[task.status]}</strong><span className="stage-label">{stages[task.stage] || '处理中'}</span></div><p>{task.detail}</p>{active && <button className="text-button" onClick={cancel} disabled={busy || connecting}>{busy ? '正在取消…' : '取消任务'}</button>}{(task.status === 'failed' || task.status === 'cancelled') && <button className="text-button" onClick={submit} disabled={!canStart}>重试提取</button>}</div>}
          {visibleError && <div className="error-banner" role="alert"><strong>暂时无法完成</strong><p>{visibleError}</p>{session && !active && task?.status !== 'failed' && task?.status !== 'cancelled' && <button className="text-button" onClick={submit} disabled={!canStart}>重试提取</button>}<button className="text-button" onClick={reconnect} disabled={connecting || busy || modelBusy}>重新连接本地服务</button></div>}
          {pollError && <div className="error-banner" role="alert"><p>状态更新中断：{pollError}</p><button className="text-button" onClick={reconnect} disabled={connecting || busy || modelBusy}>重新连接</button></div>}
          {savedDraft && <div className="draft-banner" role="status"><span>上一份编辑文稿已保留。</span><button className="text-button" onClick={() => download(savedDraft, '上一份编辑文稿.txt')}>导出上一份文稿</button></div>}
          <div className={`editor-surface ${!text ? 'is-empty' : ''}`}>
            <div className="line-gutter" ref={gutter} aria-hidden="true">{Array.from({ length: lines }, (_, i) => <span key={i}>{String(i + 1).padStart(2, '0')}</span>)}</div>
            <textarea aria-label="文稿全文" spellCheck={false} readOnly={!session || active || busy || connecting} value={text} onChange={event => { dirty.current = true; setText(event.target.value); setNotice(''); }} onScroll={event => { if (gutter.current) gutter.current.scrollTop = event.currentTarget.scrollTop; }} />
            {!text && <div className="empty-manuscript"><span className="paper-icon" aria-hidden="true"><i /><i /><i /></span><h3>{active ? '正在听，正在记录' : '文稿将在这里展开'}</h3><p>{active ? '处理阶段会实时显示，请稍候。' : '在左侧导入视频，开始你的第一份文稿。'}</p></div>}
          </div>
          <div className="editor-footer"><span>{text ? `${text.length.toLocaleString('zh-CN')} 字符` : '等待素材'}{dirty.current && text ? ' · 已编辑' : ''}</span><span aria-live="polite">{notice || (active || busy ? '提取完成后，可校对和编辑全文' : '可编辑全文 · 复制与导出保留你的修改')}</span></div>
        </section>
      </div>
      <footer className="page-footer"><span>让表达，留下文字。</span><span>本地处理 / 无跨会话历史</span></footer>
    </main>
  </div>;
}
