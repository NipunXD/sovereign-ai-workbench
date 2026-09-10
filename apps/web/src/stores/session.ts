import { create } from "zustand";

import { api, setAccessToken } from "@/lib/api";
import type { Classification, Principal } from "@/lib/types";

interface SessionState {
  principal: Principal | null;
  status: "loading" | "authenticated" | "anonymous";
  error: string | null;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  restore: () => Promise<void>;
  can: (permission: string) => boolean;
  clearance: () => Classification;
}

export const useSession = create<SessionState>((set, get) => ({
  principal: null,
  status: "loading",
  error: null,

  async login(username, password) {
    set({ error: null });
    try {
      await api.login(username, password);
      set({ principal: await api.me(), status: "authenticated" });
    } catch (error) {
      set({ error: error instanceof Error ? error.message : "Sign-in failed." });
      throw error;
    }
  },

  async logout() {
    await api.logout();
    setAccessToken(null);
    set({ principal: null, status: "anonymous" });
  },

  async restore() {
    const principal = await api.restore();
    set(
      principal
        ? { principal, status: "authenticated" }
        : { principal: null, status: "anonymous" },
    );
  },

  can: (permission) => get().principal?.permissions.includes(permission) ?? false,
  clearance: () => get().principal?.clearance ?? "public",
}));
