"use client";

import { forwardRef, type ButtonHTMLAttributes, type HTMLAttributes, type InputHTMLAttributes } from "react";

import { cn } from "@/lib/utils";
import type { Classification } from "@/lib/types";

// --- button -----------------------------------------------------------------

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "subtle";
type ButtonSize = "sm" | "md" | "lg" | "icon";

/**
 * One filled button per screen.
 *
 * `primary` is indigo and carries the single action that matters here;
 * `secondary` is a white surface with a border, which is what every other
 * action should be. When two filled buttons sit side by side, neither reads
 * as primary.
 */
const buttonVariants: Record<ButtonVariant, string> = {
  primary:
    "bg-accent text-accent-fg shadow-xs hover:bg-accent-strong active:bg-accent-strong focus-visible:ring-accent/60",
  secondary:
    "border border-border bg-surface text-fg shadow-xs hover:border-border-strong hover:bg-surface-raised",
  subtle: "bg-surface-raised text-fg-muted hover:bg-surface-sunken hover:text-fg",
  ghost: "text-fg-muted hover:bg-surface-raised hover:text-fg",
  danger: "bg-danger text-white shadow-xs hover:bg-danger/90",
};

const buttonSizes: Record<ButtonSize, string> = {
  sm: "h-8 gap-1.5 rounded-md px-2.5 text-xs",
  md: "h-9 gap-2 rounded-lg px-3.5 text-sm",
  lg: "h-10 gap-2 rounded-lg px-4 text-md",
  icon: "h-8 w-8 rounded-md",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = "secondary", size = "md", ...props }, ref) => (
    <button
      ref={ref}
      className={cn(
        "inline-flex shrink-0 items-center justify-center whitespace-nowrap font-medium",
        "transition-[background-color,border-color,box-shadow,color] duration-150",
        "disabled:pointer-events-none disabled:opacity-50",
        buttonSizes[size],
        buttonVariants[variant],
        className,
      )}
      {...props}
    />
  ),
);
Button.displayName = "Button";

// --- input ------------------------------------------------------------------

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input
      ref={ref}
      className={cn(
        "h-9 w-full rounded-lg border border-border bg-surface px-3 text-sm text-fg shadow-xs",
        "transition-[border-color,box-shadow] duration-150",
        "placeholder:text-fg-subtle",
        "focus:border-accent/60 focus:outline-none focus:ring-4 focus:ring-accent/10",
        "disabled:cursor-not-allowed disabled:bg-surface-raised disabled:opacity-60",
        className,
      )}
      {...props}
    />
  ),
);
Input.displayName = "Input";

export const Select = forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, ...props }, ref) => (
    <select
      ref={ref}
      className={cn(
        "h-9 w-full appearance-none rounded-lg border border-border bg-surface px-3 pr-8 text-sm text-fg shadow-xs",
        "bg-[length:14px] bg-[right_0.6rem_center] bg-no-repeat",
        "focus:border-accent/60 focus:outline-none focus:ring-4 focus:ring-accent/10",
        className,
      )}
      style={{
        backgroundImage:
          "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16' fill='none' stroke='%2364748b' stroke-width='1.8' stroke-linecap='round'%3E%3Cpath d='M4 6.5 8 10.5 12 6.5'/%3E%3C/svg%3E\")",
      }}
      {...props}
    />
  ),
);
Select.displayName = "Select";

// --- surfaces ---------------------------------------------------------------

export function Panel({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("card", className)} {...props} />;
}

export function PanelHeader({
  title,
  description,
  actions,
  className,
}: {
  title: string;
  description?: string;
  actions?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("card-header", className)}>
      <div className="min-w-0">
        <p className="card-title truncate">{title}</p>
        {description ? <p className="mt-0.5 truncate text-xs text-fg-subtle">{description}</p> : null}
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-1.5">{actions}</div> : null}
    </div>
  );
}

/** The band at the top of a page: what this is, and what you can do to it. */
export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: React.ReactNode;
}) {
  return (
    <div className="flex shrink-0 flex-wrap items-center gap-3 border-b border-border bg-surface px-5 py-3">
      <div className="min-w-0 flex-1">
        <h1 className="truncate text-lg font-semibold tracking-tight text-fg">{title}</h1>
        {description ? <p className="mt-0.5 truncate text-xs text-fg-muted">{description}</p> : null}
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </div>
  );
}

