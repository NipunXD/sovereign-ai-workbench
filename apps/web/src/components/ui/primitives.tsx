"use client";

import { forwardRef, type ButtonHTMLAttributes, type HTMLAttributes, type InputHTMLAttributes } from "react";

import { cn } from "@/lib/utils";
import type { Classification } from "@/lib/types";

// --- button -----------------------------------------------------------------

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
type ButtonSize = "sm" | "md";

const buttonVariants: Record<ButtonVariant, string> = {
  primary: "bg-accent text-accent-fg hover:bg-accent/90 active:bg-accent/80",
  secondary:
    "bg-surface-raised text-fg border border-border hover:border-border-strong hover:bg-surface-raised/80",
  ghost: "text-fg-muted hover:bg-surface-raised hover:text-fg",
  danger: "bg-danger/15 text-danger border border-danger/40 hover:bg-danger/25",
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
        "inline-flex items-center justify-center gap-1.5 rounded font-medium transition-colors",
        "disabled:pointer-events-none disabled:opacity-40",
        size === "sm" ? "h-7 px-2 text-xs" : "h-8 px-3 text-sm",
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
        "h-8 w-full rounded border border-border bg-bg px-2.5 text-sm text-fg",
        "placeholder:text-fg-subtle focus:border-accent/60",
        className,
      )}
      {...props}
    />
  ),
);
Input.displayName = "Input";

// --- panel ------------------------------------------------------------------

export function Panel({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("panel", className)} {...props} />;
}

export function PanelHeader({
  title,
  actions,
  className,
}: {
  title: string;
  actions?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("panel-header", className)}>
      <span>{title}</span>
      {actions ? <div className="flex items-center gap-1">{actions}</div> : null}
    </div>
  );
}

// --- classification ---------------------------------------------------------

const classificationStyles: Record<Classification, string> = {
  public: "border-classification-public/50 bg-classification-public/10 text-classification-public",
  internal:
    "border-classification-internal/50 bg-classification-internal/10 text-classification-internal",
  confidential:
    "border-classification-confidential/50 bg-classification-confidential/10 text-classification-confidential",
  restricted:
    "border-classification-restricted/50 bg-classification-restricted/10 text-classification-restricted",
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
        "inline-flex shrink-0 items-center gap-1 rounded border px-1.5 py-0.5",
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
  const tone = {
    ok: "bg-ok",
    warn: "bg-warn",
    danger: "bg-danger",
    idle: "bg-fg-subtle",
  }[state];
  return (
    <span
      className={cn("h-1.5 w-1.5 shrink-0 rounded-full", tone, pulse && "animate-pulse-dot", className)}
      aria-hidden
    />
  );
}

export function Chip({
  children,
  className,
  tone = "neutral",
}: {
  children: React.ReactNode;
  className?: string;
  tone?: "neutral" | "accent" | "ok" | "warn" | "danger";
}) {
  const tones = {
    neutral: "border-border bg-surface-raised text-fg-muted",
    accent: "border-accent/40 bg-accent/10 text-accent",
    ok: "border-ok/40 bg-ok/10 text-ok",
    warn: "border-warn/40 bg-warn/10 text-warn",
    danger: "border-danger/40 bg-danger/10 text-danger",
  }[tone];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-2xs font-medium",
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
        "inline-block h-3 w-3 animate-spin rounded-full border-[1.5px] border-current border-t-transparent",
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
}: {
  icon?: React.ReactNode;
  title: string;
  hint?: string;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 p-8 text-center">
      {icon ? <div className="text-fg-subtle">{icon}</div> : null}
      <p className="text-sm font-medium text-fg-muted">{title}</p>
      {hint ? <p className="max-w-sm text-xs text-fg-subtle">{hint}</p> : null}
    </div>
  );
}
