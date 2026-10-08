"use client";

import { useEffect, useState } from "react";
import { errorText, getData } from "./api";
import type { CollectionState } from "./types";

type Snapshot<T> = { path: string; data: T; stale: boolean; cachedAt: string | null; collectionState: CollectionState | null };
type Failure = { path: string; message: string };

export function useResource<T>(path: string | null, refreshMs: number | ((data: T | null) => number) = 0) {
  const [snapshot, setSnapshot] = useState<Snapshot<T> | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [retryKey, setRetryKey] = useState(0);

  useEffect(() => {
    if (!path) return;
    let active = true;
    let pending = false;
    let latestData: T | null = null;
    let timer: number | null = null;
    const controller = new AbortController();
    const load = async () => {
      if (pending) return;
      pending = true;
      try {
        const result = await getData<T>(path, controller.signal);
        latestData = result.data;
        if (active) {
          setSnapshot((previous) => ({
            path,
            // Keep charts stable when a new response contains identical minute points.
            data: previous?.path === path && JSON.stringify(previous.data) === JSON.stringify(result.data)
              ? previous.data : result.data,
            stale: result.stale,
            cachedAt: result.cached_at ?? null,
            collectionState: result.collection_state ?? null,
          }));
          setFailure(null);
        }
      } catch (error) {
        if (active && !(error instanceof Error && error.name === "AbortError")) {
          setFailure({ path, message: errorText(error) });
        }
      } finally {
        pending = false;
        if (active && typeof refreshMs === "function") {
          const delay = refreshMs(latestData);
          if (delay > 0) timer = window.setTimeout(() => void load(), delay);
        }
      }
    };
    void load();
    if (typeof refreshMs === "number" && refreshMs > 0) timer = window.setInterval(() => void load(), refreshMs);
    return () => {
      active = false;
      controller.abort();
      if (timer !== null) window.clearInterval(timer);
    };
  }, [path, refreshMs, retryKey]);

  const current = snapshot?.path === path ? snapshot : null;
  const currentError = failure?.path === path ? failure.message : null;
  return {
    data: current?.data ?? null,
    stale: Boolean(current && (current.stale || currentError)),
    cachedAt: current?.cachedAt ?? null,
    collectionState: current?.collectionState ?? null,
    loading: Boolean(path && !current && !currentError),
    error: currentError,
    refresh: () => { setFailure(null); setRetryKey((key) => key + 1); },
  };
}
