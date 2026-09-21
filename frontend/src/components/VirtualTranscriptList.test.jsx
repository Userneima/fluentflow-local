// @vitest-environment jsdom

import {fireEvent, render, screen} from '@testing-library/react';
import {describe, expect, it, vi} from 'vitest';
import VirtualTranscriptList from './VirtualTranscriptList.jsx';

vi.mock('@tanstack/react-virtual', () => ({
    useVirtualizer: ({count}) => ({
        getTotalSize: () => count * 66,
        getVirtualItems: () => Array.from({length: Math.min(count, 3)}, (_, index) => ({index, start: index * 66})),
        measureElement: () => {},
        scrollToIndex: vi.fn(),
    }),
}));

const segments = Array.from({length: 5000}, (_, index) => ({
    end: index + 1,
    start: index,
    text: `Segment ${index}`,
}));

describe('VirtualTranscriptList', () => {
    it('renders only the virtual window for a long editable transcript', () => {
        render(<VirtualTranscriptList onSegmentChange={vi.fn()} segments={segments}/>);

        expect(document.querySelectorAll('textarea[data-transcript-segment="true"]')).toHaveLength(3);
        expect(screen.queryByDisplayValue('Segment 4')).toBeNull();
    });

    it('reports edits using the original segment index', () => {
        const onSegmentChange = vi.fn();
        const view = render(<VirtualTranscriptList onSegmentChange={onSegmentChange} segments={segments}/>);
        const secondSegment = view.container.querySelector('[data-index="1"] textarea');

        expect(secondSegment).not.toBeNull();
        fireEvent.change(secondSegment, {target: {value: 'Edited segment'}});
        expect(onSegmentChange).toHaveBeenCalledWith(1, 'Edited segment');
    });
});
