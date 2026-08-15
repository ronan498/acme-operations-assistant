import {
  ArrowClockwise,
  CaretRight,
  Copy,
  Database,
  PaperPlaneRight,
  ShieldCheck,
  ShieldSlash,
  SignOut,
  StackSimple,
} from "@phosphor-icons/react";
import DOMPurify from "dompurify";
import { marked } from "marked";
import { useEffect, useRef, useState } from "react";
import {
  fetchMe,
  sendChat,
  streamChat,
  type ChatResponse,
  type Me,
  type ToolCall,
} from "./api";
import { beginLogin, clearTokens, completeLoginFromUrl, getAccessToken } from "./auth";

const PHOENIX_URL = "http://localhost:6006";

const DEMO_USERS = [
  { username: "ada", name: "Ada Okafor", role: "admin", blurb: "Full access. Creates and updates next actions." },
  { username: "sara", name: "Sara Lindqvist", role: "support_user", blurb: "Reads everything, posts issue updates." },
  { username: "sam", name: "Sam Whitmore", role: "sales_user", blurb: "Read-only view of customers and issues." },
];

const SUGGESTIONS = [
  "Give me an escalation summary for Northwind Logistics",
  "What changed recently on the Northwind EDI feed issue?",
  "Create a next action for the Meridian PHI issue: assign an owner today",
];

const ROLE_STYLE: Record<string, string> = {
  admin: "text-accent border-accent/40 bg-accent/10",
  support_user: "text-fog-300 border-ink-700 bg-ink-800",
  sales_user: "text-fog-500 border-ink-700 bg-ink-850",
};

type LiveTool = {
  tool: string;
  args: string;
  running: boolean;
  decision?: ToolCall["decision"];
  latency_ms?: number;
  sql?: string[] | null;
};

type Turn =
  | { kind: "user"; text: string }
  | { kind: "assistant"; resp: ChatResponse }
  | { kind: "error"; text: string };

function renderMarkdown(text: string): string {
  return DOMPurify.sanitize(marked.parse(text, { async: false }) as string);
}

function prettyArgs(args: string): string {
  try {
    return JSON.stringify(JSON.parse(args || "{}"), null, 2);
  } catch {
    return args;
  }
}

function RoleChip({ role }: { role: string }) {
  return (
    <span className={`rounded-md border px-2 py-0.5 font-mono text-[11px] ${ROLE_STYLE[role] ?? ROLE_STYLE.sales_user}`}>
      {role}
    </span>
  );
}

