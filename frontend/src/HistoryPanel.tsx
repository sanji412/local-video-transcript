import { useEffect, useState } from 'react';
import { request, type HistorySummary } from './api';
export default function HistoryPanel({ selected, refresh, disabled, onSelect }: { selected?: string; refresh: number; disabled: boolean; onSelect: (id: string) => void }) {
  const [query, setQuery] = useState('');
  const [items, setItems] = useState<HistorySummary[]>([]);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(() => {
      request<{items: HistorySummary[]}>(`/history?q=${encodeURIComponent(query)}`, {signal: controller.signal}).then(result => { if (!controller.signal.aborted) { setItems(result.items || []); setError(''); } }).catch(error => { if (!controller.signal.aborted) setError(error.message); });
    }, query ? 250 : 0);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [query, refresh, attempt]);
  return <details className="history-panel" open><summary>本地文稿库 <span>只保存在这台 Mac</span></summary><div className="history-body"><input aria-label="搜索历史文稿" type="search" placeholder="搜索标题或文稿内容" value={query} onChange={e => setQuery(e.target.value)} />{error && <p role="alert">{error}<button className="text-button" onClick={() => setAttempt(n => n+1)}>重试加载历史</button></p>}<div className="history-list">{items.map(item => <button key={item.id} className={selected === item.id ? 'selected' : ''} aria-pressed={selected === item.id} disabled={disabled} onClick={() => onSelect(item.id)}><strong>{item.title}</strong><time>{new Date(item.updated_at).toLocaleString('zh-CN', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'})}</time><span>{item.preview}</span></button>)}</div>{!items.length && !error && <p className="field-hint">{query ? '没有找到文稿，试试其他关键词。' : '完成提取后，文稿会自动保存在这里。'}</p>}</div></details>;
}
