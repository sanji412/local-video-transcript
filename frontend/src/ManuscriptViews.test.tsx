import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import App from './App';
import type { HistoryRecord, Task } from './api';

const ready = { status: 'ready', detail: '就绪', progress: 1 };
let record: HistoryRecord;
let candidate: Task | null;
let patches: unknown[];

beforeEach(() => {
  sessionStorage.clear();
  patches = [];
  candidate = null;
  record = {
    id: 'a', title: '测试文稿', source_type: 'file', source_ref: 'a.mp4',
    created_at: '2026-11-02T00:00:00Z', updated_at: '2026-11-02T00:00:00Z',
    source: 'transcription', mode: 'accurate', revision: 0, preview: '甲 乙',
    original_text: '甲\n乙', edited_text: '甲\n乙', audio_available: false, audio_retained: false,
    segments: [
      { id: 0, start: 0, end: 1, text: '甲', original_text: '甲' },
      { id: 1, start: 1, end: 2, text: '乙', original_text: '乙' },
    ],
  };
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    let body: unknown;
    if (url === '/api/session') body = {
      token: 'token', model: ready, models: { fast: ready, accurate: ready },
      dependencies: { ffmpeg: true, ffprobe: true }, active_task: candidate,
    };
    else if (url.startsWith('/api/history?')) body = { items: [record] };
    else if (init?.method === 'PATCH') {
      const change = JSON.parse(init.body as string);
      expect(change.revision).toBe(record.revision);
      patches.push(change);
      record = { ...record, revision: record.revision + 1, segments: record.segments!.map(segment =>
        segment.id === Number(url.split('/').at(-1)) ? { ...segment, text: change.text } : segment) };
      record.edited_text = record.segments!.map(segment => segment.text).join('\n');
      body = record;
    } else body = record;
    return new Response(JSON.stringify(body));
  }));
});
afterEach(() => { vi.unstubAllGlobals(); sessionStorage.clear(); });

function completedCandidate(id = 'candidate-one'): Task {
  return { id, kind: 'retry', history_id: 'a', segment_id: 0, status: 'completed',
    stage: 'completed', detail: '局部识别完成', text: '候选甲', source: 'transcription', error: null };
}

it('shows the latest complete edited manuscript between editing and immutable original', async () => {
  render(<App autosaveDelay={10000} />);
  const editor = await screen.findByLabelText('片段 1');
  fireEvent.change(editor, { target: { value: '最新校对甲' } });
  fireEvent.click(screen.getByRole('button', { name: '完整稿' }));
  const complete = screen.getByLabelText('编辑后的完整稿');
  expect(complete).toHaveValue('最新校对甲\n乙');
  expect(complete).toHaveAttribute('readonly');
  expect(screen.queryByLabelText('片段 1')).not.toBeInTheDocument();
  expect([...document.querySelectorAll('.manuscript-versions button')].map(button => button.textContent))
    .toEqual(['编辑稿', '完整稿', '原始转写']);
  fireEvent.click(screen.getByRole('button', { name: '原始转写' }));
  expect(screen.getByLabelText('原始转写全文')).toHaveValue('甲\n乙');
  fireEvent.click(screen.getByRole('button', { name: '编辑稿' }));
  expect(screen.getByLabelText('片段 1')).toHaveValue('最新校对甲');
});

it('rejects a candidate without changing any text and keeps it dismissed across views and refresh', async () => {
  candidate = completedCandidate();
  const view = render(<App />);
  await screen.findByText('候选甲');
  fireEvent.click(screen.getByRole('button', { name: '不采用' }));
  expect(screen.queryByText('候选甲')).not.toBeInTheDocument();
  expect(screen.getByLabelText('片段 1')).toHaveValue('甲');
  expect(patches).toEqual([]);
  fireEvent.click(screen.getByRole('button', { name: '完整稿' }));
  expect(screen.getByLabelText('编辑后的完整稿')).toHaveValue('甲\n乙');
  fireEvent.click(screen.getByRole('button', { name: '编辑稿' }));
  expect(screen.queryByText('候选甲')).not.toBeInTheDocument();
  view.unmount();
  const restored = render(<App />);
  await screen.findByLabelText('片段 1');
  expect(screen.queryByText('候选甲')).not.toBeInTheDocument();
  restored.unmount();
  candidate = completedCandidate('new-recognition');
  render(<App />);
  expect(await screen.findByText('候选甲')).toBeVisible();
});

it('keeps an adopted candidate resolved when switching views and reopening the page', async () => {
  candidate = completedCandidate();
  const view = render(<App />);
  await screen.findByText('候选甲');
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '采用识别结果' })); });
  await waitFor(() => expect(patches).toHaveLength(1));
  await waitFor(() => expect(screen.queryByRole('button', { name: '采用识别结果' })).not.toBeInTheDocument());
  fireEvent.click(screen.getByRole('button', { name: '完整稿' }));
  expect(screen.getByLabelText('编辑后的完整稿')).toHaveValue('候选甲\n乙');
  fireEvent.click(screen.getByRole('button', { name: '原始转写' }));
  expect(screen.getByLabelText('原始转写全文')).toHaveValue('甲\n乙');
  view.unmount();
  render(<App />);
  await screen.findByLabelText('片段 1');
  expect(screen.getByLabelText('片段 1')).toHaveValue('候选甲');
  expect(screen.queryByRole('button', { name: '采用识别结果' })).not.toBeInTheDocument();
});
