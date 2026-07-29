import {useEffect, useMemo, useState} from 'react';
import {Link, useLocation, useNavigate, useParams} from 'react-router-dom';
import {
    AlertTriangle,
    ArrowLeft,
    CheckCircle2,
    CircleDashed,
    FileText,
    LoaderCircle,
    XCircle,
} from 'lucide-react';
import {API_BASE, apiFetch, localExecutionHeaders, noteModeLabel, useI18n, noteGenerationDiagnosis} from '../app/shared.jsx';
import {useApp} from '../app/AppContext.jsx';
import TaskProgressOverview from '../components/TaskProgressOverview.jsx';
import {normalizeTaskState} from '../lib/taskState.js';

const statusText = (status, lang) => {
    const isZh = lang === 'zh';
    const labels = {
        completed: isZh ? '已完成' : 'Completed',
        running: isZh ? '进行中' : 'Running',
        pending: isZh ? '等待中' : 'Pending',
        failed: isZh ? '失败' : 'Failed',
        skipped: isZh ? '已跳过' : 'Skipped',
        cancelled: isZh ? '已取消' : 'Cancelled',
    };
    return labels[status] || status || (isZh ? '未记录' : 'Not recorded');
};

const statusTone = (status) => {
    if (status === 'failed') return {
        dot: 'border-red-200 bg-red-50 text-red-600 dark:border-red-400/20 dark:bg-red-400/10 dark:text-red-200',
        badge: 'border-red-200 bg-red-50 text-red-700 dark:border-red-400/20 dark:bg-red-400/10 dark:text-red-200',
        Icon: XCircle,
    };
    if (status === 'running') return {
        dot: 'border-blue-200 bg-blue-50 text-blue-600 dark:border-blue-400/20 dark:bg-blue-400/10 dark:text-blue-200',
        badge: 'border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-400/20 dark:bg-blue-400/10 dark:text-blue-200',
        Icon: LoaderCircle,
    };
    if (status === 'pending') return {
        dot: 'border-[#dedada] bg-white text-[#85868c] dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-white/[0.72]',
        badge: 'border-[#dedada] bg-white text-[#85868c] dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-white/[0.72]',
        Icon: CircleDashed,
    };
    if (status === 'skipped' || status === 'cancelled') return {
        dot: 'border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-300/20 dark:bg-amber-300/10 dark:text-amber-100',
        badge: 'border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-300/20 dark:bg-amber-300/10 dark:text-amber-100',
        Icon: AlertTriangle,
    };
    return {
        dot: 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-400/20 dark:bg-emerald-400/10 dark:text-emerald-200',
        badge: 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-400/20 dark:bg-emerald-400/10 dark:text-emerald-200',
        Icon: CheckCircle2,
    };
};

const compactValues = (items, limit = 5) => (
    (Array.isArray(items) ? items : [])
        .map((item) => String(item || '').trim())
        .filter(Boolean)
        .slice(0, limit)
);

const decisionSentence = (entry, lang) => {
    const isZh = lang === 'zh';
    const decision = String(entry?.decision || '').trim();
    if (!decision) return '';
    return isZh ? `${decision}。` : `${decision}.`;
};

const rawValueText = (value) => {
    if (Array.isArray(value)) {
        return value.map((item) => rawValueText(item)).filter(Boolean).join('；');
    }
    if (value && typeof value === 'object') {
        try {
            return JSON.stringify(value, null, 2);
        } catch {
            return String(value);
        }
    }
    return String(value || '').trim();
};

const readableDecisionText = (value, lang) => {
    const isZh = lang === 'zh';
    if (!isZh) return value;
    return String(value || '')
        .replace(/\blocal_whisper\b/g, '本地 faster-whisper')
        .replace(/\bsource_type=video_link\b/g, '来源类型：视频链接')
        .replace(/\bsource_type=video_file\b/g, '来源类型：视频文件')
        .replace(/\bsource_type=transcript_file\b/g, '来源类型：字幕文件')
        .replace(/\bsource_type=audio_file\b/g, '来源类型：音频文件')
        .replace(/\bsource_type=video\b/g, '来源类型：视频')
        .replace(/设置中的转写引擎：\s*local\b/g, '设置中的转写引擎：本地')
        .replace(/转写引擎：\s*local\b/g, '转写引擎：本地')
        .replace(/\bcontent has structured learning markers\b/g, '内容有结构化学习标记');
};

const isMaterialClassificationEntry = (entry) => {
    const id = String(entry?.id || '').toLowerCase();
    const title = String(entry?.title || '').toLowerCase();
    return id === 'material_classification' || title.includes('判断材料类型') || title.includes('material classification');
};

const isCompactOverviewEntry = (entry) => {
    const id = String(entry?.id || '').toLowerCase();
    const title = String(entry?.title || '').toLowerCase();
    return (
        id === 'execution_route'
        || id === 'subtitle_strategy'
        || id === 'note_mode_selection'
        || id === 'note_generation_outcome'
        || title.includes('选择处理路线')
        || title.includes('决定字幕呈现方式')
        || title.includes('选择笔记生成方式')
        || title.includes('判断笔记生成结果')
        || title.includes('execution route')
        || title.includes('subtitle strategy')
        || title.includes('note mode selection')
        || title.includes('note result')
    );
};

