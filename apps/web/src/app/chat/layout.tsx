import { AppShell } from "@/components/shell/AppShell";

export default function ChatLayout({ children }: { children: React.ReactNode }) {
  return <AppShell>{children}</AppShell>;
}
