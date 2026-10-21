export type ModelStatus = { status: 'missing' | 'downloading' | 'ready' | 'error'; detail: string; progress: number | null };
export type Task = { id: string; status: 'running' | 'completed' | 'failed' | 'cancelled'; stage: string; detail: string; text: string; source: 'subtitle' | 'transcription' | null; error: string | null };
export type Session = { token: string; model: ModelStatus; dependencies: { ffmpeg: boolean; ffprobe: boolean }; active_task: Task | null };

export async function request<T>(path: string, options: RequestInit = {}, token?: string): Promise<T> {
  const headers = new Headers(options.headers);
  if (token) headers.set('X-Session-Token', token);
  let response: Response;
  try { response = await fetch(`/api${path}`, { ...options, headers }); }
  catch (error) {
    if (options.signal?.aborted) throw error;
    throw new Error('无法连接本地服务，请确认工具正在运行后重试。');
  }
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new Error(typeof body?.detail === 'string' ? body.detail : `请求失败（${response.status}），请重试。`);
  return body as T;
}
