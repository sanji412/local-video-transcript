import { useEffect, useRef, useState } from 'react';
import { request, type Batch, type BatchPreview, } from './api';
import Efficiency from './Efficiency';
export const batchBusy = (batch: Batch | null) => Boolean(batch && (batch.status === 'running' || batch.items.some(item => item.status === 'running')));
const labels = { pending: '等待中', running: '处理中', completed: '已完成', failed: '失败', skipped: '重复跳过', cancelled: '已取消' };
export function useBatch(token: string | undefined, initial: Batch | null | undefined, pollInterval: number, connecting: boolean) {
  const [batch, setBatch] = useState<Batch | null>(null);
  const [error, setError] = useState('');
  const [mutating, setMutating] = useState(false);
  const generation = useRef(0);
  useEffect(() => { if (!connecting) setBatch(initial ?? null); }, [initial, connecting]);
  useEffect(() => {
    if (!token || connecting) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      const version = generation.current;
      try {
        const result = await request<{ batch: Batch | null }>('/batches/current', {signal:controller.signal});
        if (!controller.signal.aborted && version === generation.current) { setBatch(result.batch ?? null); setError(''); }
      } catch (e) { if (!controller.signal.aborted && version === generation.current) setError(e instanceof Error ? e.message : '队列状态暂时无法更新'); }
      if (!controller.signal.aborted) timer = setTimeout(poll, pollInterval);
    }
    timer = setTimeout(poll, pollInterval);
    return () => { controller.abort(); clearTimeout(timer); };
  }, [token, connecting, pollInterval]);
  async function mutate(path: string, body: unknown) {
    if (!token || mutating) return;
    ++generation.current; setMutating(true); setError('');
    try { setBatch(await request<Batch>(path, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)},token)); }
    catch (e) { setError(e instanceof Error ? e.message : '队列操作未完成'); }
    finally { ++generation.current; setMutating(false); }
  }
  return {batch,error,mutating,mutate};
}
export function BatchInput({ disabled, canStart, start, previewToken }: {disabled:boolean;canStart:boolean;start:(items:{share_text:string;force:boolean}[])=>Promise<void>;previewToken?:string}) {
  const [text,setText]=useState('');const [items,setItems]=useState<BatchPreview[]>([]);const [forces,setForces]=useState<Record<number,boolean>>({});const [error,setError]=useState('');const [busy,setBusy]=useState(false);
  async function preview(){if(disabled||!previewToken)return;setBusy(true);setError('');try{const next=await request<{items:BatchPreview[]}>('/batches/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({share_text:text})},previewToken);setItems(next.items);setForces({});}catch(e){setError(e instanceof Error?e.message:'预览未完成');}finally{setBusy(false);}}
  const valid=items.filter(item=>item.status!=='invalid');
  return <div className="batch-input"><label className="field-label" htmlFor="batch-text">批量分享内容</label><textarea id="batch-text" maxLength={200000} value={text} disabled={disabled||busy} placeholder="粘贴多条分享内容或链接，每批最多 20 条" onChange={e=>{setText(e.target.value);setItems([]);setForces({});}}/><button className="text-button" disabled={disabled||busy||!text.trim()||!previewToken} onClick={()=>void preview()}>{busy?'正在预览…':'预览链接'}</button>{items.length>0&&<><ol className="batch-preview">{items.map(item=><li key={item.index}><strong>{item.title||item.url||`第 ${item.index+1} 条`}</strong><span>{item.status==='ready'?'可提取':item.status==='duplicate'?'重复，默认跳过':'无效链接'}</span>{item.error&&<p>{item.error}</p>}{item.status==='duplicate'&&<label><input type="checkbox" aria-label={`仍要重新提取：${item.title}`} checked={Boolean(forces[item.index])} disabled={disabled} onChange={e=>setForces({...forces,[item.index]:e.target.checked})}/>仍要重新提取（另存一份）</label>}</li>)}</ol><button className="primary-button" disabled={!canStart||disabled||busy||!valid.length||items.length>20} onClick={()=>void start(valid.map(item=>({share_text:item.share_text,force:Boolean(forces[item.index])})))}>开始批量提取</button>{items.length>20&&<p role="alert">每批最多 20 条，请减少链接后重新预览。</p>}</>}{error&&<p role="alert">{error}</p>}<p className="field-hint">无效链接不会提交；重复项默认跳过。临时回听音频保留到下一次提取或服务重启。</p></div>;
}
export function QueuePanel({queue,disabled,onOpen,reconnect}:{reconnect:()=>void;queue:ReturnType<typeof useBatch>;disabled:boolean;onOpen:(id:string)=>void}) {
 const {batch,error,mutating,mutate}=queue;if(!batch&&!error)return null;
 const working=batchBusy(batch);const blocked=disabled||mutating;
 const action=(action:string)=>{if(batch)void mutate(`/batches/${batch.id}/actions`,{action});};
 return <section className="queue-panel panel" aria-label="批量队列"><h2>批量队列</h2>{error&&<p role="alert">队列状态更新中断：{error}。正在自动重试。<button className="text-button" disabled={disabled||mutating} onClick={reconnect}>重新连接队列</button></p>}{batch&&<><p aria-live="polite">{{running:'正在逐条处理',paused:'已暂停，当前条完成后停止',completed:'本批已结束',cancelled:'本批已停止'}[batch.status]}</p><div className="queue-actions">{batch.status==='running'&&<button disabled={blocked} onClick={()=>action('pause')}>暂停队列</button>}{batch.status==='paused'&&<button disabled={blocked||working} onClick={()=>action('resume')}>继续队列</button>}{(batch.status==='running'||batch.status==='paused')&&<button disabled={blocked} onClick={()=>action('cancel')}>停止全部</button>}{!working&&batch.items.some(i=>i.status==='failed')&&<button disabled={blocked} onClick={()=>action('retry_failed')}>重试失败项</button>}</div><ol>{batch.items.map(item=><li key={item.id}><strong>{item.title||item.url}</strong><span className="queue-status">{labels[item.status]}</span>{item.detail&&<p>{item.detail}</p>}{item.error&&<p>{item.error}</p>}{(item.status==='completed'||item.status==='running')&&<Efficiency value={item} source={item.source} running={item.status==='running'}/>}{item.history_id&&<button disabled={blocked||working} aria-label={`打开文稿：${item.title}`} onClick={()=>onOpen(item.history_id!)}>打开文稿</button>}{item.status==='pending'&&<button disabled={blocked} aria-label={`取消等待：${item.title}`} onClick={()=>void mutate(`/batches/${batch.id}/items/${item.id}/actions`,{action:'cancel'})}>取消等待</button>}{!working&&(item.status==='failed'||item.status==='cancelled'||item.status==='skipped')&&<button disabled={blocked} aria-label={`${item.status==='skipped'?'仍要重新提取':'重试'}：${item.title}`} onClick={()=>void mutate(`/batches/${batch.id}/items/${item.id}/actions`,{action:'retry',force:item.status==='skipped'})}>{item.status==='skipped'?'仍要重新提取':'重试'}</button>}</li>)}</ol></>}</section>;
}
