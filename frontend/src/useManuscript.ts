import { useEffect, useRef, useState } from 'react';
import { ApiError, request, type HistoryRecord } from './api';

export function useManuscript(token: string | undefined, delay: number) {
  const [text, renderText] = useState('');
  const [record, renderRecord] = useState<HistoryRecord | null>(null);
  const [saveState, setSaveState] = useState<'saved' | 'saving' | 'error' | 'conflict'>('saved');
  const [saveError, setSaveError] = useState('');
  const [loading, setLoading] = useState(false);
  const current = useRef<HistoryRecord | null>(null);
  const latest = useRef('');
  const dirty = useRef(false);
  const queue = useRef<Promise<boolean> | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const generation = useRef(0);
  const conflict = useRef(false);
  const failedOpen = useRef<string | null>(null);
  const tokenRef = useRef(token); tokenRef.current = token;
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; clearTimeout(timer.current); ++generation.current; }; }, []);

  function setText(value: string) { latest.current = value; renderText(value); }
  function reset() { setLoading(false); failedOpen.current = null; ++generation.current; clearTimeout(timer.current); current.current = null; renderRecord(null); dirty.current = false; conflict.current = false; setSaveState('saved'); }
  async function flush(): Promise<boolean> {
    clearTimeout(timer.current);
    if (queue.current) return queue.current;
    if (conflict.current) return false;
    if (!dirty.current) return true;
    if (!current.current) { setSaveState('error'); setSaveError('当前编辑稿尚未关联已保存文稿，请先导出保留文字。'); return false; }
    if (!tokenRef.current) return false;
    setSaveState('saving');
    const work = (async () => {
      try {
        while (current.current && dirty.current) {
          const base = current.current;
          const value = latest.current;
          const next = await request<HistoryRecord>(`/history/${encodeURIComponent(base.id)}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revision: base.revision, edited_text: value }) }, tokenRef.current);
          if (!mounted.current || current.current?.id !== base.id) return false;
          current.current = next; renderRecord(next);
          dirty.current = latest.current !== next.edited_text;
        }
        setSaveState('saved'); setSaveError(''); return true;
      } catch (error) {
        if (mounted.current) {
          conflict.current = error instanceof ApiError && error.status === 409;
          setSaveState(conflict.current ? 'conflict' : 'error');
          setSaveError(error instanceof Error ? error.message : '请重试');
        }
        return false;
      }
    })();
    queue.current = work;
    try { return await work; } finally { if (queue.current === work) queue.current = null; }
  }
  function edit(value: string) {
    ++generation.current; setLoading(false);
    setText(value); dirty.current = true;
    if (!current.current) return;
    clearTimeout(timer.current);
    if (!conflict.current) { setSaveState('saving'); timer.current = setTimeout(() => void flush(), delay); }
  }
  async function open(id: string, discard = false): Promise<boolean> {
    const epoch = ++generation.current;
    setLoading(true);
    try {
      if (!discard && !(await flush())) return false;
      if (!mounted.current || epoch !== generation.current) return false;
      const next = await request<HistoryRecord>(`/history/${encodeURIComponent(id)}`);
      if (!mounted.current || epoch !== generation.current) return false;
      if (!discard && dirty.current) return false;
      if (current.current?.id === next.id && next.revision < current.current.revision) return false;
      failedOpen.current = null; current.current = next; renderRecord(next); setText(next.edited_text); dirty.current = false; conflict.current = false; setSaveState('saved'); setSaveError('');
      return true;
    } catch (error) { if (mounted.current && epoch === generation.current) { failedOpen.current = id; setSaveState('error'); setSaveError(error instanceof Error ? error.message : '无法打开文稿'); } return false; }
    finally { if (mounted.current && epoch === generation.current) setLoading(false); }
  }

  async function rename(title: string) {
    if (!(await flush()) || !current.current || !tokenRef.current) return false;
    ++generation.current; setLoading(false);
    const base = current.current;
    setSaveState('saving');
    const work = (async () => {
      try {
        const next = await request<HistoryRecord>(`/history/${encodeURIComponent(base.id)}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revision: base.revision, title }) }, tokenRef.current);
        current.current = next; renderRecord(next); setSaveState(dirty.current ? 'saving' : 'saved'); return true;
      } catch (error) { conflict.current = error instanceof ApiError && error.status === 409; setSaveState(conflict.current ? 'conflict' : 'error'); setSaveError(error instanceof Error ? error.message : '重命名失败'); return false; }
    })();
    queue.current = work;
    const result = await work; queue.current = null;
    if (result && dirty.current) return flush();
    return result;
  }
  useEffect(() => {
    const guard = (event: BeforeUnloadEvent) => { if (dirty.current || queue.current) { event.preventDefault(); event.returnValue = ''; } };
    window.addEventListener('beforeunload', guard);
    return () => window.removeEventListener('beforeunload', guard);
  }, []);
  return { text, setText, record, loading, dirty, edit, flush, retry: () => failedOpen.current ? open(failedOpen.current) : flush(), open, reset, rename, saveState, saveError };
}
