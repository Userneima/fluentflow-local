import {useMemo, useState} from 'react';
import {ClipboardCopy, KeyRound, Rocket, Terminal} from 'lucide-react';

const ACCESS_TOKEN_KEY = 'fluentflow_access_token';

const copyText = async (text, onDone) => {
    try {
        await navigator.clipboard?.writeText(text);
        onDone(true);
        window.setTimeout(() => onDone(false), 1400);
    } catch (_) {
        onDone(false);
    }
};

const CodeBlock = ({label, value, copied, onCopy}) => (
    <div className="rounded-[14px] border border-[#e5e5e5] bg-white p-3 dark:border-white/[0.12] dark:bg-white/[0.05]">
        <div className="mb-2 flex items-center justify-between gap-3">
            <span className="text-[11px] font-extrabold uppercase tracking-[0.08em] text-[#85868c] dark:text-white/50">{label}</span>
            <button type="button" onClick={() => onCopy(value)} className="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-[10px] border border-[#dedada] px-2.5 text-[12px] font-extrabold text-[#111111] transition hover:bg-[#efeeee] dark:border-white/[0.14] dark:text-white dark:hover:bg-white/[0.08]">
                <ClipboardCopy className="size-3.5" strokeWidth={2.1}/>{copied ? '已复制' : '复制'}
            </button>
        </div>
        <pre className="max-h-60 overflow-auto whitespace-pre-wrap break-words font-mono text-[12px] leading-5 text-[#111111] dark:text-white">{value}</pre>
    </div>
);

const StepCard = ({number, icon: Icon, title, body}) => (
    <div className="relative rounded-[18px] border border-[#e5e5e5] bg-white p-5 shadow-[0_1px_2px_rgba(17,17,17,0.03)] dark:border-white/[0.12] dark:bg-white/[0.05]">
        <span className="absolute right-5 top-4 text-[24px] font-extrabold leading-none text-[#c5cde0] dark:text-white/20">{number}</span>
        <Icon className="mb-5 size-8 text-primary" strokeWidth={2.1}/>
        <h2 className="pr-8 text-[19px] font-extrabold leading-6 text-[#111111] dark:text-white">{title}</h2>
        <p className="mt-3 text-[14px] font-semibold leading-6 text-[#686a70] dark:text-white/60">{body}</p>
    </div>
);

