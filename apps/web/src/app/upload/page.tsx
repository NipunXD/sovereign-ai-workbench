"use client";

import { useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  FileUp,
  ScanLine,
  Trash2,
  UploadCloud,
  XCircle,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useRef, useState } from "react";

import { StagePipeline } from "@/components/upload/StagePipeline";
import {
  Button,
  Chip,
  ClassificationBadge,
  Panel,
  PanelHeader,
} from "@/components/ui/primitives";
import { getAccessToken } from "@/lib/api";
import { streamRequest } from "@/lib/sse";
import type { Classification, IngestComplete } from "@/lib/types";
import { cn, formatBytes } from "@/lib/utils";
import { useSession } from "@/stores/session";

const CLASSIFICATIONS: Classification[] = ["public", "internal", "confidential", "restricted"];

const DOC_TYPES = [
  "inspection",
  "sop",
  "drawing",
  "tabular",
  "correspondence",
  "unknown",
] as const;

interface Job {
  id: string;
  file: File;
  status: "queued" | "running" | "done" | "duplicate" | "error";
  stage: string | null;
  progress: number;
  message: string;
  result?: IngestComplete;
  error?: string;
}

export default function UploadPage() {
  const { principal } = useSession();
  const queryClient = useQueryClient();
  const inputRef = useRef<HTMLInputElement>(null);

  const [jobs, setJobs] = useState<Job[]>([]);
  const [dragging, setDragging] = useState(false);
  const [classification, setClassification] = useState<Classification>("internal");
  const [docType, setDocType] = useState<string>("inspection");
  const [departments, setDepartments] = useState("");

  // A document cannot be filed above the uploader's own clearance: they would
  // immediately lose access to what they just filed, and they would be
  // asserting a sensitivity they are not cleared to judge. The API refuses it
  // too — this just stops the user reaching a dead end.
  const clearanceRank = CLASSIFICATIONS.indexOf(principal?.clearance ?? "public");
  const allowed = CLASSIFICATIONS.slice(0, clearanceRank + 1);

  const patch = useCallback((id: string, update: Partial<Job>) => {
    setJobs((current) =>
      current.map((job) => (job.id === id ? { ...job, ...update } : job)),
    );
  }, []);

  const ingest = useCallback(
    async (job: Job) => {
      patch(job.id, { status: "running", message: "starting…" });

      const form = new FormData();
      form.append("file", job.file);
      form.append("classification", classification);
      form.append("doc_type", docType);
      form.append("departments", departments);

      try {
        for await (const event of streamRequest("/api/v1/documents/upload", {
          formData: form,
          token: getAccessToken(),
        })) {
          const payload = JSON.parse(event.data);
          if (event.event === "progress") {
            patch(job.id, {
              stage: String(payload.stage),
              progress: Number(payload.progress),
              message: String(payload.message),
            });
          } else if (event.event === "complete") {
            patch(job.id, {
              status: "done",
              progress: 1,
              stage: null,
              result: payload as IngestComplete,
            });
            void queryClient.invalidateQueries({ queryKey: ["documents"] });
          } else if (event.event === "duplicate") {
            patch(job.id, {
              status: "duplicate",
              progress: 1,
              message: String(payload.message),
              result: payload as IngestComplete,
            });
          } else if (event.event === "error") {
            patch(job.id, { status: "error", error: String(payload.message) });
          }
        }
      } catch (error) {
        patch(job.id, {
          status: "error",
          error: error instanceof Error ? error.message : "The upload failed.",
        });
      }
    },
    [classification, departments, docType, patch, queryClient],
  );

  const enqueue = useCallback(
    (files: FileList | File[]) => {
      const created: Job[] = Array.from(files).map((file) => ({
        id: `${file.name}-${file.size}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        file,
        status: "queued",
        stage: null,
        progress: 0,
        message: "",
      }));
      setJobs((current) => [...created, ...current]);
      // Sequential, not parallel. Ingestion is CPU-bound — OCR and embedding
      // both saturate the machine — so running several at once makes every one
      // of them slower and the progress meaningless.
      void (async () => {
        for (const job of created) await ingest(job);
      })();
    },
    [ingest],
  );

  return (
    <div className="h-full overflow-y-auto p-4">
      <div className="mx-auto max-w-4xl space-y-4">
        <Panel>
          <PanelHeader title="Ingest documents" />
          <div className="space-y-3 p-3">
            <div className="grid gap-3 sm:grid-cols-3">
              <label className="block">
                <span className="mb-1 block text-2xs font-semibold uppercase tracking-wider text-fg-subtle">
                  Classification
                </span>
                <select
                  value={classification}
                  onChange={(event) => setClassification(event.target.value as Classification)}
                  className="h-8 w-full rounded border border-border bg-bg px-2 text-sm text-fg focus:border-accent/60"
                >
                  {allowed.map((level) => (
                    <option key={level} value={level}>
                      {level}
                    </option>
                  ))}
                </select>
              </label>

              <label className="block">
                <span className="mb-1 block text-2xs font-semibold uppercase tracking-wider text-fg-subtle">
                  Document type
                </span>
                <select
                  value={docType}
                  onChange={(event) => setDocType(event.target.value)}
                  className="h-8 w-full rounded border border-border bg-bg px-2 text-sm text-fg focus:border-accent/60"
                >
                  {DOC_TYPES.map((type) => (
                    <option key={type} value={type}>
                      {type}
                    </option>
                  ))}
                </select>
              </label>

              <label className="block">
                <span className="mb-1 block text-2xs font-semibold uppercase tracking-wider text-fg-subtle">
                  Departments
                </span>
                <input
                  value={departments}
                  onChange={(event) => setDepartments(event.target.value)}
                  placeholder="inspection, process"
                  className="h-8 w-full rounded border border-border bg-bg px-2 text-sm text-fg placeholder:text-fg-subtle focus:border-accent/60"
                />
              </label>
            </div>

            {clearanceRank < CLASSIFICATIONS.length - 1 ? (
              <p className="text-2xs text-fg-subtle">
                Your clearance is{" "}
                <span className="text-fg-muted">{principal?.clearance}</span>, so you cannot
                file a document above that level.
              </p>
            ) : null}

            <div
              onDragOver={(event) => {
                event.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(event) => {
                event.preventDefault();
                setDragging(false);
                if (event.dataTransfer.files.length) enqueue(event.dataTransfer.files);
              }}
              onClick={() => inputRef.current?.click()}
              className={cn(
                "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-md border-2 border-dashed p-8 transition-colors",
                dragging
                  ? "border-accent bg-accent/10"
                  : "border-border bg-bg hover:border-border-strong",
              )}
            >
              <UploadCloud size={22} className={dragging ? "text-accent" : "text-fg-subtle"} />
              <p className="text-sm font-medium">
                Drop files here, or click to choose
              </p>
              <p className="text-2xs text-fg-subtle">
                PDF (native or scanned), images, Word, PowerPoint, Excel, CSV, text, email
              </p>
              <input
                ref={inputRef}
                type="file"
                multiple
                className="hidden"
                onChange={(event) => {
                  if (event.target.files?.length) enqueue(event.target.files);
                  event.target.value = "";
                }}
              />
            </div>
          </div>
        </Panel>

        {jobs.length ? (
          <Panel>
            <PanelHeader
              title={`Ingestion queue (${jobs.length})`}
              actions={
                <button
                  type="button"
                  onClick={() =>
                    setJobs((current) => current.filter((job) => job.status === "running"))
                  }
                  className="flex items-center gap-1 rounded px-1.5 py-0.5 text-2xs normal-case text-fg-subtle hover:bg-surface-raised hover:text-fg"
                >
                  <Trash2 size={10} /> Clear finished
                </button>
              }
            />
            <ul className="divide-y divide-border">
              {jobs.map((job) => (
                <li key={job.id} className="p-3">
                  <div className="mb-2 flex items-start gap-2">
                    <StatusIcon status={job.status} />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-xs font-medium text-fg">{job.file.name}</p>
                      <p className="mt-0.5 text-2xs text-fg-subtle">
                        {formatBytes(job.file.size)}
                        {job.message ? ` · ${job.message}` : ""}
                      </p>
                    </div>
                    <ClassificationBadge level={classification} compact />
                  </div>

                  {job.status === "running" ? (
                    <>
                      <div className="mb-2 h-1 overflow-hidden rounded-full bg-bg">
                        <div
                          className="h-full rounded-full bg-accent transition-all duration-300"
                          style={{ width: `${job.progress * 100}%` }}
                        />
                      </div>
                      <StagePipeline currentStage={job.stage} done={false} />
                    </>
                  ) : null}

                  {job.status === "done" && job.result ? (
                    <IngestSummary result={job.result} />
                  ) : null}

                  {job.status === "duplicate" ? (
                    <p className="text-2xs text-fg-muted">
                      Already indexed — content-addressed storage means nothing was
                      re-ingested.{" "}
                      {job.result?.document_id ? (
                        <Link
                          href="/documents"
                          className="text-accent underline underline-offset-2"
                        >
                          View in the library
                        </Link>
                      ) : null}
                    </p>
                  ) : null}

                  {job.status === "error" ? (
                    <p className="rounded border border-danger/40 bg-danger/10 px-2 py-1 text-2xs text-danger">
                      {job.error}
                    </p>
                  ) : null}
                </li>
              ))}
            </ul>
          </Panel>
        ) : null}
      </div>
    </div>
  );
}

function StatusIcon({ status }: { status: Job["status"] }) {
  if (status === "done") return <CheckCircle2 size={14} className="mt-0.5 shrink-0 text-ok" />;
  if (status === "duplicate") return <FileUp size={14} className="mt-0.5 shrink-0 text-info" />;
  if (status === "error") return <XCircle size={14} className="mt-0.5 shrink-0 text-danger" />;
  if (status === "running")
    return (
      <span className="mt-0.5 h-3.5 w-3.5 shrink-0 animate-spin rounded-full border-[1.5px] border-accent border-t-transparent" />
    );
  return <span className="mt-1 h-2 w-2 shrink-0 rounded-full bg-fg-subtle" />;
}

function IngestSummary({ result }: { result: IngestComplete }) {
  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-1.5">
        <Chip tone="ok">{result.pages} page{result.pages === 1 ? "" : "s"}</Chip>
        <Chip>{result.chunks} chunks</Chip>
        <Chip>{result.vectors} indexed</Chip>
        {result.ocr_pages ? (
          <Chip tone="accent">
            <ScanLine size={9} /> {result.ocr_pages} recognised
          </Chip>
        ) : null}
        {result.vlm_pages ? <Chip tone="accent">{result.vlm_pages} vision</Chip> : null}
        <Chip tone={result.mean_confidence < 0.85 ? "warn" : "neutral"}>
          {(result.mean_confidence * 100).toFixed(0)}% confidence
        </Chip>
      </div>
      {result.degraded || result.warnings.length ? (
        <div className="flex items-start gap-1.5 rounded border border-warn/40 bg-warn/10 px-2 py-1">
          <AlertTriangle size={11} className="mt-0.5 shrink-0 text-warn" />
          <div className="text-2xs text-warn">
            {result.warnings.length ? (
              <ul className="space-y-0.5">
                {result.warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            ) : (
              <p>
                Indexed, but the extracted text is low quality. Answers citing it are
                flagged so a reader knows to check the source.
              </p>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}
