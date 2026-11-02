import {render,screen,fireEvent,waitFor} from '@testing-library/react';
import {it,expect,vi} from 'vitest';
import SegmentEditor from './SegmentEditor';
import type {HistoryRecord,Task} from './api';
it('keeps a candidate separate until explicit adoption and stops playback at the segment boundary',async()=>{
 const apply=vi.fn(async()=>{});const play=vi.spyOn(HTMLMediaElement.prototype,'play').mockResolvedValue();const pause=vi.spyOn(HTMLMediaElement.prototype,'pause').mockImplementation(()=>{});
 const record={id:'a',audio_available:true,segments:[{id:0,start:2,end:4,text:'编辑稿',original_text:'原稿'}]} as HistoryRecord;
 const task={id:'retry',kind:'retry',history_id:'a',segment_id:0,status:'completed',text:'候选'} as Task;
 render(<SegmentEditor record={record} task={task} disabled={false} text={()=>'编辑稿'} edit={vi.fn()} retry={vi.fn()} clear={vi.fn()} apply={apply}/>);
 expect(screen.getByLabelText('片段 1')).toHaveValue('编辑稿');expect(apply).not.toHaveBeenCalled();
 fireEvent.click(screen.getByText('采用识别结果'));await waitFor(()=>expect(apply).toHaveBeenCalledWith(0,'候选'));
 fireEvent.click(screen.getByText(/· 回听/));const audio=screen.getByLabelText('回听音频') as HTMLAudioElement;expect(audio.currentTime).toBe(2);expect(play).toHaveBeenCalled();audio.currentTime=4;fireEvent.timeUpdate(audio);expect(pause).toHaveBeenCalled();
 fireEvent.error(audio);expect(screen.getByRole('alert')).toHaveTextContent('音频无法播放');play.mockRestore();pause.mockRestore();
});