const LocalAgentAccessPanel = ({compact = false, onClose = null}) => {
    const [token, setToken] = useState(() => localStorage.getItem(ACCESS_TOKEN_KEY) || '');
    const [copied, setCopied] = useState('');
    const apiBase = typeof window === 'undefined' ? 'http://127.0.0.1:8000' : window.location.origin;
    const config = useMemo(() => JSON.stringify({
        mcpServers: {
            fluentflow: {
                command: 'python3',
                args: ['<path-to-fluentflow>/scripts/fluentflow_mcp_server.py'],
                env: {
                    FLUENTFLOW_API_BASE: apiBase,
                    FLUENTFLOW_CLIENT_ID: 'local-client',
                    FLUENTFLOW_ACCESS_TOKEN: token || '<your-local-access-token>',
                },
            },
        },
    }, null, 2), [apiBase, token]);
    const codex = `codex mcp add fluentflow --env FLUENTFLOW_API_BASE=${apiBase} --env FLUENTFLOW_CLIENT_ID=local-client --env FLUENTFLOW_ACCESS_TOKEN=${token || '<your-local-access-token>'} -- python3 "$(pwd)/scripts/fluentflow_mcp_server.py"`;
    const saveToken = (value) => {
        setToken(value);
        if (value.trim()) localStorage.setItem(ACCESS_TOKEN_KEY, value.trim());
        else localStorage.removeItem(ACCESS_TOKEN_KEY);
    };
    const onCopy = (key) => (value) => copyText(value, (ok) => setCopied(ok ? key : ''));

    const content = (
        <>
            <div className="grid gap-4 md:grid-cols-3">
                <StepCard number="1" icon={KeyRound} title="填写本机访问令牌" body="令牌由启动 FluentFlow Local 时的 FLUENTFLOW_ACCESS_TOKEN 配置，不会上传到任何云端服务。"/>
                <StepCard number="2" icon={ClipboardCopy} title="复制 MCP 配置" body="把下方配置加入你的 AI 工具；令牌只保存在当前浏览器，方便复制。"/>
                <StepCard number="3" icon={Rocket} title="直接交给 AI" body="例如：“用 FluentFlow 把这个视频做成笔记：&lt;链接&gt;”。"/>
            </div>
            <div className="mt-5 rounded-[14px] border border-[#e5e5e5] bg-white p-4 dark:border-white/[0.12] dark:bg-white/[0.05]">
                <label className="mb-2 block text-[14px] font-extrabold text-[#111111] dark:text-white" htmlFor="local-agent-access-token">本机访问令牌</label>
                <input id="local-agent-access-token" value={token} onChange={(event) => saveToken(event.target.value)} type="password" autoComplete="off" placeholder="FLUENTFLOW_ACCESS_TOKEN" className="h-10 w-full rounded-[10px] border border-[#dedada] bg-white px-3 text-[14px] font-semibold text-[#111111] outline-none transition focus:border-primary dark:border-white/[0.14] dark:bg-white/[0.06] dark:text-white"/>
                <p className="mt-2 text-[12px] font-semibold leading-5 text-[#686a70] dark:text-white/60">此值只写入本机浏览器存储，用于生成配置；没有令牌时，先在启动器或环境变量中设置它。</p>
            </div>
            <div className="mt-5 grid gap-4 lg:grid-cols-2">
                <CodeBlock label="MCP 配置" value={config} copied={copied === 'config'} onCopy={onCopy('config')}/>
                <CodeBlock label="Codex" value={codex} copied={copied === 'codex'} onCopy={onCopy('codex')}/>
            </div>
            <div className="mt-4 flex items-center gap-2 rounded-[14px] border border-[#e5e5e5] bg-white p-4 text-[13px] font-semibold text-[#686a70] dark:border-white/[0.12] dark:bg-white/[0.05] dark:text-white/60">
                <Terminal className="size-4 text-primary"/>
                验证：运行 <code className="font-mono text-[12px]">npm run mcp:check:e2e</code>。
            </div>
        </>
    );

    return (
        <section className={compact ? 'flex min-h-0 flex-1 flex-col' : 'mx-auto w-full max-w-5xl px-6 py-8 lg:px-10'}>
            <header className={`${compact ? 'border-b border-[#e5e5e5] px-5 py-4 dark:border-white/[0.12]' : 'mb-6'} flex items-start justify-between gap-4`}>
                <div className="min-w-0">
                    <p className="text-[11px] font-extrabold uppercase tracking-[0.08em] text-[#85868c] dark:text-white/50">FluentFlow MCP</p>
                    <h1 id={compact ? 'agent-access-title' : undefined} className={`${compact ? 'text-[18px] leading-6' : 'text-[28px] leading-8'} mt-1 font-extrabold text-[#111111] dark:text-white`}>连接本机 AI 工具</h1>
                    <p className="mt-2 max-w-[68ch] text-[14px] font-semibold leading-6 text-[#686a70] dark:text-white/60">配置后，Codex 或 Claude Code 可以在这台电脑上提交视频、等待处理并读取笔记。</p>
                </div>
                {onClose && <button type="button" onClick={onClose} className="h-9 shrink-0 rounded-[12px] border border-[#dedada] px-3 text-[13px] font-extrabold text-[#111111] transition hover:bg-[#efeeee] active:translate-y-px dark:border-white/[0.14] dark:text-white dark:hover:bg-white/[0.08]">关闭</button>}
            </header>
            {compact ? <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 py-4">{content}</div> : content}
        </section>
    );
};

export default LocalAgentAccessPanel;