const materialClassificationEvidenceLine = (entry, lang) => {
    const isZh = lang === 'zh';
    const rawEvidence = Array.isArray(entry?.evidence) ? entry.evidence : [entry?.evidence];
    const readableEvidence = rawEvidence
        .map((value) => readableDecisionText(rawValueText(value), lang))
        .map((value) => String(value || '').trim())
        .filter(Boolean);
    const structuredMarker = readableEvidence.find((item) => (
        item.includes('内容有结构化学习标记')
        || item.toLowerCase().includes('structured learning markers')
    ));
    if (structuredMarker) {
        return isZh ? '依据：内容有结构化学习标记' : 'Evidence: structured learning markers';
    }
    const usefulEvidence = readableEvidence.find((item) => ![
        '来源',
        '时长',
        '语言判断',
        'source',
        'duration',
        'language',
    ].some((prefix) => item.toLowerCase().startsWith(prefix.toLowerCase())));
    if (!usefulEvidence) return '';
    return isZh ? `依据：${usefulEvidence}` : `Evidence: ${usefulEvidence}`;
};

const decisionLines = (entry, lang) => {
    const isZh = lang === 'zh';
    const decision = decisionSentence(entry, lang);
    if (isMaterialClassificationEntry(entry)) {
        const materialLines = [decision, materialClassificationEvidenceLine(entry, lang)]
            .map((value) => String(value || '').trim())
            .filter(Boolean);
        return materialLines.length ? materialLines : [isZh ? '还没有形成明确结论。' : 'No clear conclusion yet.'];
    }
    const bodyValues = [
        entry.reason,
        entry.evidence,
        entry.impact,
        entry.warnings,
    ]
        .map((value) => readableDecisionText(rawValueText(value), lang))
        .filter(Boolean);
    const lines = [decision, ...bodyValues]
        .map((value) => String(value || '').trim())
        .filter(Boolean);
    const uniqueLines = [];
    lines.forEach((line) => {
        const normalized = line.replace(/[。.;；\s]/g, '');
        if (!normalized) return;
        if (uniqueLines.some((item) => item.replace(/[。.;；\s]/g, '') === normalized)) return;
        uniqueLines.push(line);
    });
    if (uniqueLines.length) return uniqueLines;
    return [isZh ? '还没有形成明确结论。' : 'No clear conclusion yet.'];
};

const chapterCoverageData = (pageData) => {
    const detailCoverage = pageData?.chapter_coverage;
    if (detailCoverage && typeof detailCoverage === 'object') return detailCoverage;
    const packageCoverage = pageData?.note?.chapter_coverage;
    if (packageCoverage && typeof packageCoverage === 'object') return packageCoverage;
    return null;
};

