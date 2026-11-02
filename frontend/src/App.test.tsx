import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';

const ready = { status: 'ready', detail: '模型已就绪', progress: 1 };
const task = (overrides = {}) => ({ id: 'one', status: 'running', stage: 'downloading', detail: '正在下载视频', text: '', source: null, error: null, ...overrides });
let session: Record<string, unknown>;
let route: (url: string, init?: RequestInit) => unknown;
let requests: { url: string; init?: RequestInit }[];
beforeEach(() => {
  session = { token: 'secret-token', model: ready, dependencies: { ffmpeg: true, ffprobe: true }, active_task: null };
  requests = [];
  route = () => ready;
  vi.stubGlobal('fetch', vi.fn(async (input: string, init?: RequestInit) => {
    const url = String(input); requests.push({ url, init });
    const value = url === '/api/session' ? session : url.startsWith('/api/history?') ? {items:[]} : await route(url, init);
    return value instanceof Response ? value : new Response(JSON.stringify(value));
  }));
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('本地文案工作台', () => {
  it('shows an honest empty workspace and blocks submission without input', async () => {
    render(<App pollInterval={15} />);
    expect(screen.getByLabelText('文稿全文')).toHaveAttribute('readonly');
    expect(await screen.findByRole('heading', { name: '把视频，变成可用的文字。' })).toBeVisible();
    expect(screen.getByLabelText('抖音分享链接')).toBeVisible();
    expect(screen.getByText(/普通话口播 \/ 30 分钟以内/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '开始提取' })).toBeDisabled();
    expect(screen.getByText('文稿将在这里展开')).toBeVisible();
  });

  it('submits a link with the session token, polls stages, and copies / exports edited text', async () => {
    const user = userEvent.setup();
    let polls = 0;
    route = (url, init) => {
      if (url === '/api/tasks/link') return task();
      if (url === '/api/tasks/one' && !init?.method) return ++polls === 1 ? task({ stage: 'transcribing', detail: '正在转写全文' }) : task({ status: 'completed', stage: 'completed', detail: '提取完成', text: '原始文案', source: 'transcription' });
      return ready;
    };
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
    const createURL = vi.fn().mockReturnValue('blob:export');
    vi.stubGlobal('URL', class extends URL { static createObjectURL = createURL; static revokeObjectURL = vi.fn(); });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    render(<App pollInterval={30} />);
    await user.type(await screen.findByLabelText('抖音分享链接'), '看看 https://v.douyin.com/abc/');
    await user.click(screen.getByRole('button', { name: '开始提取' }));
    expect(await screen.findByText('正在转写全文')).toBeVisible();
    const editor = await screen.findByLabelText('文稿全文');
    await waitFor(() => expect(editor).toHaveValue('原始文案'));
    expect(screen.getByText('语音转写')).toBeVisible();
    await user.clear(editor); await user.type(editor, '校对后的全文\n第二行');
    await user.click(screen.getByRole('button', { name: '复制全文' }));
    expect(writeText).toHaveBeenCalledWith('校对后的全文\n第二行');
    await user.click(screen.getByRole('button', { name: '导出 TXT' }));
    const blob = createURL.mock.calls[0][0] as Blob;
    const exported = await new Promise(resolve => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.readAsText(blob); });
    expect(exported).toBe('校对后的全文\n第二行');
    expect(click).toHaveBeenCalled();
    const post = requests.find(r => r.url === '/api/tasks/link')!;
    expect(new Headers(post.init?.headers).get('X-Session-Token')).toBe('secret-token');
    expect(JSON.parse(post.init?.body as string)).toEqual({ share_text: '看看 https://v.douyin.com/abc/', mode: 'accurate', retain_audio: false });
    click.mockRestore();
  });

  it('prepares a missing model explicitly and waits for readiness before submission', async () => {
    const user = userEvent.setup();
    session.model = { status: 'missing', detail: '首次使用需要下载模型', progress: null };
    let polls = 0;
    route = (url) => url === '/api/model/download?mode=accurate' ? { status: 'downloading', detail: '正在准备模型', progress: .1 } : ++polls === 1 ? { status: 'downloading', detail: '下载中', progress: .5 } : ready;
    render(<App pollInterval={30} />);
    await waitFor(() => expect(screen.getByLabelText('抖音分享链接')).toBeEnabled());
    await user.type(await screen.findByLabelText('抖音分享链接'), 'https://v.douyin.com/abc/');
    expect(screen.getByLabelText('抖音分享链接')).toHaveValue('https://v.douyin.com/abc/');
    expect(screen.getByRole('button', { name: '开始提取' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: '下载模型' }));
    expect(await screen.findByRole('progressbar')).toBeVisible();
    await waitFor(() => expect(screen.getByRole('button', { name: '开始提取' })).toBeEnabled());
    expect(new Headers(requests.find(r => r.url === '/api/model/download?mode=accurate')?.init?.headers).get('X-Session-Token')).toBe('secret-token');
  });

  it('imports a file, displays its name, and cancels the active task', async () => {
    const user = userEvent.setup();
    route = (url) => url.endsWith('/cancel') ? task({ status: 'cancelled', stage: 'cancelled', detail: '任务已取消' }) : task();
    render(<App pollInterval={20} />);
    await screen.findByLabelText('抖音分享链接');
    await user.click(screen.getByRole('tab', { name: '本地 MP4' }));
    const file = new File(['video'], '我的视频.mp4', { type: 'video/mp4' });
    await user.upload(screen.getByLabelText('选择 MP4 文件'), file);
    expect(screen.getByText('我的视频.mp4')).toBeVisible();
    await user.click(screen.getByRole('button', { name: '开始提取' }));
    await user.click(await screen.findByRole('button', { name: '取消任务' }));
    expect(await screen.findByText('已取消')).toBeVisible();
    const post = requests.find(r => r.url === '/api/tasks/file')!;
    expect((post.init?.body as FormData).get('file')).toEqual(file);
    expect(new Headers(post.init?.headers).has('Content-Type')).toBe(false);
  });

  it('retries a failed task with the retained input', async () => {
    const user = userEvent.setup();
    let starts = 0;
    route = (url) => url === '/api/tasks/link' ? (++starts === 1 ? task({ status: 'failed', error: '链接暂时无法获取，请导入 MP4', detail: '提取失败' }) : task({ id: 'two' })) : task({ id: 'two' });
    render(<App pollInterval={20} />);
    await user.type(await screen.findByLabelText('抖音分享链接'), 'https://v.douyin.com/abc/');
    await user.click(screen.getByRole('button', { name: '开始提取' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('链接暂时无法获取，请导入 MP4');
    await user.click(screen.getByRole('button', { name: '重试提取' }));
    expect(starts).toBe(2);
  });

  it('restores an active task, waits for the complete result, then permits editing', async () => {
    session.active_task = task({ text: '已恢复的内容' });
    let resolvePoll: (value: unknown) => void = () => {};
    route = () => new Promise(resolve => { resolvePoll = resolve; });
    const user = userEvent.setup();
    render(<App pollInterval={10} />);
    const editor = await screen.findByLabelText('文稿全文');
    await waitFor(() => expect(editor).toHaveValue('已恢复的内容'));
    expect(editor).toHaveAttribute('readonly');
    await user.type(editor, '过早编辑');
    expect(editor).toHaveValue('已恢复的内容');
    await act(async () => resolvePoll(task({ status: 'completed', text: '后台结果', source: 'subtitle' })));
    expect(editor).toHaveValue('后台结果');
    expect(editor).not.toHaveAttribute('readonly');
    await user.clear(editor); await user.type(editor, '我的编辑');
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)); });
    expect(editor).toHaveValue('我的编辑');
    expect(screen.getByText('字幕提取')).toBeVisible();
  });

  it('explains missing tools and prevents extraction', async () => {
    session.dependencies = { ffmpeg: false, ffprobe: false };
    render(<App />);
    expect(await screen.findByText(/缺少 FFmpeg/)).toBeVisible();
    expect(screen.getByRole('button', { name: '开始提取' })).toBeDisabled();
  });

  it('shows API detail errors and permits resubmitting the retained input', async () => {
    const user = userEvent.setup();
    let starts = 0;
    route = (url) => url === '/api/tasks/link' && ++starts === 1 ? new Response(JSON.stringify({ detail: '分享内容包含多个链接，请只保留一个。' }), { status: 400 }) : task();
    render(<App pollInterval={30} />);
    await user.type(await screen.findByLabelText('抖音分享链接'), 'https://v.douyin.com/abc/');
    await user.click(screen.getByRole('button', { name: '开始提取' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('分享内容包含多个链接，请只保留一个。');
    expect(screen.getByLabelText('抖音分享链接')).toHaveValue('https://v.douyin.com/abc/');
    await user.click(screen.getByRole('button', { name: '重试提取' }));
    expect(await screen.findByRole('button', { name: '取消任务' })).toBeEnabled();
  });

  it('keeps one pending poll and rejects its late result after cancellation', async () => {
    const user = userEvent.setup();
    session.active_task = task({ stage: 'custom-stage', detail: '正在检查本地视频' });
    let polls = 0;
    let resolvePoll: (value: unknown) => void = () => {};
    route = (url) => url.endsWith('/cancel') ? task({ status: 'cancelled', detail: '已停止' }) : new Promise(resolve => { polls++; resolvePoll = resolve; });
    render(<App pollInterval={10} />);
    expect(await screen.findByText('处理中')).toBeVisible();
    expect(screen.queryByText('custom-stage')).not.toBeInTheDocument();
    await waitFor(() => expect(polls).toBe(1));
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 50)); });
    expect(polls).toBe(1);
    await user.click(screen.getByRole('button', { name: '取消任务' }));
    await act(async () => resolvePoll(task({ status: 'completed', text: '过期结果' })));
    expect(screen.getByText('已取消')).toBeVisible();
    expect(screen.getByLabelText('文稿全文')).toHaveValue('');
  });

  it('can retry a failed model download', async () => {
    const user = userEvent.setup();
    session.model = { status: 'error', detail: '网络中断，下载未完成', progress: null };
    route = () => ready;
    render(<App />);
    await user.click(await screen.findByRole('button', { name: '重新下载模型' }));
    expect(await screen.findByText('模型已就绪')).toBeVisible();
  });

  it('clearly marks a disconnected startup and reconnects to the local service', async () => {
    const user = userEvent.setup();
    let disconnected = true;
    vi.stubGlobal('fetch', vi.fn(async (url: string) => { if (url === '/api/session' && disconnected) { disconnected = false; throw new TypeError('Failed to fetch'); } return new Response(JSON.stringify(url === '/api/session' ? session : {items:[]})); }));
    render(<App />);
    expect(await screen.findByRole('alert')).toHaveTextContent('无法连接本地服务');
    expect(screen.getByText('无法连接')).toBeVisible();
    expect(screen.getByRole('button', { name: '开始提取' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: '重新连接本地服务' }));
    expect(await screen.findByText('模型已就绪')).toBeVisible();
  });

  it('displays fractional model progress as fifty percent', async () => {
    session.model = { status: 'downloading', detail: '下载模型中', progress: .5 };
    render(<App pollInterval={1000} />);
    const progress = await screen.findByRole('progressbar', { name: '模型下载进度' });
    expect(progress).toHaveAttribute('max', '1');
    expect(progress).toHaveAttribute('value', '0.5');
    expect(progress).toHaveAttribute('aria-valuetext', '50%');
  });

  it.each([['preparing', '准备开始'], ['checking', '检查素材'], ['subtitles', '提取字幕'], ['audio', '处理音频'], ['converting', '处理音频']])('translates backend stage %s', async (stage, label) => {
    session.active_task = task({ stage });
    render(<App pollInterval={1000} />);
    expect(await screen.findByText(label)).toBeVisible();
  });

  it('refreshes a restarted session after task 404, clears its lost task, and submits with the new token', async () => {
    const user = userEvent.setup();
    session.active_task = task();
    route = (url) => url === '/api/tasks/one' ? new Response(JSON.stringify({ detail: '任务不存在，请重新导入。' }), { status: 404 }) : task({ id: 'new-task' });
    render(<App pollInterval={10} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('任务不存在');
    expect(screen.getByLabelText('抖音分享链接')).toBeDisabled();
    session = { token: 'new-token', model: ready, dependencies: { ffmpeg: true, ffprobe: true }, active_task: null };
    await user.click(screen.getByRole('button', { name: /^重新连接$/ }));
    await waitFor(() => expect(screen.getByLabelText('抖音分享链接')).toBeEnabled());
    expect(screen.queryByRole('button', { name: '取消任务' })).not.toBeInTheDocument();
    expect(requests.filter(r => r.url === '/api/session')).toHaveLength(2);
    const oldReads = requests.filter(r => r.url === '/api/tasks/one').length;
    await user.type(screen.getByLabelText('抖音分享链接'), 'https://v.douyin.com/new/');
    await user.click(screen.getByRole('button', { name: '开始提取' }));
    const post = requests.find(r => r.url === '/api/tasks/link')!;
    expect(new Headers(post.init?.headers).get('X-Session-Token')).toBe('new-token');
    expect(requests.filter(r => r.url === '/api/tasks/one')).toHaveLength(oldReads);
  });

  it('reconciles a replacement task from the refreshed session and polls its new ID', async () => {
    const user = userEvent.setup();
    session.active_task = task();
    route = (url) => url === '/api/tasks/one' ? new Response(JSON.stringify({ detail: '旧任务已失效' }), { status: 404 }) : task({ id: 'replacement', status: 'completed', text: '新的完整文稿', source: 'transcription' });
    render(<App pollInterval={10} />);
    await screen.findByRole('alert');
    session = { ...session, token: 'replacement-token', active_task: task({ id: 'replacement', detail: '已恢复另一窗口的任务' }) };
    await user.click(screen.getByRole('button', { name: /^重新连接$/ }));
    await waitFor(() => expect(screen.getByLabelText('文稿全文')).toHaveValue('新的完整文稿'));
    expect(requests.some(r => r.url === '/api/tasks/replacement')).toBe(true);
  });

  it('refreshes model/session state after a failed model poll while keeping an edited completed draft', async () => {
    const user = userEvent.setup();
    session.active_task = task({ status: 'completed', text: '已经完成的文稿', source: 'subtitle' });
    session.model = { status: 'downloading', detail: '模型准备中', progress: .5 };
    route = () => new Response(JSON.stringify({ detail: '服务已重启' }), { status: 503 });
    render(<App pollInterval={40} />);
    const editor = screen.getByLabelText('文稿全文');
    await waitFor(() => expect(editor).toHaveValue('已经完成的文稿'));
    await user.clear(editor); await user.type(editor, '保留我的校对稿');
    await screen.findByRole('alert');
    session = { token: 'refreshed-model-token', model: { status: 'missing', detail: '模型需要重新准备', progress: null }, dependencies: { ffmpeg: true, ffprobe: true }, active_task: null };
    route = () => ready;
    await user.click(screen.getByRole('button', { name: /^重新连接$/ }));
    await user.click(await screen.findByRole('button', { name: '下载模型' }));
    expect(editor).toHaveValue('保留我的校对稿');
    const post = requests.find(r => r.url === '/api/model/download?mode=accurate')!;
    expect(new Headers(post.init?.headers).get('X-Session-Token')).toBe('refreshed-model-token');
  });

  it('keeps the prior edited draft exportable when reconnecting to another active task', async () => {
    const user = userEvent.setup();
    session.active_task = task({ status: 'completed', text: '上一份文稿' });
    session.model = { status: 'downloading', detail: '模型准备中', progress: .5 };
    route = () => new Response(JSON.stringify({ detail: '连接中断' }), { status: 503 });
    const createURL = vi.fn().mockReturnValue('blob:saved-draft');
    vi.stubGlobal('URL', class extends URL { static createObjectURL = createURL; static revokeObjectURL = vi.fn(); });
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    render(<App pollInterval={40} />);
    const editor = screen.getByLabelText('文稿全文');
    await waitFor(() => expect(editor).toHaveValue('上一份文稿'));
    await user.clear(editor); await user.type(editor, '我编辑后的上一份文稿');
    await screen.findByRole('alert');
    session = { ...session, token: 'another-token', model: ready, active_task: task({ id: 'another' }) };
    route = () => task({ id: 'another', status: 'completed', text: '另一份完整文稿' });
    await user.click(screen.getByRole('button', { name: /^重新连接$/ }));
    await waitFor(() => expect(editor).toHaveValue('另一份完整文稿'));
    await user.click(screen.getByRole('button', { name: '导出上一份文稿' }));
    const blob = createURL.mock.calls[0][0] as Blob;
    const exported = await new Promise(resolve => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.readAsText(blob); });
    expect(exported).toBe('我编辑后的上一份文稿');
  });

  it('also refreshes an expired session token after a model download mutation fails', async () => {
    const user = userEvent.setup();
    session.model = { status: 'missing', detail: '需要下载模型', progress: null };
    route = () => new Response(JSON.stringify({ detail: '会话令牌已失效' }), { status: 403 });
    render(<App />);
    await user.click(await screen.findByRole('button', { name: '下载模型' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('会话令牌已失效');
    session = { ...session, token: 'fresh-token' };
    route = () => ready;
    await user.click(screen.getByRole('button', { name: '重新连接本地服务' }));
    await waitFor(() => expect(requests.filter(r => r.url === '/api/session')).toHaveLength(2));
    await user.click(screen.getByRole('button', { name: '下载模型' }));
    const posts = requests.filter(r => r.url === '/api/model/download?mode=accurate');
    expect(new Headers(posts.at(-1)?.init?.headers).get('X-Session-Token')).toBe('fresh-token');
  });
  it('blocks session reconnect while a retry submission is pending and releases busy after completion', async () => {
    const user = userEvent.setup();
    session.active_task = task({ status: 'failed', stage: 'failed', error: '下载失败', detail: '请重试' });
    let finish: (value: unknown) => void = () => {};
    route = () => new Promise(resolve => { finish = resolve; });
    render(<App pollInterval={1000} />);
    await user.type(await screen.findByLabelText('抖音分享链接'), 'https://v.douyin.com/abc/');
    await user.click(screen.getByRole('button', { name: '重试提取' }));
    expect(screen.getByRole('button', { name: '重新连接本地服务' })).toBeDisabled();
    await act(async () => finish(task({ status: 'completed', text: '重试成功全文', source: 'transcription' })));
    await waitFor(() => expect(screen.getByLabelText('文稿全文')).toHaveValue('重试成功全文'));
    expect(screen.getByRole('button', { name: '开始提取' })).toBeEnabled();
  });

});

it('keeps failed local recognition away from full-video retry routing', async () => {
 session.active_task = task({kind:'retry',status:'failed',history_id:'a',segment_id:0});
 route = url => url.endsWith('/recognize') ? task({kind:'retry',status:'failed',history_id:'a',segment_id:0}) : url === '/api/history/a' ? {id:'a',title:'文稿',revision:1,edited_text:'稿',original_text:'稿',audio_available:true,segments:[{id:0,start:0,end:1,text:'稿',original_text:'稿'}]} : ready;
 const user=userEvent.setup();render(<App/>);
 await screen.findByLabelText('片段 1');
 await user.type(screen.getByLabelText('抖音分享链接'),'https://v.douyin.com/abc/');
 expect(screen.queryByRole('button',{name:'重试提取'})).not.toBeInTheDocument();
 await user.click(screen.getByRole('button',{name:'重新识别此段'}));
 expect(requests.some(r=>r.url==='/api/history/a/segments/0/recognize'&&r.init?.method==='POST')).toBe(true);
 expect(requests.some(r=>r.url==='/api/tasks/link')).toBe(false);
});
