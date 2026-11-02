export type ModelStatus = { status: 'missing' | 'downloading' | 'ready' | 'error'; detail: string; progress: number | null };
export type RecognitionMode = 'fast' | 'accurate';
export type Timing = { recognition_seconds?: number | null; total_seconds?: number | null; media_duration_seconds?: number | null };
export type HistorySummary = Timing & { id: string; title: string; source_type: 'link' | 'file'; source_ref: string; created_at: string; updated_at: string; source: 'subtitle' | 'transcription'; mode: RecognitionMode; revision: number; preview: string };
export type Segment = { id: number; start: number; end: number; original_text: string; text: string };
export type HistoryRecord = HistorySummary & { original_text: string; edited_text: string; segments?: Segment[]; audio_available?: boolean; audio_retained?: boolean };
export class ApiError extends Error { constructor(message: string, public status: number, public duplicate?: HistorySummary) { super(message); } }
export type Task = Timing & { batch_id?: string | null; id: string; kind?: 'task' | 'retry'; segment_id?: number; mode?: RecognitionMode; history_id?: string | null; status: 'running' | 'completed' | 'failed' | 'cancelled'; stage: string; detail: string; text: string; source: 'subtitle' | 'transcription' | null; error: string | null };
export type BatchItem = Timing & { source?: string | null; id: string; index: number; url: string; title: string; share_text: string; status: 'pending' | 'running' | 'completed' | 'failed' | 'skipped' | 'cancelled'; force: boolean; task_id: string | null; history_id: string | null; error: string | null; detail: string };
export type Batch = { id: string; status: 'running' | 'paused' | 'completed' | 'cancelled'; mode: RecognitionMode; retain_audio: boolean; items: BatchItem[] };
export type BatchPreview = { index: number; share_text: string; title: string; url: string | null; status: 'ready' | 'duplicate' | 'invalid'; existing?: HistorySummary | null; duplicate_of?: number | null; error?: string | null };
export type Session = { batch?: Batch | null; token: string; model: ModelStatus; models?: Record<RecognitionMode, ModelStatus>; dependencies: { ffmpeg: boolean; ffprobe: boolean }; active_task: Task | null };

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
  if (!response.ok) throw new ApiError(typeof body?.detail === 'string' ? body.detail : `请求失败（${response.status}），请重试。`, response.status, body?.duplicate);
  return body as T;
}
