import {render,screen,waitFor,fireEvent,act} from '@testing-library/react';
import {beforeEach,afterEach,it,expect,vi} from 'vitest';
import App from './App';
const ready={status:'ready',detail:'就绪',progress:1};
const make=(id='a')=>({id,title:id==='a'?'第一份文稿':'第二份文稿',source_type:'file',source_ref:'演讲.mp4',created_at:'2026-10-31T01:00:00Z',updated_at:'2026-10-31T01:00:00Z',source:'transcription',mode:'accurate',revision:1,preview:'预览',original_text:'不可变原稿',edited_text:id==='a'?'已保存的校对稿':'第二稿'});
let records:ReturnType<typeof make>[], patches:any[],fail:number,hold:boolean,pending:()=>void,starts:number;
beforeEach(()=>{records=[make(),make('b')];patches=[];fail=0;hold=false;starts=0;vi.stubGlobal('fetch',vi.fn(async(input:string,init?:RequestInit)=>{const url=String(input),method=init?.method||'GET';let result:any;
if(url==='/api/session')result={token:'secret',model:ready,models:{fast:ready,accurate:ready},dependencies:{ffmpeg:true,ffprobe:true},active_task:null};
else if(url.startsWith('/api/history?')){const q=new URL('http://local'+url).searchParams.get('q')||'';result={items:records.filter(r=>r.title.includes(q))};}
else if(url.startsWith('/api/history/')){const id=url.split('/').at(-1),r=records.find(r=>r.id===id)!;if(method==='PATCH'){const body=JSON.parse(init!.body as string);patches.push(body);if(fail)return new Response(JSON.stringify({detail:fail===409?'修订冲突':'保存暂时失败'}),{status:fail});const saved={...r,...body,revision:r.revision+1};records=records.map(v=>v.id===id?saved:v);if(hold)return new Promise<Response>(resolve=>pending=()=>resolve(new Response(JSON.stringify(saved))));result=saved;}else if(method==='DELETE'){records=records.filter(v=>v.id!==id);return new Response(null,{status:204});}else result=r;}
else if(url==='/api/tasks/link'){starts++;result={id:'job',status:'running',stage:'checking',detail:'处理中',text:'',source:null,error:null,mode:'accurate'};}else result=ready;
return new Response(JSON.stringify(result));}));});
afterEach(()=>{vi.restoreAllMocks();vi.unstubAllGlobals();});
async function start(delay=15){render(<App autosaveDelay={delay} pollInterval={10000}/>);await waitFor(()=>expect(screen.getByLabelText('文稿全文')).toHaveValue('已保存的校对稿'));return screen.getByLabelText('文稿全文');}

