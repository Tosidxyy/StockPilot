"use client";

import { AgentChat } from "./AgentChat";

export function AgentWorkspace({ initialPrompt }: { initialPrompt: string }) {
  return (
    <div className="agent-page-grid">
      <section className="card agent-workspace">
        <AgentChat
          initialPrompt={initialPrompt}
        />
      </section>
    </div>
  );
}
