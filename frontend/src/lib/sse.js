// Reading a job's SSE stream: shared by media processing and note
// regeneration so both report progress the same way and neither has to
// re-derive the framing.
//
// The server speaks the `/process` vocabulary: `stage: 'done'` carries the
// result, `stage: 'error'` carries a message, anything else is progress.
export const readSseResult = async (response, onProgress) => {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let result = null;
    while (true) {
        const {value, done} = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, {stream: true});
        const parts = buffer.split('\n\n');
        buffer = parts.pop() || '';
        for (const part of parts) {
            const line = part.split('\n').find((l) => l.startsWith('data: '));
            if (!line) continue;
            let data;
            try {
                data = JSON.parse(line.slice(6));
            } catch (_) {
                // A half-written frame is not a protocol error; the next read
                // completes it. Only real stage errors should reach the caller.
                continue;
            }
            if (data.stage === 'done') {
                result = data.result;
                onProgress?.({stage: 'done', progress: 100, result: data.result});
            } else if (data.stage === 'transcript_ready') {
                onProgress?.({stage: 'transcript_ready', progress: data.progress || 60, result: data.result});
            } else if (data.stage === 'error') {
                // Tagged so a caller can tell "the server rejected this" from
                // "the connection went away" — only the latter is worth
                // re-attaching for.
                const failure = new Error(data.error || 'Processing failed');
                failure.serverStage = true;
                throw failure;
            } else {
                onProgress?.(data);
            }
        }
    }
    if (!result) throw new Error('No result received from server');
    return result;
};