/** One tool invocation: chip row, expandable to arguments + executed SQL. */
function ToolRow({ call }: { call: LiveTool }) {
  const [open, setOpen] = useState(false);
  const decision = call.running ? "running" : (call.decision ?? "error");
  const styles =
    decision === "running"
      ? "border-ink-500 bg-ink-800 text-fog-300"
      : decision === "allow"
        ? "border-ok/30 bg-ok/10 text-ok"
        : decision === "deny"
          ? "border-bad/40 bg-bad/10 text-bad"
          : "border-ink-700 bg-ink-800 text-fog-500";
  const Icon = decision === "deny" ? ShieldSlash : ShieldCheck;
  const hasDetail = Boolean(call.args && call.args !== "{}") || Boolean(call.sql?.length);

  return (
    <div className="min-w-0">
      <button
        onClick={() => hasDetail && setOpen(!open)}
        className={`inline-flex max-w-full items-center gap-1.5 rounded-md border px-2 py-1 font-mono text-[11px] ${styles} ${hasDetail ? "cursor-pointer" : "cursor-default"}`}
        aria-expanded={open}
      >
        {hasDetail && (
          <CaretRight size={10} weight="bold" className={`shrink-0 transition-transform ${open ? "rotate-90" : ""}`} />
        )}
        {call.running ? (
          <span className="h-2 w-2 shrink-0 animate-pulse rounded-full bg-accent" aria-label="running" />
        ) : (
          <Icon size={12} weight="bold" className="shrink-0" />
        )}
        <span className="truncate">{call.tool}</span>
        <span className="shrink-0 opacity-60">
          {call.running ? "running" : decision === "duplicate" ? "dup" : `${Math.round(call.latency_ms ?? 0)}ms`}
        </span>
      </button>
      {open && (
        <div className="mt-1.5 space-y-2 rounded-md border border-ink-700 bg-ink-900 p-3">
          {call.args && call.args !== "{}" && (
            <div>
              <p className="mb-1 font-mono text-[10px] uppercase tracking-wide text-fog-500">arguments</p>
              <pre className="overflow-x-auto font-mono text-[11px] leading-relaxed text-fog-300">{prettyArgs(call.args)}</pre>
            </div>
          )}
          {call.sql?.length ? (
            <div>
              <p className="mb-1 flex items-center gap-1 font-mono text-[10px] uppercase tracking-wide text-fog-500">
                <Database size={11} /> sql executed
              </p>
              {call.sql.map((stmt, i) => (
                <pre key={i} className="mb-1 overflow-x-auto whitespace-pre-wrap rounded bg-ink-950 p-2 font-mono text-[11px] leading-relaxed text-fog-300">{stmt}</pre>
              ))}
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}

function MetaLine({ resp, onCopyTrace, copied }: { resp: ChatResponse; onCopyTrace: () => void; copied: boolean }) {
  return (
    <p className="flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[11px] text-fog-500">
      <span>{resp.model}</span>
      <span>{resp.rounds} {resp.rounds === 1 ? "round" : "rounds"}</span>
      <span>{resp.usage.input_tokens.toLocaleString()} in / {resp.usage.output_tokens.toLocaleString()} out</span>
      {resp.usage.cached_tokens > 0 && <span className="text-ok">{resp.usage.cached_tokens.toLocaleString()} cached</span>}
      {resp.est_cost_usd != null && <span>${resp.est_cost_usd.toFixed(4)}</span>}
      {resp.trace_id && (
        <span className="inline-flex items-center gap-1.5">
          <a href={PHOENIX_URL} target="_blank" rel="noreferrer" className="underline decoration-ink-700 underline-offset-2 hover:text-fog-300">
            trace
          </a>
          <button onClick={onCopyTrace} title={resp.trace_id} className="text-fog-500 hover:text-fog-300" aria-label="copy trace id">
            <Copy size={12} />
          </button>
          {copied && <span className="text-ok">copied</span>}
        </span>
      )}
    </p>
  );
}

function AssistantTurn({ resp }: { resp: ChatResponse }) {
  const [copied, setCopied] = useState(false);
  const copyTrace = () => {
    if (!resp.trace_id) return;
    void navigator.clipboard.writeText(resp.trace_id);
    setCopied(true);
    setTimeout(() => setCopied(false), 1200);
  };
  return (
    <div className="space-y-3">
      {resp.tool_calls.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {resp.tool_calls.map((call, i) => (
            <ToolRow key={i} call={{ ...call, running: false }} />
          ))}
        </div>
      )}
      <div className="md" dangerouslySetInnerHTML={{ __html: renderMarkdown(resp.answer) }} />
      <MetaLine resp={resp} onCopyTrace={copyTrace} copied={copied} />
    </div>
  );
}

/** The in-flight turn: live tool rows appear as the agent works, then the
 * answer streams in token by token. */
function LiveTurn({ tools, text, startedAt }: { tools: LiveTool[]; text: string; startedAt: number }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setElapsed((Date.now() - startedAt) / 1000), 100);
    return () => clearInterval(id);
  }, [startedAt]);

  return (
    <div className="space-y-3" aria-live="polite">
      {tools.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {tools.map((call, i) => (
            <ToolRow key={i} call={call} />
          ))}
        </div>
      )}
      {text ? (
        <div className="md" dangerouslySetInnerHTML={{ __html: renderMarkdown(text) }} />
      ) : (
        <div className="space-y-2.5 py-1">
          <div className="shimmer h-3.5 w-3/5 rounded" />
          <div className="shimmer h-3.5 w-2/5 rounded" />
        </div>
      )}
      <p className="font-mono text-[11px] text-fog-500">
        {text ? "writing" : tools.some((t) => t.running) ? "calling tools" : "reasoning"} · {elapsed.toFixed(1)}s
      </p>
    </div>
  );
}