const formatSeconds = (value) => {
    const total = Math.max(0, Math.floor(Number(value) || 0));
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const seconds = total % 60;
    if (hours > 0) return `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
    return `${minutes}:${String(seconds).padStart(2, '0')}`;
};

const rangeText = (item, lang) => {
    const startSeconds = Number(item?.start_seconds);
    const endSeconds = Number(item?.end_seconds);
    if (Number.isFinite(startSeconds) && Number.isFinite(endSeconds) && endSeconds > startSeconds) {
        return `${formatSeconds(startSeconds)}-${formatSeconds(endSeconds)}`;
    }
    const start = Number(item?.char_start);
    const end = Number(item?.char_end);
    if (Number.isFinite(start) && Number.isFinite(end) && end > start) {
        return lang === 'zh' ? `字符 ${start}-${end}` : `Chars ${start}-${end}`;
    }
    const ids = compactValues(item?.source_segment_ids, 3);
    if (ids.length) return ids.join(', ');
    return lang === 'zh' ? '未记录' : 'Not recorded';
};

function fallbackDecisionEntries(packageData, lang) {
    const plan = packageData?.processing_plan || {};
    const material = plan.material || {};
    const note = packageData?.note || {};
    const noteMode = plan.note_strategy?.resolved_mode || note.resolved_mode || plan.note_strategy?.selected_mode || note.requested_mode || 'auto';
    const fallback = [
        {
            id: 'material_classification',
            title: lang === 'zh' ? '判断材料类型' : 'Material classification',
            status: material.type ? 'completed' : 'pending',
            decision: material.type || (lang === 'zh' ? '待判断' : 'Pending'),
            reason: plan.goal?.reason || (lang === 'zh' ? '根据来源、时长和正文线索判断材料用途。' : 'Judged from source, duration, and transcript signals.'),
            evidence: compactValues(material.evidence),
            impact: plan.goal?.reason || (lang === 'zh' ? '影响后续笔记结构和重点保留方式。' : 'Affects note structure and what gets preserved.'),
            source: 'inferred',
            confidence: material.confidence,
        },
        {
            id: 'note_mode_selection',
            title: lang === 'zh' ? '选择笔记生成方式' : 'Note mode selection',
            status: noteMode ? 'completed' : 'pending',
            decision: noteModeLabel(noteMode, lang),
            reason: plan.note_strategy?.reason || (lang === 'zh' ? '按当前自动模式和材料信息选择。' : 'Chosen from automatic mode and material signals.'),
            evidence: compactValues([
                material.duration_seconds ? `${lang === 'zh' ? '时长' : 'Duration'}：${Math.round(Number(material.duration_seconds) / 60)} min` : '',
                plan.note_strategy?.confidence ? `${lang === 'zh' ? '置信度' : 'Confidence'}：${plan.note_strategy.confidence}` : '',
            ]),
            impact: lang === 'zh' ? '影响生成速度、结构完整度和证据覆盖。' : 'Affects speed, structure, and evidence coverage.',
            source: 'inferred',
            confidence: plan.note_strategy?.confidence,
        },
        {
            id: 'note_generation_outcome',
            title: lang === 'zh' ? '判断笔记生成结果' : 'Note result',
            status: note.status || note.diagnosis?.status || 'pending',
            decision: note.diagnosis?.title || (lang === 'zh' ? '等待结果' : 'Waiting for result'),
            reason: note.diagnosis?.detail || '',
            evidence: compactValues([
                note.markdown_chars ? `${lang === 'zh' ? '笔记字数' : 'Note chars'}：${Number(note.markdown_chars).toLocaleString()}` : '',
            ]),
            impact: note.diagnosis?.next_action || '',
            source: 'inferred',
        },
    ];
    return fallback.filter((entry) => entry.title || entry.decision);
}

function rawDecisionEntries(packageData, lang) {
    const entries = packageData?.decision_log?.entries;
    return Array.isArray(entries) && entries.length ? entries : fallbackDecisionEntries(packageData, lang);
}

function decisionEntries(packageData, lang) {
    return rawDecisionEntries(packageData, lang)
        .filter((entry) => !isCompactOverviewEntry(entry) && !isMaterialClassificationEntry(entry));
}

function materialDecisionEntry(packageData, lang) {
    return rawDecisionEntries(packageData, lang).find(isMaterialClassificationEntry) || null;
}

const trimSentencePunctuation = (value) => (
    String(value || '').trim().replace(/[。.;；]+$/g, '')
);

const materialJudgmentValue = (entry, lang) => {
    if (!entry) return '';
    const decision = trimSentencePunctuation(decisionSentence(entry, lang));
    const evidence = materialClassificationEvidenceLine(entry, lang);
    return [decision, evidence].filter(Boolean).join(' · ');
};

const DecisionEntry = ({entry, index, isLast, lang}) => {
    const tone = statusTone(entry.status);
    const Icon = tone.Icon;
    const lines = decisionLines(entry, lang);
    return (
        <article className="grid grid-cols-[42px_minmax(0,1fr)] gap-3">
            <div className="relative flex justify-center">
                {!isLast && <div className="absolute top-11 h-[calc(100%-1rem)] w-px bg-[#dedada] dark:bg-white/[0.12]"/>}
                <div className={`relative z-10 flex size-9 items-center justify-center rounded-[13px] border ${tone.dot}`}>
                    <Icon className={`size-[18px] ${entry.status === 'running' ? 'animate-spin' : ''}`} strokeWidth={2.15}/>
                </div>
            </div>
            <div className="min-w-0 rounded-[18px] border border-[#dedada] bg-white px-4 py-3.5 shadow-[0_18px_44px_-38px_rgba(17,17,17,.42)] dark:border-white/[0.14] dark:bg-white/[0.07] dark:shadow-none">
                <div className="flex flex-wrap items-center gap-2">
                    <p className="text-[12px] font-extrabold text-[#676970] dark:text-white/[0.76]">
                        {entry.title || (lang === 'zh' ? `原始判断 ${index + 1}` : `Raw decision ${index + 1}`)}
                    </p>
                    {entry.status && entry.status !== 'completed' && (
                        <span className={`rounded-full border px-2 py-0.5 text-[10px] font-extrabold ${tone.badge}`}>
                            {statusText(entry.status, lang)}
                        </span>
                    )}
                </div>
                <div className="mt-2 space-y-1.5">
                    {lines.slice(0, 3).map((line, lineIndex) => (
                        <p
                            key={`${entry.id || index}-${lineIndex}`}
                            className={`${lineIndex === 0 ? 'text-[16px] font-extrabold text-[#111111] dark:text-white' : 'text-[13px] font-semibold text-[#3f4148] dark:text-white/[0.80]'} leading-6 whitespace-pre-wrap break-words`}
                        >
                            {line}
                        </p>
                    ))}
                </div>
            </div>
        </article>
    );
};

const StatBlock = ({label, value}) => (
    <div className="rounded-[14px] border border-[#dedada] bg-[#fbfbfb] p-3 dark:border-white/[0.10] dark:bg-white/[0.04]">
        <p className="text-[11px] font-extrabold text-[#85868c] dark:text-white/[0.72]">{label}</p>
        <p className="mt-1 truncate text-[14px] font-extrabold text-[#111111] dark:text-white">{value}</p>
    </div>
);

const ChapterCoverageEvidence = ({coverage, lang}) => {
    if (!coverage) return null;
    const isZh = lang === 'zh';
    const summary = coverage.summary || {};
    const chapters = Array.isArray(coverage.chapters) ? coverage.chapters : [];
    const evidence = Array.isArray(coverage.evidence) ? coverage.evidence : [];
    if (!evidence.length && !chapters.length) return null;
    const visibleEvidence = evidence.slice(0, 12);
    const hiddenCount = Math.max(evidence.length - visibleEvidence.length, 0);
    return (
        <section className="border-y border-[#dedada] py-5 dark:border-white/[0.10]">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
                <div>
                    <p className="text-[12px] font-extrabold text-[#85868c] dark:text-white/[0.72]">
                        {isZh ? 'Chapter Coverage 证据表' : 'Chapter coverage evidence'}
                    </p>
                    <h2 className="font-headline text-[20px] font-extrabold text-[#111111] dark:text-white">
                        {isZh ? '这份笔记覆盖了哪些原文证据' : 'Source evidence behind this note'}
                    </h2>
                </div>
                <div className="grid grid-cols-3 gap-2 text-[12px] sm:min-w-[26rem]">
                    <StatBlock label={isZh ? '证据' : 'Evidence'} value={summary.evidence_count ?? evidence.length}/>
                    <StatBlock label={isZh ? '章节' : 'Chapters'} value={summary.chapter_count ?? chapters.length}/>
                    <StatBlock
                        label={isZh ? '重点覆盖' : 'Important'}
                        value={`${summary.covered_important_evidence_count ?? '-'} / ${summary.important_evidence_count ?? '-'}`}
                    />
                </div>
            </div>

            {chapters.length > 0 && (
                <div className="mt-4 flex flex-wrap gap-2">
                    {chapters.map((chapter) => (
                        <span key={chapter.chapter_id || chapter.title} className="rounded-full border border-[#dedada] bg-white px-3 py-1 text-[12px] font-bold text-[#57585d] dark:border-white/[0.10] dark:bg-white/[0.055] dark:text-white/[0.76]">
                            {chapter.order ? `${chapter.order}. ` : ''}{chapter.title || chapter.chapter_id}
                            <span className="ml-1 text-[#85868c] dark:text-white/[0.66]">({chapter.evidence_count ?? 0})</span>
                        </span>
                    ))}
                </div>
            )}

            {visibleEvidence.length > 0 && (
                <div className="mt-4 overflow-x-auto rounded-[16px] border border-[#dedada] dark:border-white/[0.10]">
                    <div className="grid min-w-[46rem] grid-cols-[5rem_7rem_minmax(0,1fr)_9rem] border-b border-[#dedada] bg-[#fbfbfb] px-3 py-2 text-[11px] font-extrabold text-[#85868c] dark:border-white/[0.10] dark:bg-white/[0.035] dark:text-white/[0.72]">
                        <span>ID</span>
                        <span>{isZh ? '重要性' : 'Weight'}</span>
                        <span>{isZh ? '证据内容' : 'Evidence'}</span>
                        <span>{isZh ? '来源' : 'Source'}</span>
                    </div>
                    <div className="min-w-[46rem] divide-y divide-[#dedada] bg-white dark:divide-white/[0.08] dark:bg-white/[0.035]">
                        {visibleEvidence.map((item) => (
                            <div key={item.evidence_id} className="grid grid-cols-[5rem_7rem_minmax(0,1fr)_9rem] gap-0 px-3 py-3 text-[12px] leading-5">
                                <span className="font-extrabold text-[#111111] dark:text-white">{item.evidence_id}</span>
                                <span className="font-bold text-[#676970] dark:text-white/[0.76]">
                                    {item.importance ?? '-'} / 5
                                    {item.covered === false && <span className="ml-1 text-amber-700 dark:text-amber-200">{isZh ? '待补' : 'open'}</span>}
                                </span>
                                <span className="min-w-0 pr-4 font-semibold text-[#2f3035] dark:text-white/[0.76]">
                                    {item.text}
                                    {Array.isArray(item.keywords) && item.keywords.length > 0 && (
                                        <span className="ml-2 text-[#85868c] dark:text-white/[0.68]">
                                            {item.keywords.slice(0, 3).join(' / ')}
                                        </span>
                                    )}
                                    {Array.isArray(item.covered_by_chapter_ids) && item.covered_by_chapter_ids.length > 0 && (
                                        <span className="ml-2 text-[#85868c] dark:text-white/[0.68]">
                                            {isZh ? '章节' : 'Chapter'} {item.covered_by_chapter_ids.slice(0, 3).join(', ')}
                                        </span>
                                    )}
                                </span>
                                <span className="font-bold text-[#676970] dark:text-white/[0.74]">{rangeText(item, lang)}</span>
                            </div>
                        ))}
                    </div>
                </div>
            )}
            {hiddenCount > 0 && (
                <p className="mt-2 text-[12px] font-bold text-[#85868c] dark:text-white/[0.72]">
                    {isZh ? `还有 ${hiddenCount} 条证据未在此处展开。` : `${hiddenCount} more evidence rows hidden here.`}
                </p>
            )}
        </section>
    );
};

const taskIdForJob = (job) => String(job?.task_id || job?.result?.task_id || '').trim();

const liveStageRank = {
    queued: 0,
    resolving: 1,
    downloading: 2,
    saving: 3,
    audio: 4,
    stt: 5,
    transcript_ready: 6,
    summary: 7,
    export: 8,
    done: 9,
    completed: 9,
};

const bytesToMb = (value) => {
    const n = Number(value);
    return Number.isFinite(n) && n > 0 ? n / 1024 / 1024 : null;
};

const videoSourceProgressFromJob = (job) => {
    const progress = job?.metadata?.video_source_progress;
    if (!progress || typeof progress !== 'object') return null;
    const loaded = Number(progress.loaded_bytes);
    const total = Number(progress.total_bytes);
    const hasLoaded = Number.isFinite(loaded) && loaded > 0;
    const hasTotal = Number.isFinite(total) && total > 0;
    const percent = hasLoaded && hasTotal ? Math.max(0, Math.min(100, loaded / total * 100)) : null;
    const jobProgress = Number(job?.progress);
    const mappedProgress = Number.isFinite(jobProgress) && jobProgress > 0
        ? jobProgress
        : percent == null
            ? null
            : 10 + percent * 0.45;
    if (!progress.message && !hasLoaded && !hasTotal && mappedProgress == null) return null;
    return {
        ...progress,
        loaded_bytes: Number.isFinite(loaded) ? loaded : progress.loaded_bytes,
        total_bytes: Number.isFinite(total) ? total : progress.total_bytes,
        stage: progress.stage || job?.stage || 'downloading',
        progress: mappedProgress,
    };
};

const snapshotStepToStage = (step) => ({
    source_fetch: 'downloading',
    subtitle_parse: 'transcript_parse',
    audio_prepare: 'audio',
    transcription: 'stt',
    subtitle_prepare: 'transcript_ready',
    note_generation: 'summary',
    result_save: 'done',
    feishu_export: 'export',
}[String(step || '').trim()] || '');

const mergeLiveSnapshotPageData = (nextData, currentData) => {
    const mergedData = {
        ...nextData,
        note: nextData?.note || currentData?.note || null,
        processing_plan: nextData?.processing_plan || currentData?.processing_plan || null,
    };
    if (!currentData?.data_quality?.cached_snapshot) return mergedData;
    const currentTask = currentData.task || {};
    const nextTask = nextData?.task || {};
    const currentStage = String(currentTask.stage || '').toLowerCase();
    const nextStage = String(nextTask.stage || '').toLowerCase();
    const currentProgress = Number(currentTask.progress) || 0;
    const nextProgress = Number(nextTask.progress) || 0;
    const currentIsLive = ['running', 'queued'].includes(String(currentTask.status || '').toLowerCase())
        || ['queued', 'resolving', 'downloading', 'saving', 'audio', 'stt', 'summary', 'export'].includes(currentStage);
    const nextIsTerminal = ['completed', 'failed', 'cancelled'].includes(String(nextTask.status || nextTask.stage || '').toLowerCase())
        || ['done', 'failed', 'cancelled'].includes(nextStage);
    const currentIsAhead = (liveStageRank[currentStage] ?? 0) > (liveStageRank[nextStage] ?? 0) || currentProgress > nextProgress;
    if (!currentIsLive || nextIsTerminal || !currentIsAhead) return mergedData;
    return {
        ...mergedData,
        task: {
            ...nextTask,
            status: currentTask.status,
            stage: currentTask.stage,
            progress: currentTask.progress,
            file_size_mb: nextTask.file_size_mb ?? currentTask.file_size_mb,
            duration_seconds: nextTask.duration_seconds ?? currentTask.duration_seconds,
        },
        source: {
            ...(mergedData?.source || {}),
            video_source_progress: currentData.source?.video_source_progress || nextData?.source?.video_source_progress || null,
            file_size_mb: nextData?.source?.file_size_mb ?? currentData.source?.file_size_mb,
            duration_seconds: nextData?.source?.duration_seconds ?? currentData.source?.duration_seconds,
        },
        data_quality: {
            ...(mergedData?.data_quality || {}),
            cached_snapshot: currentData.data_quality?.cached_snapshot || nextData?.data_quality?.cached_snapshot || false,
            used_cached_live_overlay: true,
        },
    };
};

const pageDataFromJobSnapshot = (job, fallbackTaskId, lang) => {
    if (!job || typeof job !== 'object') return null;
    const taskId = taskIdForJob(job) || fallbackTaskId;
    if (!taskId) return null;
    const snapshot = job.task_snapshot && typeof job.task_snapshot === 'object' ? job.task_snapshot : {};
    const result = job.result && typeof job.result === 'object' ? job.result : {};
    const metadata = job.metadata && typeof job.metadata === 'object' ? job.metadata : {};
    const videoSource = metadata.video_source && typeof metadata.video_source === 'object' ? metadata.video_source : {};
    const videoProgress = videoSourceProgressFromJob(job);
    const progressValue = Number(job.progress);
    const fileSizeMb = job.source_file_size_mb || bytesToMb(videoProgress?.total_bytes) || null;
    const title = (
        metadata.display_title
        || result.display_title
        || videoSource.display_title
        || videoSource.title
        || result.raw_title
        || job.source_filename
        || result.filename
        || taskId
    );
    const status = videoProgress ? 'running' : (snapshot.overall_status || normalizeTaskState(job));
    const snapshotStage = snapshotStepToStage(snapshot.current_step);
    const legacyStage = String(job.stage || '').trim();
    const effectiveLegacyStage = legacyStage && !['queued', 'idle'].includes(legacyStage) ? legacyStage : '';
    const stage = videoProgress
        ? (job.stage && job.stage !== 'queued' ? job.stage : 'downloading')
        : (effectiveLegacyStage || snapshotStage || legacyStage || (status === 'completed' ? 'done' : status === 'failed' ? 'failed' : 'queued'));
    const generatedDiagnosis = noteGenerationDiagnosis(result, lang);
    const diagnosis = snapshot.failure_reason ? {
        ...generatedDiagnosis,
        status: status === 'failed' ? 'failed' : 'warning',
        severity: status === 'failed' ? 'error' : 'warning',
        title: status === 'failed'
            ? (lang === 'zh' ? '任务处理失败' : 'Task failed')
            : (lang === 'zh' ? '需要复查' : 'Review needed'),
        detail: snapshot.failure_reason,
        nextAction: snapshot.next_action || '',
        next_action: snapshot.next_action || '',
        canRegenerate: true,
        retryable: true,
    } : generatedDiagnosis;
    const noteStatus = result.summary_skipped
        ? 'skipped'
        : result.summary_markdown
            ? 'completed'
            : result.summary_status || diagnosis.status || 'pending';
    const decisionLog = job.decision_log && typeof job.decision_log === 'object'
        ? job.decision_log
        : result.decision_log && typeof result.decision_log === 'object'
            ? result.decision_log
            : {entries: []};
    return {
        ok: true,
        cached: true,
        task: {
            task_id: taskId,
            status,
            stage,
            progress: Number.isFinite(Number(snapshot.progress))
                ? Number(snapshot.progress)
                : Number.isFinite(progressValue) && progressValue > 0
                ? progressValue
                : videoProgress?.progress ?? (status === 'completed' ? 100 : 0),
            source_type: job.source_type || result.source || null,
            title,
            filename: result.filename || job.source_filename || null,
            file_size_mb: fileSizeMb,
            duration_seconds: result.audio_duration_seconds || job.source_duration_seconds || metadata.duration_seconds || null,
            created_at: job.created_at || null,
            updated_at: job.updated_at || null,
            video_source_progress: videoProgress,
        },
        task_snapshot: Object.keys(snapshot).length ? snapshot : null,
        timeline: Array.isArray(snapshot.steps) ? snapshot.steps : [],
        title,
        source: {
            type: job.source_type || result.source || null,
            filename: result.filename || job.source_filename || null,
            raw_title: result.raw_title || metadata.raw_title || videoSource.raw_title || null,
            display_title: title,
            url: videoSource.url || videoSource.webpage_url || null,
            duration_seconds: result.audio_duration_seconds || job.source_duration_seconds || metadata.duration_seconds || null,
            file_size_mb: fileSizeMb,
            video_source: Object.keys(videoSource).length ? videoSource : null,
            video_source_progress: videoProgress,
        },
        transcript: {
            available: !!(
                String(result.transcript_text || result.transcript_text_preview || '').trim()
                || (Array.isArray(result.raw_segments) && result.raw_segments.length)
                || (Array.isArray(result.display_segments) && result.display_segments.length)
            ),
            text: result.transcript_text || '',
            preview: result.transcript_text_preview || result.transcript_text || '',
            raw_segments: Array.isArray(result.raw_segments) ? result.raw_segments : [],
            display_segments: Array.isArray(result.display_segments) ? result.display_segments : [],
            corrected_text: result.corrected_transcript_text || '',
            corrected_segments: Array.isArray(result.corrected_segments) ? result.corrected_segments : [],
            corrections: Array.isArray(result.transcript_corrections) ? result.transcript_corrections : [],
            correction: result.transcript_correction && typeof result.transcript_correction === 'object'
                ? result.transcript_correction
                : (result.transcript_correction_status ? {status: result.transcript_correction_status} : null),
            note_input_source: result.note_generation_transcript_source || 'transcript_text',
            source_language: result.source_language || null,
            detected_language: result.detected_language || null,
            subtitle_mode: result.subtitle_mode || null,
            translation_status: result.translation_status || null,
            stt_provider: metadata.stt_provider || result.stt_provider || null,
        },
        note: {
            status: noteStatus,
            markdown: result.summary_markdown || '',
            markdown_chars: String(result.summary_markdown || '').length,
            diagnosis: {
                ...diagnosis,
                next_action: diagnosis.nextAction || '',
                retryable: !!diagnosis.canRegenerate,
            },
            requested_mode: result.requested_note_mode || null,
            resolved_mode: result.resolved_note_mode || null,
            prompt_preset: result.prompt_preset || null,
            prompt_preset_label: result.prompt_preset_label || null,
            chapter_coverage: result.chapter_coverage || null,
        },
        processing_plan: result.processing_plan || null,
        tool_trace: result.tool_trace || null,
        decision_log: decisionLog,
        diagnosis: {
            ...diagnosis,
            next_action: diagnosis.nextAction || '',
            retryable: !!diagnosis.canRegenerate,
        },
        actions: Array.isArray(snapshot.actions) && snapshot.actions.length ? snapshot.actions : (result.summary_markdown ? [{
            id: 'open_result',
            label: lang === 'zh' ? '打开结果' : 'Open result',
            method: 'GET',
            path: `/jobs/${taskId}`,
            enabled: true,
            tone: 'primary',
        }] : []),
        artifacts: snapshot.artifacts || result.artifacts || null,
        chapter_coverage: result.chapter_coverage || null,
        data_quality: {
            ...(snapshot.data_quality || {}),
            cached_snapshot: true,
        },
    };
};

const AgentTrace = () => {
    const {taskId} = useParams();
    const navigate = useNavigate();
    const location = useLocation();
    const {lang} = useI18n();
    const {currentJob, setLastResult} = useApp();
    const initialJob = location.state?.job && taskIdForJob(location.state.job) === taskId ? location.state.job : null;
    const initialPageData = pageDataFromJobSnapshot(initialJob, taskId, lang);
    const [loading, setLoading] = useState(!initialPageData);
    const [error, setError] = useState(null);
    const [pageData, setPageData] = useState(() => initialPageData);
    const [openingResult, setOpeningResult] = useState(false);
    const isZh = lang === 'zh';

    const readJson = async (path, options={}) => {
        const response = await apiFetch(`${API_BASE}${path}`, {headers: localExecutionHeaders(options)});
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            const exc = new Error(data?.detail || `HTTP ${response.status}`);
            exc.status = response.status;
            throw exc;
        }
        return data;
    };

    const readJsonWithLocalFallback = async (path) => {
        const currentJobOptions = currentJob?.taskId === taskId ? {sttProvider: currentJob.sttProvider} : {};
        const currentIsLocal = !!localExecutionHeaders(currentJobOptions)['X-FluentFlow-Execution-Target'];
        try {
            return await readJson(path, currentJobOptions);
        } catch (exc) {
            if (exc.status !== 404 || currentIsLocal) throw exc;
            return await readJson(path, {localExecution: true});
        }
    };

    const loadTaskDetail = async (staleRef={current:false}, {silent=false} = {}) => {
        if (!silent) setLoading(true);
        setError(null);
        try {
            const detailData = await readJsonWithLocalFallback(`/jobs/${encodeURIComponent(taskId)}/detail`);
            if (!staleRef.current) {
                setPageData((current) => silent
                    ? mergeLiveSnapshotPageData(detailData, current || initialPageData)
                    : detailData);
            }
        } catch (detailError) {
            try {
                const packageData = await readJsonWithLocalFallback(`/agent/v1/tasks/${encodeURIComponent(taskId)}/package`);
                if (!staleRef.current) {
                    setPageData((current) => silent
                        ? mergeLiveSnapshotPageData(packageData, current || initialPageData)
                        : packageData);
                }
            } catch (packageError) {
                if (!staleRef.current && !silent && !pageData) setError(packageError.message || detailError.message || String(packageError));
            }
        } finally {
            if (!staleRef.current) setLoading(false);
        }
    };

    useEffect(() => {
        const staleRef = {current: false};
        const seededPageData = pageDataFromJobSnapshot(initialJob, taskId, lang);
        if (seededPageData) {
            setPageData(seededPageData);
            setLoading(false);
        } else {
            setPageData(null);
        }
        loadTaskDetail(staleRef, {silent: !!seededPageData});
        return () => { staleRef.current = true; };
    }, [taskId]);

    useEffect(() => {
        const taskStatus = String(pageData?.task?.status || '').toLowerCase();
        const taskStage = String(pageData?.task?.stage || '').toLowerCase();
        if (!['queued', 'running'].includes(taskStatus) && !['queued', 'resolving', 'downloading', 'saving', 'audio', 'stt', 'summary', 'export'].includes(taskStage)) return undefined;
        const staleRef = {current: false};
        const timer = setInterval(() => loadTaskDetail(staleRef, {silent: true}), 3000);
        return () => {
            staleRef.current = true;
            clearInterval(timer);
        };
    }, [pageData?.task?.status, pageData?.task?.stage, taskId]);

    const title = pageData?.task?.title || pageData?.title || taskId;
    const decisions = useMemo(() => decisionEntries(pageData, lang), [pageData, lang]);
    const materialEntry = useMemo(() => materialDecisionEntry(pageData, lang), [pageData, lang]);
    const chapterCoverage = useMemo(() => chapterCoverageData(pageData), [pageData]);
    const canOpenResult = Boolean(
        pageData?.actions?.some((action) => action?.id === 'open_result' && action?.enabled !== false)
        || pageData?.transcript?.available
        || String(pageData?.note?.markdown || '').trim()
    );

    const openResult = async () => {
        if (!canOpenResult || openingResult) return;
        setOpeningResult(true);
        setError(null);
        try {
            const job = await readJsonWithLocalFallback(`/jobs/${encodeURIComponent(taskId)}`);
            if (job?.result) {
                setLastResult(job.result);
                navigate('/editor');
                return;
            }
            setError(isZh ? '这条记录暂时没有可打开的结果。请返回处理记录刷新后再试。' : 'This record does not have an openable result yet. Go back to records, refresh, and try again.');
        } catch (exc) {
            setError(exc.message || String(exc));
        } finally {
            setOpeningResult(false);
        }
    };

    if (loading) {
        return (
            <main className="ml-[var(--sidebar-offset)] flex min-h-dvh flex-1 items-center justify-center bg-[#f8f7fb] p-8 transition-[margin] duration-200 ease-out dark:bg-[#101010]">
                <div className="flex items-center gap-2.5 text-sm font-semibold text-on-surface-variant">
                    <LoaderCircle className="size-4 animate-spin" strokeWidth={2.15}/>
                    {isZh ? '加载判断推进...' : 'Loading decision flow...'}
                </div>
            </main>
        );
    }

    if (error) {
        return (
            <main className="ml-[var(--sidebar-offset)] flex min-h-dvh flex-1 flex-col items-center justify-center gap-4 bg-[#f8f7fb] p-8 transition-[margin] duration-200 ease-out dark:bg-[#101010]">
                <div className="rounded-[16px] border border-red-200 bg-red-50 px-4 py-3 text-sm font-semibold text-red-700 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-300">{error}</div>
                <button type="button" onClick={() => navigate('/agent')} className="inline-flex h-9 items-center gap-2 rounded-[12px] border border-[#dedada] bg-white px-4 text-sm font-bold text-[#111111] transition hover:bg-[#efeeee] dark:border-white/[0.12] dark:bg-white/[0.08] dark:text-white dark:hover:bg-white/[0.12]">
                    <ArrowLeft className="size-4" strokeWidth={2.15}/>
                    {isZh ? '返回处理记录' : 'Back to records'}
                </button>
            </main>
        );
    }

    return (
        <main className="ml-[var(--sidebar-offset)] h-dvh flex-1 overflow-y-auto bg-[#f8f7fb] px-6 py-5 text-[#111111] transition-[margin] duration-200 ease-out hide-scrollbar dark:bg-[#101010] dark:text-white/[0.92] lg:px-10">
            <div className="mx-auto max-w-7xl space-y-5">
                <header className="flex flex-col gap-3 border-b border-[#dedada] pb-4 dark:border-white/[0.10] lg:flex-row lg:items-center lg:justify-between">
                    <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                            <Link to="/agent" className="inline-flex items-center gap-1.5 text-[13px] font-bold text-[#676970] transition hover:text-[#111111] dark:text-white/[0.74] dark:hover:text-white">
                                <ArrowLeft className="size-3.5" strokeWidth={2.15}/>
                                {isZh ? '处理记录' : 'Processing records'}
                            </Link>
                            <span className="text-[#a2a3a8] dark:text-white/[0.55]">/</span>
                            <h1 className="font-headline text-[22px] font-extrabold leading-tight text-[#111111] dark:text-white">
                                {isZh ? '处理详情' : 'Task details'}
                            </h1>
                        </div>
                        <p className="mt-1 max-w-[72ch] truncate text-[13px] leading-5 text-[#676970] dark:text-white/[0.74]" title={title}>
                            {title}
                        </p>
                    </div>
                    {canOpenResult && (
                        <div className="flex flex-wrap gap-2 lg:justify-end">
                            <button type="button" onClick={openResult} disabled={openingResult} className="inline-flex h-9 items-center gap-2 rounded-[12px] bg-[#111111] px-4 text-[13px] font-extrabold text-white transition hover:bg-[#2a2a2a] disabled:cursor-not-allowed disabled:opacity-55 dark:bg-white dark:text-[#111111] dark:hover:bg-white/[0.88]">
                                {openingResult ? <LoaderCircle className="size-4 animate-spin" strokeWidth={2.15}/> : <FileText className="size-4" strokeWidth={2.15}/>}
                                {openingResult ? (isZh ? '打开中...' : 'Opening...') : (isZh ? '查看结果' : 'View result')}
                            </button>
                        </div>
                    )}
                </header>

                <TaskProgressOverview pageData={pageData} materialJudgment={materialJudgmentValue(materialEntry, lang)}/>

                <ChapterCoverageEvidence coverage={chapterCoverage} lang={lang}/>

                {decisions.length > 0 && (
                <div className="min-w-0">
                    <section className="min-w-0 space-y-4">
                        <div>
                            <p className="text-[12px] font-extrabold text-[#676970] dark:text-white/[0.76]">
                                {isZh ? '判断记录' : 'Decision records'}
                            </p>
                            <h2 className="font-headline text-[18px] font-extrabold text-[#111111] dark:text-white">
                                {isZh ? '原始判断字段' : 'Raw decision fields'}
                            </h2>
                        </div>
                        <div className="space-y-3">
                            {decisions.map((entry, index) => (
                                <DecisionEntry
                                    key={entry.id || `${entry.title}-${index}`}
                                    entry={entry}
                                    index={index}
                                    isLast={index === decisions.length - 1}
                                    lang={lang}
                                />
                            ))}
                        </div>
                    </section>
                </div>
                )}
            </div>
        </main>
    );
};

export default AgentTrace;
