import { AppShell } from "@/components/shell/AppShell";

export default function EvalsLayout({ children }: { children: React.ReactNode }) {
  return <AppShell>{children}</AppShell>;
}
