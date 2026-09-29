"use client";

import { useId, useRef } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

export function MarkdownMessage({ content }: { content: string }) {
  const id = useId();
  const details = useRef<HTMLDetailsElement>(null);
  const boundary = /(?:^|\n)### (?:证据来源|数据时间与限制)\s*\n/.exec(content);
  const main = (boundary ? content.slice(0, boundary.index) : content)
    .replace(/(\*\*[^*\n]+\*\*)(?=[\p{L}\p{N}])/gu, "$1 ");
  const extra = boundary ? content.slice(boundary.index) : "";
  const evidence = [...new Set(content.match(/E[a-f0-9]{24}/g) || [])];
  const short = (value: string, links: boolean) => value.replace(/\[?(E[a-f0-9]{24})\]?/g, (_match, key: string) => {
    const number = evidence.indexOf(key) + 1;
    return links && extra ? `[${number}](#${id}-sources)` : `〔${number}〕`;
  });
  return <div className="markdown-message">
    <Markdown remarkPlugins={[remarkGfm]} skipHtml components={{ a: ({ href, children }) =>
      href === `#${id}-sources` ? <button className="evidence-reference" type="button" aria-label={`查看来源 ${children}`} onClick={() => {
        if (details.current) { details.current.open = true; details.current.scrollIntoView({ block: "nearest", behavior: "smooth" }); }
      }}>{children}</button> : <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>
    }}>{short(main, true)}</Markdown>
    {extra && <details className="answer-sources" id={`${id}-sources`} ref={details}>
      <summary>来源与数据时间{evidence.length ? ` · ${evidence.length} 条证据` : ""}</summary>
      <Markdown remarkPlugins={[remarkGfm]} skipHtml>{short(extra, false)}</Markdown>
    </details>}
  </div>;
}
