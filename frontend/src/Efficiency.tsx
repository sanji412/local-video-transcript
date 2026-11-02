import { useEffect, useState } from 'react';
import type { Timing } from './api';
const seconds = (value?: number | null) => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? `${value.toFixed(1)} 秒` : '未测量';
export default function Efficiency({ value, source, running = false }: { value: Timing; source?: string | null; running?: boolean }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    setElapsed(0);
    if (!running) return;
    const started = Date.now();
    const timer = setInterval(() => setElapsed((Date.now() - started) / 1000), 1000);
    return () => clearInterval(timer);
  }, [running, value.total_seconds]);
  const recognition = value.recognition_seconds;
  const duration = value.media_duration_seconds;
  const speed = source !== 'subtitle' && typeof recognition === 'number' && Number.isFinite(recognition) && recognition > 0 && typeof duration === 'number' && Number.isFinite(duration) && duration > 0 ? `${(duration / recognition).toFixed(1)} 倍实时` : null;
  return <div className="efficiency" aria-label="处理耗时"><span>{running ? `已用时间：${seconds((value.total_seconds ?? 0) + elapsed)}` : `总处理（不含排队）：${seconds(value.total_seconds)}`}</span><span>模型识别（含加载）：{source === 'subtitle' ? '未调用模型（字幕提取）' : seconds(recognition)}</span><span>视频时长：{seconds(duration)}</span>{speed && <span>{speed}</span>}</div>;
}
