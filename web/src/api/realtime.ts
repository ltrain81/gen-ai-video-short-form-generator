import { events } from 'aws-amplify/data';

export type AgentEvent =
  | { type: 'turn.started' | 'turn.completed'; turn: string }
  | { type: 'turn.failed'; turn: string; error?: string }
  | { type: 'assistant.delta'; turn: string; text: string }
  | { type: 'tool'; turn: string; name: string; status: string; error?: string }
  | { type: 'timeline.updated'; version: number; message?: string }
  | { type: 'question'; questionId: string; question: string; kind: 'text' | 'choice' | 'clips' | 'confirm' | 'range';
      options?: string[]; clips?: { assetId: string; startSec: number; endSec: number; label?: string }[];
      multiple?: boolean; estimate?: Record<string, unknown> };

/** Subscribe to this user's project channel. Returns an unsubscribe function. */
export async function subscribeProject(projectId: string, sub: string, onEvent: (e: AgentEvent) => void): Promise<() => void> {
  const channel = await events.connect(`/projects/${projectId}/${sub}`);
  const subscription = channel.subscribe({
    next: (data: unknown) => {
      const raw = (data as { event?: unknown }).event ?? data;
      try { onEvent((typeof raw === 'string' ? JSON.parse(raw) : raw) as AgentEvent); } catch { /* ignore malformed */ }
    },
    error: (err: unknown) => console.error('realtime', err),
  });
  return () => { subscription.unsubscribe(); channel.close(); };
}
