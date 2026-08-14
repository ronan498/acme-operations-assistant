export type ToolCall = {
  tool: string;
  args: string;
  decision: "allow" | "deny" | "duplicate" | "error";
  latency_ms: number;
};

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
