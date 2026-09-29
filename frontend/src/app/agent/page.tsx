import Link from "next/link";
import { AgentWorkspace } from "../../components/AgentWorkspace";

export default async function AgentPage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string }>;
}) {
  const { q } = await searchParams;
  return (
    <main className="content agent-page">
      <nav className="breadcrumb" aria-label="当前位置"><Link href="/">市场概览</Link><span>/</span><span>AI Agent</span></nav>
      <div className="hero"><div><p className="eyebrow">STOCKPILOT / AGENT</p><h1>AI 行情分析</h1><p>结合真实行情与资讯，找到值得关注的机会和风险。</p></div></div>
      <AgentWorkspace initialPrompt={q?.slice(0, 2000) ?? ""} />
    </main>
  );
}