/** One number, one label. The unit the reference dashboards are built from. */
export function StatTile({
  value,
  label,
  hint,
  tone,
  icon,
}: {
  value: string;
  label: string;
  hint?: string;
  tone?: "warn" | "danger" | "ok";
  icon?: React.ReactNode;
}) {
  return (
    <div className="stat-tile">
      <div className="flex items-center gap-2">
        {icon ? <span className="text-fg-subtle">{icon}</span> : null}
        <span className="section-label truncate">{label}</span>
      </div>
      <p
        className={cn(
          "tnum mt-2 text-2xl font-semibold leading-none tracking-tight",
          tone === "warn" ? "text-warn" : tone === "danger" ? "text-danger" : tone === "ok" ? "text-ok" : "text-fg",
        )}
      >
        {value}
      </p>
      {hint ? <p className="mt-1.5 truncate text-2xs text-fg-subtle">{hint}</p> : null}
    </div>
  );
}

// --- classification ---------------------------------------------------------

const classificationStyles: Record<Classification, string> = {
  public: "border-classification-public/25 bg-classification-public/10 text-classification-public",
  internal: "border-classification-internal/25 bg-classification-internal/10 text-classification-internal",
  confidential:
    "border-classification-confidential/25 bg-classification-confidential/10 text-classification-confidential",
  restricted: "border-classification-restricted/25 bg-classification-restricted/10 text-classification-restricted",
};

/**
 * Sensitivity, always shown with its label.
 *
 * Never colour alone: a refinery control room is exactly where you find bad
 * monitors and colour-vision deficiency, and "which of these documents is
 * restricted" is not a question anyone should have to squint at.
 */
export function ClassificationBadge({
  level,
  className,
  compact = false,
}: {
  level: Classification;
  className?: string;
  compact?: boolean;
}) {
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2 py-0.5",
        "text-2xs font-semibold uppercase tracking-wider",
        classificationStyles[level],
        className,
      )}
      title={`Classification: ${level}`}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden />
      {compact ? level.slice(0, 3) : level}
    </span>
  );
}

// --- status -----------------------------------------------------------------

export function StatusDot({
  state,
  pulse = false,
  className,
}: {
  state: "ok" | "warn" | "danger" | "idle";
  pulse?: boolean;
  className?: string;
}) {
  const tone = { ok: "bg-ok", warn: "bg-warn", danger: "bg-danger", idle: "bg-fg-subtle" }[state];
  return (
    <span className={cn("relative flex h-2 w-2 shrink-0", className)} aria-hidden>
      {pulse ? <span className={cn("absolute inline-flex h-full w-full animate-ping rounded-full opacity-60", tone)} /> : null}
      <span className={cn("relative inline-flex h-2 w-2 rounded-full", tone)} />
    </span>
  );
}

/** A pill. Soft tint, no hard border, the way every SaaS status badge reads. */
export function Chip({
  children,
  className,
  tone = "neutral",
}: {
  children: React.ReactNode;
  className?: string;
  tone?: "neutral" | "accent" | "ok" | "warn" | "danger" | "info";
}) {
  const tones = {
    neutral: "bg-surface-raised text-fg-muted ring-border",
    accent: "bg-accent-muted text-accent ring-accent/20",
    ok: "bg-ok/10 text-ok ring-ok/20",
    warn: "bg-warn/10 text-warn ring-warn/20",
    danger: "bg-danger/10 text-danger ring-danger/20",
    info: "bg-info/10 text-info ring-info/20",
  }[tone];
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-2xs font-medium ring-1 ring-inset",
        tones,
        className,
      )}
    >
      {children}
    </span>
  );
}

export function Spinner({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "inline-block h-3.5 w-3.5 shrink-0 animate-spin rounded-full border-2 border-current border-t-transparent opacity-70",
        className,
      )}
      aria-hidden
    />
  );
}

export function EmptyState({
  icon,
  title,
  hint,
  action,
}: {
  icon?: React.ReactNode;
  title: string;
  hint?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 px-6 py-14 text-center">
      {icon ? (
        <div className="flex h-11 w-11 items-center justify-center rounded-xl border border-border bg-surface-raised text-fg-subtle">
          {icon}
        </div>
      ) : null}
      <div>
        <p className="text-sm font-semibold text-fg">{title}</p>
        {hint ? <p className="mx-auto mt-1 max-w-sm text-xs leading-relaxed text-fg-subtle">{hint}</p> : null}
      </div>
      {action}
    </div>
  );
}
