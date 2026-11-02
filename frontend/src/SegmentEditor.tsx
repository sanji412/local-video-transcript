import { useEffect, useRef, useState } from 'react';
import type { HistoryRecord, Task } from './api';
export default function SegmentEditor({ record, disabled, text, edit, retry, apply, clear, task }: {
    record: HistoryRecord;
    disabled: boolean;
    text: (id: number) => string;
    edit: (id: number, value: string) => void;
    retry: (id: number) => void;
    apply: (id: number, value: string) => Promise<void>;
    clear: () => void;
    task: Task | null;
}) {
    const audio = useRef<HTMLAudioElement>(null);
    const boundary = useRef<number | null>(null);
    const [error, setError] = useState('');
    const [adopted, setAdopted] = useState<string | null>(null);
    useEffect(() => {
        const player = audio.current;
        boundary.current = null;
        // StrictMode replays setup after cleanup without remounting this node.
        if (player && !player.getAttribute('src')) {
            player.setAttribute('src', `/api/history/${encodeURIComponent(record.id)}/audio`);
        }
        return () => {
            boundary.current = null;
            if (player) {
                player.pause();
                player.removeAttribute('src');
                player.load();
            }
        };
    }, [record.id, record.audio_available]);
    const stamp = (seconds: number) => {
        const tenths = Math.round(seconds * 10);
        return `${Math.floor(tenths / 600).toString().padStart(2, '0')}:${((tenths % 600) / 10).toFixed(1).padStart(4, '0')}`;
    };
    async function play(start: number, end: number) { if (!audio.current)
        return; try {
        boundary.current = end;
        audio.current.currentTime = start;
        await audio.current.play();
        setError('');
    }
    catch {
        setError('音频无法播放，请确认回听音频仍可用。');
    } }
    return <div className="segment-editor">
 {record.audio_available ? <><audio key={record.id} aria-label="回听音频" controls ref={audio} src={`/api/history/${encodeURIComponent(record.id)}/audio`} onError={() => setError('音频无法播放，请重新导入素材。')} onTimeUpdate={() => { if (audio.current && boundary.current !== null && audio.current.currentTime >= boundary.current) {
        audio.current.pause();
        boundary.current = null;
    } }}/><button className="text-button" disabled={disabled} onClick={clear}>清除回听音频</button><p className="field-hint">{record.audio_retained ? '音频随文稿保留，直到手动清除。' : '临时音频会在下次提取或服务重启时清理。'}</p></> : <p className="field-hint">无回听音频，可继续编辑；重新导入视频可恢复回听。</p>}
 {error && <p role="alert">{error}</p>}
 <div className="segment-list">{record.segments?.map(s => <div className="segment-row" key={s.id}><div><button className="text-button" disabled={!record.audio_available} onClick={() => void play(s.start, s.end)}>{stamp(s.start)} – {stamp(s.end)} · 回听</button><button className="text-button" disabled={disabled || !record.audio_available} onClick={() => retry(s.id)}>重新识别此段</button></div><textarea aria-label={`片段 ${s.id + 1}`} value={text(s.id)} maxLength={10000} disabled={disabled} onChange={e => edit(s.id, e.target.value)}/>{task?.kind === 'retry' && task.history_id === record.id && task.segment_id === s.id && task.status === 'completed' && adopted !== task.id && <div className="draft-banner"><p>识别候选（尚未采用）</p><p>{task.text}</p><button disabled={disabled} onClick={() => void apply(s.id, task.text).then(() => setAdopted(task.id)).catch(e => setError(e instanceof Error ? e.message : "采用失败，请重试"))}>采用识别结果</button></div>}</div>)}
 </div></div>;
}
