"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, API_BASE } from "./api";
import type { AgentEvent, Plan, Task } from "./types";

const TERMINAL = new Set(["COMPLETED", "FAILED", "REJECTED"]);

/** Streams a task's events over SSE; falls back to polling if the stream is unavailable. */
export function useAgentTask() {
  const [task, setTask] = useState<Task | null>(null);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [transport, setTransport] = useState<"sse" | "polling" | null>(null);
  const esRef = useRef<EventSource | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const lastSeq = useRef(0);
  const taskIdRef = useRef<string | null>(null);

  const stop = useCallback(() => {
    esRef.current?.close();
    esRef.current = null;
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = null;
  }, []);

  const refreshTask = useCallback(async (id: string) => {
    const t = await api.task(id);
    if (taskIdRef.current !== id) return t;
    setTask(t);
    if (t.plan_id) setPlan(await api.plan(t.plan_id));
    return t;
  }, []);

  const ingest = useCallback(
    (evs: AgentEvent[], id: string) => {
      const fresh = evs.filter((e) => e.seq > lastSeq.current);
      if (!fresh.length) return;
      lastSeq.current = fresh[fresh.length - 1].seq;
      setEvents((prev) => [...prev, ...fresh]);
      if (fresh.some((e) => ["STATE_CHANGED", "APPROVAL", "REPORT"].includes(e.type))) void refreshTask(id);
    },
    [refreshTask],
  );

  const startPolling = useCallback(
    (id: string) => {
      setTransport("polling");
      pollRef.current = setInterval(async () => {
        try {
          const { events: evs } = await api.events(id, lastSeq.current);
          ingest(evs, id);
          const t = await refreshTask(id);
          if (t && TERMINAL.has(t.state)) stop();
        } catch (e) {
          setError(String(e));
        }
      }, 900);
    },
    [ingest, refreshTask, stop],
  );

  const subscribe = useCallback(
    (id: string) => {
      stop();
      if (typeof EventSource === "undefined") return startPolling(id);
      const es = new EventSource(`${API_BASE}/api/agent/tasks/${id}/events?after=${lastSeq.current}`);
      esRef.current = es;
      setTransport("sse");
      es.addEventListener("agent-event", (m) => ingest([JSON.parse((m as MessageEvent).data)], id));
      es.addEventListener("task", (m) => {
        const t = JSON.parse((m as MessageEvent).data) as Task;
        if (taskIdRef.current === id) setTask(t);
      });
      es.addEventListener("end", () => {
        es.close();
        void refreshTask(id);
      });
      es.onerror = () => {
        if (es.readyState === EventSource.CLOSED || lastSeq.current === 0) {
          es.close();
          startPolling(id);
        }
      };
    },
    [ingest, refreshTask, startPolling, stop],
  );

  const run = useCallback(
    async (command: string) => {
      stop();
      setError(null);
      setEvents([]);
      setPlan(null);
      lastSeq.current = 0;
      try {
        const t = await api.createTask(command);
        taskIdRef.current = t.id;
        setTask(t);
        subscribe(t.id);
      } catch (e) {
        setError(String(e));
      }
    },
    [stop, subscribe],
  );

  /** After approve/reject the task resumes server-side; re-open the stream to follow it. */
  const follow = useCallback(async () => {
    const id = taskIdRef.current;
    if (!id) return;
    await refreshTask(id);
    subscribe(id);
  }, [refreshTask, subscribe]);

  /** Follow an existing task (e.g. one started by an external agent over MCP) from its first event. */
  const attach = useCallback(
    async (id: string) => {
      stop();
      setError(null);
      setEvents([]);
      setPlan(null);
      lastSeq.current = 0;
      taskIdRef.current = id;
      await refreshTask(id);
      subscribe(id);
    },
    [refreshTask, stop, subscribe],
  );

  const clear = useCallback(() => {
    stop();
    taskIdRef.current = null;
    lastSeq.current = 0;
    setTask(null);
    setEvents([]);
    setPlan(null);
    setError(null);
  }, [stop]);

  useEffect(() => stop, [stop]);

  return { task, events, plan, error, transport, run, follow, attach, clear, setPlan };
}
