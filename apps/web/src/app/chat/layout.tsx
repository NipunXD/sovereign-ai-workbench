import { SessionsSidebar } from "@/components/chat/SessionsSidebar";
import { AppShell } from "@/components/shell/AppShell";

export default function ChatLayout({ children }: { children: React.ReactNode }) {
  return (
    <AppShell>
      <div className="flex h-full">
        <SessionsSidebar />
        <div className="min-w-0 flex-1">{children}</div>
      </div>
    </AppShell>
  );
}
