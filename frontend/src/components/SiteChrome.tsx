"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { apiRequest } from "../lib/api";
import { StockSearch } from "./StockSearch";

function subscribeHash(change: () => void) {
  window.addEventListener("hashchange", change);
  window.addEventListener("popstate", change);
  return () => {
    window.removeEventListener("hashchange", change);
    window.removeEventListener("popstate", change);
  };
}

export function SiteChrome({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const hash = useSyncExternalStore(subscribeHash, () => window.location.hash, () => "");
  const watchlistActive = pathname === "/" && hash === "#watchlist";
  const overviewActive = pathname === "/" && !watchlistActive;
  const [apiConnected, setApiConnected] = useState<boolean | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void apiRequest<{ status: string }>("/health", { signal: controller.signal })
      .then((result) => setApiConnected(result.status === "ok"))
      .catch((error) => {
        if (!(error instanceof Error && error.name === "AbortError")) setApiConnected(false);
      });
    return () => controller.abort();
  }, []);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Link className="brand" href="/" aria-label="StockPilot 首页">
          <span className="brand-mark">S</span>
          <span><strong>StockPilot</strong><small>A 股智能看盘</small></span>
        </Link>
        <nav aria-label="主导航">
          <p className="nav-title">Workspace</p>
          <Link className={`nav-link ${overviewActive ? "active" : ""}`} href="/" aria-current={overviewActive ? "page" : undefined} onClick={(event) => { if (pathname === "/" && hash) { event.preventDefault(); window.location.hash = ""; } }}>
            <span className="nav-dot" />市场概览
          </Link>
          <Link className={`nav-link ${watchlistActive ? "active" : ""}`} href="/#watchlist" aria-current={watchlistActive ? "page" : undefined} onClick={(event) => { if (pathname === "/") { event.preventDefault(); window.location.hash = "watchlist"; } }}><span className="nav-dot" />自选股</Link>
          <Link className={`nav-link ${pathname === "/agent" ? "active" : ""}`} href="/agent" aria-current={pathname === "/agent" ? "page" : undefined}><span className="nav-dot" />AI Agent</Link>
        </nav>
        <div className="sidebar-footer">
          <div className="status-line"><span className={`status-pulse ${apiConnected === false ? "offline" : ""}`} />
            {apiConnected === null ? "检查 API 连接…" : apiConnected ? "API 已连接" : "API 未连接"}
          </div>
          <small>多源行情 · V0.2 开发版</small>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <Link className="mobile-brand" href="/">StockPilot</Link>
          <StockSearch />
          <div className="top-actions"><span className="chip">A 股行情</span><span className="chip subtle">数据以接口为准</span></div>
        </header>
        {children}
      </div>
    </div>
  );
}
