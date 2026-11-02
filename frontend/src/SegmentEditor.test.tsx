import {render,screen,fireEvent,waitFor} from '@testing-library/react';
import {StrictMode} from 'react';
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

it('rounds timestamps across minute boundaries and releases audio on removal',()=>{
 const pause=vi.spyOn(HTMLMediaElement.prototype,'pause').mockImplementation(()=>{});
 const load=vi.spyOn(HTMLMediaElement.prototype,'load').mockImplementation(()=>{});
 const record={id:'a',audio_available:true,segments:[{id:0,start:59.99,end:61,text:'稿'}]} as HistoryRecord;
 const props={record,task:null,disabled:false,text:()=> '稿',edit:vi.fn(),retry:vi.fn(),clear:vi.fn(),apply:vi.fn()};
 const view=render(<SegmentEditor {...props}/>);
 expect(screen.getByText(/01:00.0 – 01:01.0/)).toBeVisible();
 const audio=screen.getByLabelText('回听音频');
 view.rerender(<SegmentEditor {...props} record={{...record,audio_available:false}}/>);
 expect(pause).toHaveBeenCalled();expect(audio).not.toHaveAttribute('src');expect(load).toHaveBeenCalled();
 pause.mockRestore();load.mockRestore();
});

it('keeps the new audio source when switching manuscripts',()=>{
 const record={id:'a',audio_available:true,segments:[{id:0,start:0,end:1,text:'稿'}]} as HistoryRecord;
 const props={record,task:null,disabled:false,text:()=> '稿',edit:vi.fn(),retry:vi.fn(),clear:vi.fn(),apply:vi.fn()};
 const view=render(<SegmentEditor {...props}/>);
 const oldAudio=screen.getByLabelText('回听音频');
 view.rerender(<SegmentEditor {...props} record={{...record,id:'b'}}/>);
 expect(screen.getByLabelText('回听音频')).toHaveAttribute('src','/api/history/b/audio');
 expect(oldAudio).not.toHaveAttribute('src');
});

it('retains playable audio after development StrictMode effect replay',()=>{
 const record={id:'a',audio_available:true,segments:[{id:0,start:0,end:1,text:'稿'}]} as HistoryRecord;
 render(<StrictMode><SegmentEditor record={record} task={null} disabled={false} text={()=>'稿'} edit={vi.fn()} retry={vi.fn()} clear={vi.fn()} apply={vi.fn()}/></StrictMode>);
 expect(screen.getByLabelText('回听音频')).toHaveAttribute('src','/api/history/a/audio');
});
