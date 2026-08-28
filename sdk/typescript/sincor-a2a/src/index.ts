export type TaskCreated = {
  task_id: string;
  skill: string;
  bounty_axm: number;
  requires_merit: boolean;
};

type Handler = (task: TaskCreated) => void | Promise<void>;

async function sign(payload: Record<string, unknown>, baseUrl: string) {
  const res = await fetch(`${baseUrl}/api/v1/sign`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = (await res.json()) as { signature: string };
  return data.signature;
}

export class SincorA2A {
  private handlers: Handler[] = [];
  constructor(
    private baseUrl: string,
    private agentId: string,
  ) {}

  onTask(handler: Handler) {
    this.handlers.push(handler);
    return this;
  }

  async register(input: {
    tags: string[];
    wallet: string;
    rpc_callback: string;
    name?: string;
  }) {
    const payload = {
      agent_id: this.agentId,
      name: input.name ?? this.agentId,
      capability_tags: input.tags,
      rpc_callback: input.rpc_callback,
      wallet: input.wallet,
      chain_id: 8453,
    };
    const signature = await sign(payload, this.baseUrl);
    const res = await fetch(`${this.baseUrl}/v1/a2a/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...payload, signature }),
    });
    return res.json();
  }

  async heartbeat() {
    const res = await fetch(`${this.baseUrl}/v1/a2a/heartbeat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agent_id: this.agentId }),
    });
    return res.json();
  }

  async bid(taskId: string, bidAxm: number, timeEstMs: number) {
    const res = await fetch(`${this.baseUrl}/v1/a2a/bids`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        task_id: taskId,
        agent_id: this.agentId,
        bid_axm: bidAxm,
        time_est_ms: timeEstMs,
      }),
    });
    return res.json();
  }

  async submitProof(taskId: string, receiptHash: string) {
    const res = await fetch(`${this.baseUrl}/v1/a2a/proofs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        task_id: taskId,
        agent_id: this.agentId,
        receipt_hash: receiptHash,
      }),
    });
    return res.json();
  }

  listen(tags: string[] = []) {
    const url = `${this.baseUrl}/v1/a2a/stream?tags=${tags.join(",")}`;
    const connect = () => {
      const es = new EventSource(url);
      es.addEventListener("task.created", (ev) => {
        const data = JSON.parse((ev as MessageEvent).data) as { payload: TaskCreated };
        for (const h of this.handlers) void h(data.payload);
      });
      es.onerror = () => {
        es.close();
        setTimeout(connect, 1500);
      };
    };
    connect();
  }
}
