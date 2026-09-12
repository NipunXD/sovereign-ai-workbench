"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ArrowRight, Cpu, Link2, ShieldCheck } from "lucide-react";

import { PipelineStrip } from "@/components/shell/PipelineStrip";
import { Button, ClassificationBadge, Input } from "@/components/ui/primitives";
import type { Classification } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useSession } from "@/stores/session";

/** Demo accounts, shown because the separation-of-duties story is the point:
 *  these roles deliberately cannot do each other's jobs. */
const DEMO_ACCOUNTS: Array<{
  username: string;
  label: string;
  note: string;
  clearance: Classification;
}> = [
  { username: "senior", label: "Senior Inspection Engineer", note: "generates documents, runs code", clearance: "restricted" },
  { username: "engineer", label: "Inspection Engineer", note: "asks, retrieves, calculates", clearance: "confidential" },
  { username: "approver", label: "Maintenance Head", note: "signs off — cannot generate", clearance: "confidential" },
  { username: "viewer", label: "Plant Operator", note: "reads what operators may read", clearance: "internal" },
  { username: "auditor", label: "Internal Audit", note: "reads the log, acts on nothing", clearance: "internal" },
];

const CLEARANCE_TONE: Record<Classification, string> = {
  public: "text-classification-public",
  internal: "text-classification-internal",
  confidential: "text-classification-confidential",
  restricted: "text-classification-restricted",
};

/** What the deployment can prove about itself, not what it promises. */
const PROOFS = [
  {
    icon: Cpu,
    title: "Open weights, on this hardware",
    body: "Qwen and Llama-family models served by LM Studio and Ollama on the plant's own GPU. The model manifest is a file in the repo.",
  },
  {
    icon: ShieldCheck,
    title: "Zero egress, verifiable",
    body: "The running system publishes the complete list of hosts it can reach. The header badge reads that list live and would turn red.",
  },
  {
    icon: Link2,
    title: "Second person, hash-chained",
    body: "Documents and code wait for someone else's sign-off. Every event commits to the one before it, so history cannot be edited quietly.",
  },
];

export default function LoginPage() {
  const router = useRouter();
  const { login, status, error, restore } = useSession();
  const [username, setUsername] = useState("senior");
  const [password, setPassword] = useState("workbench123");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (status === "loading") void restore();
    if (status === "authenticated") router.replace("/chat");
  }, [status, restore, router]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      await login(username, password);
      router.replace("/chat");
    } catch {
      /* the store holds the message */
    } finally {
      setBusy(false);
    }
  }

  const selected = DEMO_ACCOUNTS.find((a) => a.username === username);

  return (
    <div className="hero-canvas h-full overflow-y-auto">
      <div className="mx-auto flex min-h-full w-full max-w-6xl flex-col justify-center px-6 py-10">
        <div className="grid items-center gap-12 lg:grid-cols-[1.1fr_1fr]">
          {/* --- the claim --- */}
          <div className="stagger">
            <p className="mb-5 flex flex-wrap items-center gap-2 text-2xs font-semibold uppercase tracking-[0.18em] text-fg-subtle">
              <span className="brand-mark flex h-6 w-6 items-center justify-center rounded text-2xs font-bold text-accent-fg">
                MW
              </span>
              <span>Smart India Hackathon 2026</span>
              <span aria-hidden>·</span>
              <span>PS 26117</span>
              <span aria-hidden>·</span>
              <span>Team AIDUO</span>
            </p>

            <h1 className="text-4xl font-semibold leading-[1.08] tracking-tight sm:text-[2.75rem]">
              <span className="wordmark">Sovereign</span> AI Workbench
            </h1>
            <p className="mt-5 max-w-lg text-lg leading-relaxed text-fg-muted">
              An agent that plans, reads the plant&apos;s own documents, runs the numbers, and hands
              back an answer with the exact passage behind every claim — without a single byte
              leaving the refinery.
            </p>
            <p className="mt-2 text-xs text-fg-subtle">
              Built for Mangalore Refinery and Petrochemicals Limited · Crude Distillation Unit
            </p>

            <div className="mt-9 rounded-xl border border-border bg-surface p-5 shadow-card">
              <p className="mb-4 text-2xs font-semibold uppercase tracking-wider text-fg-subtle">
                How every answer is built
              </p>
              <PipelineStrip />
            </div>

            <ul className="mt-6 grid gap-3 sm:grid-cols-3">
              {PROOFS.map((proof) => {
                const Icon = proof.icon;
                return (
                  <li key={proof.title} className="rounded-xl border border-border bg-surface p-4 shadow-xs">
                    <Icon size={15} className="text-accent" />
                    <p className="mt-2 text-xs font-semibold text-fg">{proof.title}</p>
                    <p className="mt-1 text-xs leading-relaxed text-fg-subtle">{proof.body}</p>
                  </li>
                );
              })}
            </ul>
          </div>

          {/* --- the door --- */}
          <div className="animate-fade-in-up rounded-2xl border border-border bg-surface p-6 shadow-lifted">
            <div className="mb-4">
              <h2 className="text-lg font-semibold tracking-tight">Sign in</h2>
              <p className="mt-0.5 text-2xs text-fg-subtle">
                Pick a role to see what it is — and is not — allowed to do.
              </p>
            </div>

            <ul className="stagger mb-4 grid gap-1.5">
              {DEMO_ACCOUNTS.map((account) => (
                <li key={account.username}>
                  <button
                    type="button"
                    onClick={() => setUsername(account.username)}
                    data-selected={username === account.username}
                    className={cn(
                      "role-card flex w-full items-center gap-3 py-2.5 pl-4 pr-3 hover:border-border-strong",
                      CLEARANCE_TONE[account.clearance],
                    )}
                  >
                    <span className="w-16 shrink-0 font-mono text-xs text-fg">{account.username}</span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-xs font-medium text-fg">{account.label}</span>
                      <span className="block truncate text-xs text-fg-subtle">{account.note}</span>
                    </span>
                    <ClassificationBadge level={account.clearance} compact />
                  </button>
                </li>
              ))}
            </ul>

            <form onSubmit={submit} className="space-y-3">
              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <label htmlFor="username" className="mb-1 block text-2xs font-semibold uppercase tracking-wider text-fg-subtle">
                    Username
                  </label>
                  <Input
                    id="username"
                    value={username}
                    onChange={(event) => setUsername(event.target.value)}
                    autoComplete="username"
                    required
                  />
                </div>
                <div>
                  <label htmlFor="password" className="mb-1 block text-2xs font-semibold uppercase tracking-wider text-fg-subtle">
                    Password
                  </label>
                  <Input
                    id="password"
                    type="password"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    autoComplete="current-password"
                    required
                  />
                </div>
              </div>

              {error ? (
                <p className="rounded border border-danger/40 bg-danger/10 px-2 py-1.5 text-xs text-danger">
                  {error}
                </p>
              ) : null}

              <Button type="submit" variant="primary" size="lg" className="w-full" disabled={busy}>
                {busy ? "Signing in…" : `Enter as ${selected?.label ?? username}`}
                {!busy ? <ArrowRight size={14} /> : null}
              </Button>
            </form>

            <p className="mt-3 text-center text-2xs text-fg-subtle">
              Demo password for every account:{" "}
              <span className="font-mono text-fg-muted">workbench123</span>
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
