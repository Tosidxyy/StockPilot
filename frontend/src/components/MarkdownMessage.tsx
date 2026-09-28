import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

export function MarkdownMessage({ content }: { content: string }) {
  return <div className="markdown-message"><Markdown remarkPlugins={[remarkGfm]} skipHtml>{content}</Markdown></div>;
}