function LoginScreen() {
  return (
    <main className="grid min-h-[100dvh] lg:grid-cols-[1.2fr_1fr]">
      <section className="hidden flex-col justify-between border-r border-ink-800 p-10 lg:flex">
        <p className="font-mono text-sm tracking-wide text-fog-300">
          ACME<span className="text-accent">/</span>OPS
        </p>
        <div className="max-w-md space-y-4">
          <h1 className="text-4xl font-semibold leading-tight tracking-tight text-fog-100">
            One question, straight to an answer.
          </h1>
          <p className="text-fog-500">
            Customer issues, histories, and next actions live in one assistant. Every tool call is
            authorized against your role and written to the audit log.
          </p>
        </div>
        <p className="font-mono text-[11px] text-fog-500">
          Keycloak login · role-gated tools · full audit trail
        </p>
      </section>

      <section className="flex flex-col justify-center gap-6 p-8 sm:p-12">
        <div className="lg:hidden">
          <p className="font-mono text-sm tracking-wide text-fog-300">
            ACME<span className="text-accent">/</span>OPS
          </p>
        </div>
        <div>
          <h2 className="text-lg font-medium text-fog-100">Sign in as</h2>
          <p className="mt-1 text-sm text-fog-500">Demo users, password <code className="font-mono text-fog-300">demo</code> for all.</p>
        </div>
        <div className="space-y-2.5">
          {DEMO_USERS.map((u) => (
            <button
              key={u.username}
              onClick={() => void beginLogin(u.username)}
              className="group flex w-full items-center justify-between rounded-[10px] border border-ink-700 bg-ink-900 px-4 py-3.5 text-left transition-colors hover:border-ink-500 hover:bg-ink-850 active:scale-[0.99]"
            >
              <span>
                <span className="block text-sm font-medium text-fog-100">{u.name}</span>
                <span className="mt-0.5 block text-[13px] text-fog-500">{u.blurb}</span>
              </span>
              <RoleChip role={u.role} />
            </button>
          ))}
        </div>
        <p className="text-[13px] text-fog-500">
          You will authenticate on Keycloak's own login page and return here with a signed token.
        </p>
      </section>
    </main>
  );
}