it('keeps a recovered segment candidate available after visiting another manuscript',async()=>{
 records=records.map(r=>({...r,edited_text:r.id==='a'?'甲':'乙',segments:[{id:0,start:0,end:1,text:r.id==='a'?'甲':'乙',original_text:'原稿'}]}));
 const previous=fetch;
 vi.stubGlobal('fetch',vi.fn((input:string,init?:RequestInit)=>{
  if(String(input)==='/api/session')return Promise.resolve(new Response(JSON.stringify({token:'secret',model:ready,models:{fast:ready,accurate:ready},dependencies:{ffmpeg:true,ffprobe:true},active_task:{id:'retry',kind:'retry',status:'completed',stage:'completed',detail:'局部完成',history_id:'a',segment_id:0,text:'恢复的识别候选',source:'transcription',error:null}})));
  return previous(input,init);
 }));
 render(<App/>);
 await screen.findByText('恢复的识别候选');
 fireEvent.click(await screen.findByRole('button',{name:/第二份文稿/}));
 await waitFor(()=>expect(screen.getByLabelText('片段 1')).toHaveValue('乙'));
 expect(screen.queryByText('恢复的识别候选')).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:/第一份文稿/}));
 await screen.findByText('恢复的识别候选');
 expect(screen.getByLabelText('片段 1')).toHaveValue('甲');
 expect(screen.getByRole('button',{name:'采用识别结果'})).toBeEnabled();
});
it('restores edited text and immutable original',async()=>{const editor=await start();fireEvent.change(editor,{target:{value:'新校对稿'}});fireEvent.click(screen.getByRole('button',{name:'原始转写'}));expect(screen.getByLabelText('原始转写全文')).toHaveValue('不可变原稿');expect(screen.getByLabelText('原始转写全文')).toHaveAttribute('readonly');await waitFor(()=>expect(patches[0]).toMatchObject({revision:1,edited_text:'新校对稿'}));await screen.findByText('已保存');});
it('serializes latest edits made during a pending PATCH',async()=>{const editor=await start();hold=true;fireEvent.change(editor,{target:{value:'第一改'}});await waitFor(()=>expect(patches).toHaveLength(1));fireEvent.change(editor,{target:{value:'最新修改'}});expect(screen.queryByText('已保存')).not.toBeInTheDocument();hold=false;await act(async()=>pending());await waitFor(()=>expect(patches).toHaveLength(2));expect(patches[1]).toMatchObject({revision:2,edited_text:'最新修改'});await screen.findByText('已保存');});
it.each([503,409])('preserves draft on %s and blocks switching and new tasks',async(code)=>{const editor=await start(10000);fail=code;fireEvent.change(editor,{target:{value:'不能丢的稿'}});fireEvent.click(screen.getByRole('button',{name:/第二份文稿/}));await screen.findAllByText(/保存失败/);expect(editor).toHaveValue('不能丢的稿');fireEvent.change(screen.getByLabelText('抖音分享链接'),{target:{value:'https://v.douyin.com/a/'}});fireEvent.click(screen.getByRole('button',{name:'开始提取'}));await screen.findAllByText(/保存失败/);expect(starts).toBe(0);expect(editor).toHaveValue('不能丢的稿');fail=0;if(code===409)expect(screen.getByRole('button',{name:'重新载入最新稿'})).toBeVisible();else{fireEvent.click(screen.getByRole('button',{name:'重试保存'}));await screen.findByText('已保存');}});
it('flushes before switching records',async()=>{const editor=await start(10000);fireEvent.change(editor,{target:{value:'离开前保存'}});fireEvent.click(screen.getByRole('button',{name:/第二份文稿/}));await waitFor(()=>expect(editor).toHaveValue('第二稿'));expect(patches[0].edited_text).toBe('离开前保存');});
it('defaults new operations to accurate',async()=>{await start();expect(screen.getByLabelText('识别模式')).toHaveValue('accurate');fireEvent.change(screen.getByLabelText('抖音分享链接'),{target:{value:'https://v.douyin.com/a/'}});fireEvent.click(screen.getByRole('button',{name:'开始提取'}));await waitFor(()=>expect(starts).toBe(1));const call=vi.mocked(fetch).mock.calls.find(([url])=>url==='/api/tasks/link')!;expect(JSON.parse(call[1]!.body as string).mode).toBe('accurate');});
it('searches, renames versioned titles and confirms deletion with a 204 response',async()=>{await start();fireEvent.click(screen.getByRole('button',{name:'重命名'}));fireEvent.change(screen.getByLabelText('文稿标题'),{target:{value:'重命名文稿'}});fireEvent.click(screen.getByRole('button',{name:'保存标题'}));await waitFor(()=>expect(patches[0]).toEqual({revision:1,title:'重命名文稿'}));await waitFor(()=>expect(screen.queryByRole('button',{name:'保存标题'})).not.toBeInTheDocument());fireEvent.change(screen.getByLabelText('搜索历史文稿'),{target:{value:'重命名'}});await waitFor(()=>expect(screen.queryByRole('button',{name:/第二份文稿/})).not.toBeInTheDocument());fireEvent.click(screen.getByRole('button',{name:'删除文稿'}));expect(records).toHaveLength(2);fireEvent.click(screen.getByRole('button',{name:'确认删除'}));await waitFor(()=>expect(records).toHaveLength(1));expect(screen.getByLabelText('文稿全文')).toHaveValue('');expect(screen.queryByRole('alert')).not.toBeInTheDocument();});
it('flushes current edits before starting a new extraction and guards page exit',async()=>{const editor=await start(10000);fireEvent.change(editor,{target:{value:'先保存再开始'}});const event=new Event('beforeunload',{cancelable:true});window.dispatchEvent(event);expect(event.defaultPrevented).toBe(true);fireEvent.change(screen.getByLabelText('抖音分享链接'),{target:{value:'https://v.douyin.com/a/'}});fireEvent.click(screen.getByRole('button',{name:'开始提取'}));await waitFor(()=>expect(starts).toBe(1));expect(patches[0].edited_text).toBe('先保存再开始');});
it('blocks deleting a dirty draft when saving fails',async()=>{const editor=await start(10000);fail=503;fireEvent.change(editor,{target:{value:'仍需保留'}});fireEvent.click(screen.getByRole('button',{name:'删除文稿'}));fireEvent.click(screen.getByRole('button',{name:'确认删除'}));await screen.findAllByText(/保存失败/);expect(records).toHaveLength(2);expect(editor).toHaveValue('仍需保留');});
it('allows explicit reload after conflict and retains the immutable original',async()=>{const editor=await start(10000);fail=409;fireEvent.change(editor,{target:{value:'本地冲突稿'}});fireEvent.blur(editor);await screen.findAllByText(/保存失败/);records[0]={...records[0],revision:3,edited_text:'另一窗口的最新稿'};fail=0;fireEvent.click(screen.getByRole('button',{name:'重新载入最新稿'}));await waitFor(()=>expect(editor).toHaveValue('另一窗口的最新稿'));fireEvent.change(editor,{target:{value:'在最新稿上编辑'}});fireEvent.blur(editor);await waitFor(()=>expect(patches.at(-1)).toMatchObject({revision:3,edited_text:'在最新稿上编辑'}));expect(records[0].original_text).toBe('不可变原稿');});
it('keeps accurate missing while fast is ready, and sends explicit fast mode',async()=>{const previous=fetch;vi.stubGlobal('fetch',vi.fn(async(input:string,init?:RequestInit)=>{if(String(input)==='/api/session')return new Response(JSON.stringify({token:'secret',model:ready,models:{fast:ready,accurate:{status:'missing',detail:'精确模型待下载',progress:null}},dependencies:{ffmpeg:true,ffprobe:true},active_task:null}));return previous(input,init);}));await start();fireEvent.change(screen.getByLabelText('抖音分享链接'),{target:{value:'https://v.douyin.com/a/'}});expect(screen.getByRole('button',{name:'开始提取'})).toBeDisabled();expect(screen.getByText(/约 3.1 GB/)).toBeVisible();fireEvent.change(screen.getByLabelText('识别模式'),{target:{value:'fast'}});expect(screen.getByRole('button',{name:'开始提取'})).toBeEnabled();fireEvent.click(screen.getByRole('button',{name:'开始提取'}));await waitFor(()=>expect(starts).toBe(1));const call=vi.mocked(fetch).mock.calls.find(([url])=>url==='/api/tasks/link')!;expect(JSON.parse(call[1]!.body as string).mode).toBe('fast');});
it('blocks starting fast while accurate is downloading and polls the correct profile',async()=>{const previous=fetch;vi.stubGlobal('fetch',vi.fn(async(input:string,init?:RequestInit)=>{if(String(input)==='/api/session')return new Response(JSON.stringify({token:'secret',model:ready,models:{fast:ready,accurate:{status:'downloading',detail:'下载精确模型',progress:.2}},dependencies:{ffmpeg:true,ffprobe:true},active_task:null}));return previous(input,init);}));render(<App pollInterval={15}/>);await waitFor(()=>expect(screen.getByLabelText('文稿全文')).toHaveValue('已保存的校对稿'));fireEvent.change(screen.getByLabelText('识别模式'),{target:{value:'fast'}});fireEvent.change(screen.getByLabelText('抖音分享链接'),{target:{value:'https://v.douyin.com/a/'}});expect(screen.getByRole('button',{name:'开始提取'})).toBeDisabled();await waitFor(()=>expect(vi.mocked(fetch).mock.calls.some(([url])=>url==='/api/model?mode=accurate')).toBe(true));await waitFor(()=>expect(screen.getByRole('button',{name:'开始提取'})).toBeEnabled());});
it('does not start a new task after a rename revision conflict, even without text edits',async()=>{await start();fail=409;fireEvent.click(screen.getByRole('button',{name:'重命名'}));fireEvent.change(screen.getByLabelText('文稿标题'),{target:{value:'冲突标题'}});fireEvent.click(screen.getByRole('button',{name:'保存标题'}));await screen.findAllByText(/保存失败/);fireEvent.change(screen.getByLabelText('抖音分享链接'),{target:{value:'https://v.douyin.com/a/'}});fireEvent.click(screen.getByRole('button',{name:'开始提取'}));await act(async()=>{await new Promise(resolve=>setTimeout(resolve,10));});expect(starts).toBe(0);});
it('retries a failed history read without leaving the editor stuck',async()=>{const previous=fetch;let rejected=false;vi.stubGlobal('fetch',vi.fn(async(input:string,init?:RequestInit)=>{if(String(input)==='/api/history/a'&&!init?.method&&!rejected){rejected=true;return new Response(JSON.stringify({detail:'磁盘暂不可用'}),{status:503});}return previous(input,init);}));render(<App/>);await screen.findByText(/磁盘暂不可用/);fireEvent.click(screen.getByRole('button',{name:'重试保存'}));await waitFor(()=>expect(screen.getByLabelText('文稿全文')).toHaveValue('已保存的校对稿'));});
it('keeps a completed result read-only until its persisted history record is loaded',async()=>{
 const previous=fetch;let finish!:(response:Response)=>void;
 vi.stubGlobal('fetch',vi.fn(async(input:string,init?:RequestInit)=>{
  if(String(input)==='/api/session')return new Response(JSON.stringify({token:'secret',model:ready,models:{fast:ready,accurate:ready},dependencies:{ffmpeg:true,ffprobe:true},active_task:{id:'job',status:'completed',history_id:'a',stage:'completed',text:'原始任务文本',source:'transcription',error:null}}));
  if(String(input)==='/api/history/a'&&!init?.method)return new Promise<Response>(resolve=>finish=resolve);
  return previous(input,init);
 }));
 render(<App/>);await screen.findByText('已完成');
 const editor=screen.getByLabelText('文稿全文');expect(editor).toHaveAttribute('readonly');
 await act(async()=>finish(new Response(JSON.stringify(records[0]))));await waitFor(()=>expect(editor).toHaveValue('已保存的校对稿'));
 expect(editor).not.toHaveAttribute('readonly');
 fireEvent.change(editor,{target:{value:'加载后编辑'}});fireEvent.change(screen.getByLabelText('抖音分享链接'),{target:{value:'https://v.douyin.com/a/'}});fireEvent.click(screen.getByRole('button',{name:'开始提取'}));await waitFor(()=>expect(starts).toBe(1));expect(patches[0].edited_text).toBe('加载后编辑');
});
it('does not let a delayed initial restore list replace an explicit history selection',async()=>{
 const previous=fetch;let finish!:(response:Response)=>void;let captured=false;
 vi.stubGlobal('fetch',vi.fn(async(input:string,init?:RequestInit)=>{
  if(String(input)==='/api/history?q='&&!captured){captured=true;return new Promise<Response>(resolve=>finish=resolve);}
  return previous(input,init);
 }));
 render(<App/>);await screen.findByRole('button',{name:/第二份文稿/});fireEvent.click(screen.getByRole('button',{name:/第二份文稿/}));await waitFor(()=>expect(screen.getByLabelText('文稿全文')).toHaveValue('第二稿'));
 await act(async()=>{finish(new Response(JSON.stringify({items:records})));});
 await act(async()=>{await new Promise(resolve=>setTimeout(resolve,20));});expect(screen.getByLabelText('文稿全文')).toHaveValue('第二稿');
});
it('keeps the fresh empty workbench read-only without a saved backing record',async()=>{
 records=[];render(<App/>);await waitFor(()=>expect(screen.getByText('已就绪')).toBeVisible());expect(screen.getByLabelText('文稿全文')).toHaveAttribute('readonly');
});
it('does not let a delayed initial record GET replace a later explicit selection',async()=>{
 const previous=fetch;let finish!:(response:Response)=>void;let captured=false;
 vi.stubGlobal('fetch',vi.fn(async(input:string,init?:RequestInit)=>{
  if(String(input)==='/api/history/a'&&!init?.method&&!captured){captured=true;return new Promise<Response>(resolve=>finish=resolve);}
  return previous(input,init);
 }));
 render(<App/>);await waitFor(()=>expect(captured).toBe(true));fireEvent.click(await screen.findByRole('button',{name:/第二份文稿/}));await waitFor(()=>expect(screen.getByLabelText('文稿全文')).toHaveValue('第二稿'));
 await act(async()=>finish(new Response(JSON.stringify(records[0]))));expect(screen.getByLabelText('文稿全文')).toHaveValue('第二稿');
});
