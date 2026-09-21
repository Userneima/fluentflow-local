import {useRef} from 'react';
import {nudgeSplitRatio, splitRatioFromPointer} from '../lib/editorLayoutPrefs.js';

// Vertical divider between the transcript and note panes. The ratio itself
// lives in the editor page because both panes read it; this component only
// turns pointer and keyboard input into ratio changes, and occupies exactly the
// gap the two panes used to be separated by.
const EditorSplitHandle = ({containerRef, ratio, defaultRatio = 0.5, onChange, onCommit, onReset, lang}) => {
    const draggingRef = useRef(false);

    const applyFromClientX = (clientX) => {
        const next = splitRatioFromPointer(clientX, containerRef.current?.getBoundingClientRect());
        if (next !== null) onChange(next);
    };

    const label = lang === 'zh'
        ? '拖动调整左右宽度，双击恢复默认'
        : 'Drag to resize the panes, double-click to reset';

    const handleKeyDown = (event) => {
        const direction = event.key === 'ArrowLeft' ? -1 : event.key === 'ArrowRight' ? 1 : 0;
        if (!direction) return;
        event.preventDefault();
        const next = nudgeSplitRatio(ratio, direction, defaultRatio);
        if (next === null) return;
        onChange(next);
        onCommit(next);
    };

    return (
        <div
            role="separator"
            aria-orientation="vertical"
            aria-label={label}
            title={label}
            tabIndex={0}
            onPointerDown={(event) => {
                // Without this the browser starts a text selection drag across
                // both panes instead of resizing.
                event.preventDefault();
                draggingRef.current = true;
                event.currentTarget.setPointerCapture?.(event.pointerId);
                applyFromClientX(event.clientX);
            }}
            onPointerMove={(event) => {
                if (draggingRef.current) applyFromClientX(event.clientX);
            }}
            onPointerUp={(event) => {
                if (!draggingRef.current) return;
                draggingRef.current = false;
                event.currentTarget.releasePointerCapture?.(event.pointerId);
                onCommit();
            }}
            onPointerCancel={() => { draggingRef.current = false; }}
            onDoubleClick={onReset}
            onKeyDown={handleKeyDown}
            className="group/split flex w-4 shrink-0 cursor-col-resize touch-none select-none items-center justify-center focus-visible:outline-none"
        >
            <span className="h-16 w-1 rounded-full bg-[#e4e0e0] transition-colors group-hover/split:bg-primary/60 group-focus-visible/split:bg-primary dark:bg-white/[0.16] dark:group-hover/split:bg-primary/70"/>
        </div>
    );
};

export default EditorSplitHandle;