export default function App() {
  const [phase, setPhase] = useState<"boot" | "login" | "ready">("boot");
  const [me, setMe] = useState<Me | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState<number | null>(null); // startedAt ms
  const [liveTools, setLiveTools] = useState<LiveTool[]>([]);
  const [liveText, setLiveText] = useState("");
  const [sessionId, setSessionId] = useState(() => crypto.randomUUID());
  const [sessionCost, setSessionCost] = useState(0);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    (async () => {
      try {
        await completeLoginFromUrl();
        const token = await getAccessToken();
        if (!token) return setPhase("login");
        setMe(await fetchMe(token));
        setPhase("ready");
      } catch {
        clearTokens();
        setPhase("login");
      }
    })();
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns, sending, liveTools, liveText]);

  const finish = (resp: ChatResponse) => {
    setSessionCost((c) => c + (resp.est_cost_usd ?? 0));
    setTurns((t) => [...t, { kind: "assistant", resp }]);
  };

  const ask = async (text: string) => {
    const message = text.trim();
    if (!message || sending) return;
    setInput("");
    setTurns((t) => [...t, { kind: "user", text: message }]);
    setSending(Date.now());
    setLiveTools([]);
    setLiveText("");
    try {
      const token = await getAccessToken();
      if (!token) {
        setPhase("login");
        return;
      }
      try {
        await streamChat(token, message, sessionId, (event) => {
          if (event.type === "tool_start") {
            setLiveTools((prev) => [...prev, { tool: event.tool, args: event.args, running: true }]);
          } else if (event.type === "tool_end") {
            setLiveTools((prev) => {
              const next = [...prev];
              const i = next.findIndex((t) => t.running && t.tool === event.tool);
              if (i >= 0)
                next[i] = { ...next[i], running: false, decision: event.decision,
                            latency_ms: event.latency_ms, sql: event.sql };
              return next;
            });
          } else if (event.type === "delta") {
            setLiveText((prev) => prev + event.text);
          } else if (event.type === "final") {
            finish(event as unknown as ChatResponse);
          }
        });
      } catch (streamErr) {
        // transport hiccup: fall back to the plain request/response path once
        console.warn("stream failed, falling back:", streamErr);
        finish(await sendChat(token, message, sessionId));
      }
    } catch (err) {
      setTurns((t) => [...t, { kind: "error", text: err instanceof Error ? err.message : String(err) }]);
    } finally {
      setSending(null);
      setLiveTools([]);
      setLiveText("");
    }
  };

  const newChat = () => {
    setSessionId(crypto.randomUUID());
    setTurns([]);
    setSessionCost(0);
  };

  const signOut = () => {
    clearTokens();
    setMe(null);
    setTurns([]);
    setPhase("login");
  };

  if (phase === "boot") return <main className="min-h-[100dvh]" />;
  if (phase === "login") return <LoginScreen />;

  return (
    <div className="flex min-h-[100dvh] flex-col">
      <header className="sticky top-0 z-10 border-b border-ink-800 bg-ink-950/90 backdrop-blur">
        <div className="mx-auto flex h-16 max-w-4xl items-center justify-between px-4">
          <p className="font-mono text-sm tracking-wide text-fog-300">
            ACME<span className="text-accent">/</span>OPS
          </p>
          <div className="flex items-center gap-3">
            <span className="rounded-md border border-ink-700 bg-ink-900 px-2 py-1 font-mono text-[11px] text-fog-300" title="estimated LLM spend this session">
              ${sessionCost.toFixed(4)}
            </span>
            <span className="hidden items-center gap-2 sm:flex">
              <span className="text-sm text-fog-300">{me?.username}</span>
              {me?.roles.map((r) => <RoleChip key={r} role={r} />)}
            </span>
            <button onClick={newChat} title="new conversation" aria-label="new conversation"
              className="rounded-md border border-ink-700 p-2 text-fog-500 transition-colors hover:border-ink-500 hover:text-fog-300 active:scale-[0.97]">
              <ArrowClockwise size={15} />
            </button>
            <button onClick={signOut} title="sign out" aria-label="sign out"
              className="rounded-md border border-ink-700 p-2 text-fog-500 transition-colors hover:border-ink-500 hover:text-fog-300 active:scale-[0.97]">
              <SignOut size={15} />
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-4xl flex-1 px-4">
        {turns.length === 0 && !sending ? (
          <div className="flex min-h-[60vh] flex-col items-start justify-center gap-6">
            <div className="space-y-2">
              <StackSimple size={22} className="text-accent" />
              <h1 className="text-xl font-medium text-fog-100">Ask about customers, issues, and next actions.</h1>
              <p className="max-w-lg text-sm text-fog-500">
                The agent picks its own tools. What your role does not permit gets refused, explained,
                and audited.
              </p>
            </div>
            <div className="flex flex-col items-start gap-2">
              {SUGGESTIONS.map((s) => (
                <button key={s} onClick={() => void ask(s)}
                  className="rounded-[10px] border border-ink-700 bg-ink-900 px-3.5 py-2 text-left text-sm text-fog-300 transition-colors hover:border-ink-500 hover:bg-ink-850 active:scale-[0.99]">
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="space-y-7 py-8">
            {turns.map((turn, i) =>
              turn.kind === "user" ? (
                <div key={i} className="flex justify-end">
                  <p className="max-w-[85%] rounded-[10px] border border-ink-700 bg-ink-850 px-4 py-2.5 text-[15px] text-fog-100">
                    {turn.text}
                  </p>
                </div>
              ) : turn.kind === "assistant" ? (
                <AssistantTurn key={i} resp={turn.resp} />
              ) : (
                <div key={i} className="rounded-[10px] border border-bad/40 bg-bad/10 px-4 py-3 text-sm text-bad">
                  {turn.text}
                </div>
              ),
            )}
            {sending && <LiveTurn tools={liveTools} text={liveText} startedAt={sending} />}
            <div ref={endRef} />
          </div>
        )}
      </main>

      <footer className="sticky bottom-0 border-t border-ink-800 bg-ink-950/90 pb-4 pt-3 backdrop-blur">
        <form
          className="mx-auto flex max-w-4xl items-end gap-2 px-4"
          onSubmit={(e) => {
            e.preventDefault();
            void ask(input);
          }}
        >
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void ask(input);
              }
            }}
            rows={1}
            placeholder={`Ask as ${me?.username ?? "user"}…`}
            aria-label="message"
            className="max-h-40 min-h-[46px] flex-1 resize-none rounded-[10px] border border-ink-700 bg-ink-900 px-4 py-3 text-[15px] text-fog-100 outline-none transition-colors placeholder:text-fog-500 focus:border-accent-dim"
          />
          <button
            type="submit"
            disabled={!input.trim() || !!sending}
            aria-label="send"
            className="flex h-[46px] w-[46px] items-center justify-center rounded-[10px] bg-accent text-ink-950 transition-all hover:bg-accent-dim active:scale-[0.96] disabled:opacity-35"
          >
            <PaperPlaneRight size={18} weight="fill" />
          </button>
        </form>
      </footer>
    </div>
  );
}
