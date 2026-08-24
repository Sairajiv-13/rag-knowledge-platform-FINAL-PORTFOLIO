"use client";

import { useRouter } from "next/navigation";

export default function LogoutButton() {
  const router = useRouter();
  async function logout() {
    await fetch("/api/session", { method: "DELETE" });
    router.push("/login");
    router.refresh();
  }
  return (
    <button
      onClick={logout}
      className="rounded-md px-3 py-1.5 text-slate-500 hover:bg-slate-100 hover:text-slate-900"
    >
      Sign out
    </button>
  );
}
