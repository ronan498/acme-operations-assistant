export type ToolCall = {
  tool: string;
  args: string;
  decision: "allow" | "deny" | "duplicate" | "error";
  latency_ms: number;
  sql?: string[] | null;
};

export type StreamEvent =
  | { type: "tool_start"; call_id: string; tool: string; args: string }
  | { type: "tool_end"; call_id: string; tool: string; decision: ToolCall["decision"]; latency_ms: number; is_error: boolean; sql: string[] | null }
  | { type: "delta"; text: string }
  | { type: "text_reset" }
  | ({ type: "final"; est_cost_usd: number | null; trace_id: string | null } & Omit<ChatResponse, "est_cost_usd" | "trace_id">)
  | { type: "error"; detail: string };

export type ChatResponse = {
  answer: string;
  session_id: string;
  stop_reason: string;
  rounds: number;
  tool_calls: ToolCall[];
  usage: { input_tokens: number; output_tokens: number; cached_tokens: number };
  model: string;
  est_cost_usd: number | null;
  trace_id: string | null;
};

export type Me = { sub: string; username: string; roles: string[] };

async function request<T>(path: string, token: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    ...init,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
    },
  });
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      detail = (await resp.json()).detail ?? detail;
    } catch {
      /* keep the status line */
    }
    throw new Error(detail);
  }
  return resp.json() as Promise<T>;
}

export const fetchMe = (token: string) => request<Me>("/me", token);

export const sendChat = (token: string, message: string, sessionId: string) =>
  request<ChatResponse>("/chat", token, {
    method: "POST",
    body: JSON.stringify({ message, session_id: sessionId }),
  });

/** Consume /chat/stream as Server-Sent Events. Resolves after `final` (or throws). */
export async function streamChat(
  token: string,
  message: string,
  sessionId: string,
  onEvent: (event: StreamEvent) => void,
): Promise<void> {
  const resp = await fetch("/chat/stream", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ message, session_id: sessionId }),
  });
  if (!resp.ok || !resp.body) throw new Error(`stream failed: HTTP ${resp.status}`);

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) >= 0) {
      const chunk = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      for (const line of chunk.split("\n")) {
        if (!line.startsWith("data: ")) continue;
        const event = JSON.parse(line.slice(6)) as StreamEvent;
        if (event.type === "error") throw new Error(event.detail);
        onEvent(event);
      }
    }
  }
}
