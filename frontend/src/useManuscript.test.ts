import {renderHook,act,waitFor} from '@testing-library/react';
import {it,expect,vi,afterEach} from 'vitest';
import {useManuscript} from './useManuscript';
afterEach(()=>vi.unstubAllGlobals());
it('refuses to flush dirty unbound text and preserves it while an initial GET is pending',async()=>{
 let finish!:(response:Response)=>void;
 const fetcher=vi.fn(()=>new Promise<Response>(resolve=>finish=resolve));vi.stubGlobal('fetch',fetcher);
 const {result}=renderHook(()=>useManuscript('secret',700));
 let loading!:Promise<boolean>;act(()=>{loading=result.current.open('a');});
 await waitFor(()=>expect(fetcher).toHaveBeenCalledTimes(1));
 act(()=>result.current.edit('不能丢的新修改'));
 await act(async()=>{finish(new Response(JSON.stringify({id:'a',revision:1,edited_text:'原稿',original_text:'原稿'})));await loading;});
 let success=true;await act(async()=>{success=await result.current.flush();});
 expect(success).toBe(false);expect(result.current.text).toBe('不能丢的新修改');expect(result.current.dirty.current).toBe(true);expect(fetcher).toHaveBeenCalledTimes(1);
});
it('rejects a stale same-record GET after a newer edit has finished saving',async()=>{
 const original={id:'a',revision:1,edited_text:'原稿',original_text:'原稿'};
 let finish!:(response:Response)=>void;let reads=0;
 vi.stubGlobal('fetch',vi.fn((_input:string,init?:RequestInit)=>{
  if(init?.method==='PATCH')return Promise.resolve(new Response(JSON.stringify({...original,revision:2,edited_text:JSON.parse(init.body as string).edited_text})));
  if(++reads===1)return Promise.resolve(new Response(JSON.stringify(original)));
  return new Promise<Response>(resolve=>finish=resolve);
 }));
 const {result}=renderHook(()=>useManuscript('secret',700));
 await act(async()=>{await result.current.open('a');});
 let loading!:Promise<boolean>;act(()=>{loading=result.current.open('a');});await waitFor(()=>expect(reads).toBe(2));
 act(()=>result.current.edit('新版本已保存'));await act(async()=>{await result.current.flush();});
 expect(result.current.record?.revision).toBe(2);
 await act(async()=>{finish(new Response(JSON.stringify(original)));await loading;});
 expect(result.current.text).toBe('新版本已保存');expect(result.current.record?.revision).toBe(2);expect(result.current.saveState).toBe('saved');
});
it('saves only changed segments with sequential revisions and keeps full edited text',async()=>{
 const segments=[{id:0,start:0,end:1,original_text:'甲',text:'甲'},{id:1,start:1,end:2,original_text:'乙',text:'乙'}];
 let record={id:'a',revision:1,edited_text:'甲\n乙',original_text:'甲\n乙',segments};
 const calls:string[]=[];
 vi.stubGlobal('fetch',vi.fn((path:string,init?:RequestInit)=>{
  if(init?.method==='PATCH'){calls.push(path);const body=JSON.parse(init.body as string);expect(body.revision).toBe(record.revision);const id=Number(path.split('/').pop());record={...record,revision:record.revision+1,segments:record.segments.map(s=>s.id===id?{...s,text:body.text}:s)};record.edited_text=record.segments.map(s=>s.text).join('\n');}
  return Promise.resolve(new Response(JSON.stringify(record)));
 }));
 const {result}=renderHook(()=>useManuscript('secret',700));await act(async()=>{await result.current.open('a');});
 act(()=>result.current.editSegment(0,'修改'));await act(async()=>{await result.current.flush();});
 expect(calls).toEqual(['/api/history/a/segments/0']);expect(result.current.text).toBe('修改\n乙');expect(result.current.record?.segments?.[1].text).toBe('乙');
});
