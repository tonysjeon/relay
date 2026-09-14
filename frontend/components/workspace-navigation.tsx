"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Icon } from "@/components/icons";

function useCodingSection() {
  const pathname = usePathname();
  return pathname === "/coding-sessions" || pathname.startsWith("/coding-sessions/");
}

export function WorkspaceNavigation() {
  const coding = useCodingSection();
  return (
    <nav aria-label="Main navigation">
      <p className="nav-label">Workspace</p>
      <div className="nav-tabs">
        <Link href="/" className="nav-item" aria-current={!coding ? "page" : undefined}>
          <Icon name="workflow" /> Workflow runs
        </Link>
        <Link href="/coding-sessions" className="nav-item" aria-current={coding ? "page" : undefined}>
          <Icon name="code" /> Coding sessions
        </Link>
      </div>
    </nav>
  );
}

export function WorkspaceSection() {
  return <strong>{useCodingSection() ? "Coding sessions" : "Workflow runs"}</strong>;
}
