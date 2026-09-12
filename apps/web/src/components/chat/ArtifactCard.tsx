"use client";

import { AlertTriangle, CheckCircle2, ChevronDown, Download, FileSpreadsheet, FileText, Presentation } from "lucide-react";
import { useState } from "react";

import { Button, Chip } from "@/components/ui/primitives";
import { downloadArtifact } from "@/lib/api";
import type { GeneratedArtifact } from "@/lib/types";
import { cn, formatBytes } from "@/lib/utils";

/**
 * A document the run produced, as a card in the conversation.
 *
 * Before this the only trace of a generated file was a tool line in the side
 * panel and an entry on another page. A report the person asked for should
 * land in front of them: what it is, who signed it off, what it was built
 * from, and a button that saves it.
 */
export function ArtifactCard({ artifact }: { artifact: GeneratedArtifact }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const provenance = artifact.provenance;
  const sources = provenance.sources ?? [];
  const approved = Boolean(provenance.approved_by);

  async function save() {
    setBusy(true);
    try {
      await downloadArtifact(artifact.sha256, artifact.filename);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="artifact-card animate-fade-in-up mt-4 overflow-hidden rounded-xl border border-border bg-surface shadow-sm">
      <div className="flex items-center gap-3 p-3.5">
        <KindGlyph kind={artifact.kind} />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-fg" title={artifact.filename}>
            {artifact.filename}
          </p>
          <p className="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-2xs text-fg-subtle">
            <span className="uppercase">{artifact.kind}</span>
            <span className="tnum">{formatBytes(artifact.size_bytes)}</span>
            <span className="tnum">{sources.length} source{sources.length === 1 ? "" : "s"}</span>
            {provenance.has_uncertain_sources ? (
              <span className="flex items-center gap-1 text-warn">
                <AlertTriangle size={10} /> rests on scanned text
              </span>
            ) : null}
          </p>
        </div>
        {approved ? (
          <Chip tone="ok">
            <CheckCircle2 size={10} /> approved
          </Chip>
        ) : (
          <Chip tone="warn">awaiting approval</Chip>
        )}
        <Button variant="primary" size="sm" onClick={save} disabled={busy}>
          <Download size={12} /> {busy ? "saving…" : "Download"}
        </Button>
      </div>

      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1.5 border-t border-border px-3.5 py-2 text-left text-2xs font-medium text-fg-subtle transition-colors hover:bg-surface-raised hover:text-fg"
      >
        <ChevronDown size={12} className={cn("transition-transform", open && "rotate-180")} />
        Provenance
        {approved ? (
          <span className="ml-auto truncate">
            signed off by <span className="text-fg-muted">{provenance.approved_by}</span>
          </span>
        ) : null}
      </button>

      {open ? (
        <div className="border-t border-border bg-surface-raised/50 px-3.5 py-3 text-2xs">
          <dl className="grid gap-x-4 gap-y-1 sm:grid-cols-2">
            <Row label="Requested by">{provenance.generated_by ?? "—"}</Row>
            <Row label="Approved by" tone={approved ? "ok" : "warn"}>
              {approved
                ? `${provenance.approved_by}${provenance.approved_at ? ` · ${new Date(provenance.approved_at).toLocaleString()}` : ""}`
                : "not yet"}
            </Row>
            <Row label="Models" mono>
              {Object.values(provenance.models ?? {}).join(", ") || "—"}
            </Row>
            <Row label="Digest" mono>
              {artifact.sha256.slice(0, 20)}…
            </Row>
          </dl>
          {sources.length ? (
            <ul className="mt-2 space-y-0.5 border-t border-border pt-2">
              {sources.map((source) => (
                <li key={`${source.title}-${source.pages.join(",")}`} className="flex items-baseline gap-2">
                  <span className="min-w-0 flex-1 truncate text-fg-muted">{source.title}</span>
                  <span className="tnum shrink-0 text-fg-subtle">p{source.pages.join(", ")}</span>
                  {source.lowest_confidence < 0.85 ? (
                    <span className="tnum shrink-0 text-warn">OCR {(source.lowest_confidence * 100).toFixed(0)}%</span>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-2 text-warn">No documents were cited — this content is not grounded in the corpus.</p>
          )}
          <p className="mt-2 border-t border-border pt-1.5 leading-relaxed text-fg-subtle">
            This record is written into the file itself, so it travels with the document when it is sent on.
          </p>
        </div>
      ) : null}
    </div>
  );
}

function Row({
  label,
  children,
  mono,
  tone,
}: {
  label: string;
  children: React.ReactNode;
  mono?: boolean;
  tone?: "ok" | "warn";
}) {
  return (
    <div className="flex gap-2">
      <dt className="w-20 shrink-0 text-fg-subtle">{label}</dt>
      <dd
        className={cn(
          "min-w-0 flex-1 truncate",
          mono && "font-mono",
          tone === "ok" ? "text-ok" : tone === "warn" ? "text-warn" : "text-fg-muted",
        )}
      >
        {children}
      </dd>
    </div>
  );
}

export function KindGlyph({ kind, size = "md" }: { kind: string; size?: "md" | "lg" }) {
  const base = cn(
    "flex shrink-0 items-center justify-center rounded-xl",
    size === "lg" ? "h-12 w-12" : "h-10 w-10",
  );
  const glyph = size === "lg" ? 22 : 16;
  if (kind === "xlsx")
    return (
      <span className={cn(base, "bg-ok/10 text-ok ring-1 ring-inset ring-ok/20")}>
        <FileSpreadsheet size={glyph} />
      </span>
    );
  if (kind === "pptx")
    return (
      <span className={cn(base, "bg-accent-muted text-accent ring-1 ring-inset ring-accent/20")}>
        <Presentation size={glyph} />
      </span>
    );
  return (
    <span className={cn(base, "bg-info/10 text-info ring-1 ring-inset ring-info/20")}>
      <FileText size={glyph} />
    </span>
  );
}
