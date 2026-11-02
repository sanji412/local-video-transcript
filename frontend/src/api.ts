export type ModelStatus = { status: 'missing' | 'downloading' | 'ready' | 'error'; detail: string; progress: number | null };
export type RecognitionMode = 'fast' | 'accurate';
export type HistorySummary = { id: string; title: string; source_type: 'link' | 'file'; source_ref: string; created_at: string; updated_at: string; source: 'subtitle' | 'transcription'; mode: RecognitionMode; revision: number; preview: string };
export type Segment = { id: number; start: number; end: number; original_text: string; text: string };
export type HistoryRecord = HistorySummary & { original_text: string; edited_text: string; segments?: Segment[]; audio_available?: boolean; audio_retained?: boolean };
export class ApiError extends Error { constructor(message: string, public status: number) { super(message); } }
export type Task = { id: string; kind?: 'task' | 'retry'; segment_id?: number; mode?: RecognitionMode; history_id?: string | null; status: 'running' | 'completed' | 'failed' | 'cancelled'; stage: string; detail: string; text: string; source: 'subtitle' | 'transcription' | null; error: string | null };
export type Session = { token: string; model: ModelStatus; models?: Record<RecognitionMode, ModelStatus>; dependencies: { ffmpeg: boolean; ffprobe: boolean }; active_task: Task | null };

export async function request<T>(path: string, options: RequestInit = {}, token?: string): Promise<T> {
  const headers = new Headers(options.headers);
  if (token) headers.set('X-Session-Token', token);
  let response: Response;
  try { response = await fetch(`/api${path}`, { ...options, headers }); }
  catch (error) {
    if (options.signal?.aborted) throw error;
    throw new Error('无法连接本地服务，请确认工具正在运行后重试。');
  }
  if (response.status === 204 && response.ok) return undefined as T;
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(typeof body?.detail === 'string' ? body.detail : `请求失败（${response.status}），请重试。`, response.status);
  return body as T;
}
