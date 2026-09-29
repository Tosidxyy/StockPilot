"use client";

import { useState, type FormEvent } from "react";

const initial = { start: "", end: "", keyword: "", category: "" };

export function useListFilters() {
  const [draft, setDraft] = useState(initial);
  const [applied, setApplied] = useState(initial);
  const [issue, setIssue] = useState("");
  const [page, setPage] = useState(1);
  const change = (field: keyof typeof initial, value: string) => {
    setDraft((current) => ({ ...current, [field]: value })); setIssue("");
  };
  const apply = (event: FormEvent) => {
    event.preventDefault();
    if (draft.start && draft.end && draft.start > draft.end) {
      setIssue("起始日期不能晚于结束日期；请修正后筛选，当前结果保持不变。"); return false;
    }
    setApplied({ ...draft, keyword: draft.keyword.trim() }); setIssue(""); setPage(1); return true;
  };
  const clear = () => { setDraft(initial); setApplied(initial); setIssue(""); setPage(1); };
  const params = new URLSearchParams({ page: String(page), page_size: "10" });
  for (const [key, value] of Object.entries(applied)) if (value) params.set(key, value);
  return { draft, change, apply, clear, issue, page, setPage, params };
}
