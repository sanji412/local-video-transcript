import {render,screen} from '@testing-library/react';
import {expect,it} from 'vitest';
import Efficiency from './Efficiency';
it('keeps legacy times unmeasured',()=>{render(<Efficiency value={{}}/>);expect(screen.getByText('模型识别（含加载）：未测量')).toBeVisible();expect(screen.getByText('总处理（不含排队）：未测量')).toBeVisible();expect(screen.queryByText(/倍实时/)).not.toBeInTheDocument();});
it('never claims subtitle extraction invoked the model',()=>{render(<Efficiency value={{recognition_seconds:10,media_duration_seconds:100}} source="subtitle"/>);expect(screen.getByText('模型识别（含加载）：未调用模型（字幕提取）')).toBeVisible();expect(screen.queryByText(/倍实时/)).not.toBeInTheDocument();});
it.each([0,-1,NaN,null])('does not divide by missing or nonpositive model time %s',recognition_seconds=>{render(<Efficiency value={{recognition_seconds,media_duration_seconds:60}}/>);expect(screen.queryByText(/倍实时/)).not.toBeInTheDocument();});
