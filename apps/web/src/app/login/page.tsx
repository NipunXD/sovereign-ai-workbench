"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ShieldCheck } from "lucide-react";

import { Button, Input } from "@/components/ui/primitives";
import { useSession } from "@/stores/session";

/** Demo accounts, shown because the separation-of-duties story is the point:
 *  these roles deliberately cannot do each other's jobs. */
const DEMO_ACCOUNTS = [
  { username: "senior", label: "Senior Inspection Engineer", note: "restricted clearance, can run code" },
  { username: "engineer", label: "Inspection Engineer", note: "confidential clearance" },
  { username: "approver", label: "Maintenance Head", note: "approves, cannot generate" },
  { username: "viewer", label: "Plant Operator", note: "internal clearance only" },
  { username: "auditor", label: "Internal Audit", note: "reads the audit log, acts on nothing" },
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

  return (
    <div className="flex h-full items-center justify-center p-6">
      <div className="w-full max-w-4xl">
        <div className="grid gap-6 md:grid-cols-[1fr_1.1fr]">
          <div className="flex flex-col justify-center">
            <div className="mb-4 flex items-center gap-2">
              <div className="flex h-8 w-8 items-center justify-center rounded bg-accent text-xs font-bold text-accent-fg">
                MW
              </div>
              <div>
                <h1 className="text-sm font-semibold">Sovereign AI Workbench</h1>
                <p className="text-2xs text-fg-subtle">
                  Mangalore Refinery and Petrochemicals Limited
                </p>
              </div>
            </div>

            <p className="text-sm leading-relaxed text-fg-muted">
              Agentic AI for confidential industrial work, running entirely on
              plant infrastructure.
            </p>

            <div className="mt-4 flex items-start gap-2 rounded border border-ok/30 bg-ok/5 p-2.5">
              <ShieldCheck size={14} className="mt-0.5 shrink-0 text-ok" />
              <p className="text-2xs leading-relaxed text-fg-muted">
                Every model runs locally on open weights. No document, query or
                measurement is sent to an external service — the running system
                reports the complete list of hosts it can reach.
              </p>
            </div>
          </div>

          <div className="panel p-5">
            <form onSubmit={submit} className="space-y-3">
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

              {error ? (
                <p className="rounded border border-danger/40 bg-danger/10 px-2 py-1.5 text-xs text-danger">
                  {error}
                </p>
              ) : null}

              <Button type="submit" variant="primary" className="w-full" disabled={busy}>
                {busy ? "Signing in…" : "Sign in"}
              </Button>
            </form>

            <div className="mt-4 border-t border-border pt-3">
              <p className="mb-2 text-2xs font-semibold uppercase tracking-wider text-fg-subtle">
                Demo accounts
              </p>
              <ul className="space-y-1">
                {DEMO_ACCOUNTS.map((account) => (
                  <li key={account.username}>
                    <button
                      type="button"
                      onClick={() => setUsername(account.username)}
                      className="flex w-full items-baseline gap-2 rounded px-1.5 py-1 text-left transition-colors hover:bg-surface-raised"
                    >
                      <span className="w-16 shrink-0 font-mono text-xs text-accent">
                        {account.username}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-2xs text-fg-muted">
                        {account.label}
                      </span>
                      <span className="shrink-0 text-2xs text-fg-subtle">{account.note}</span>
                    </button>
                  </li>
                ))}
              </ul>
              <p className="mt-2 px-1.5 text-2xs text-fg-subtle">
                All demo accounts use the password{" "}
                <span className="font-mono text-fg-muted">workbench123</span>.
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
