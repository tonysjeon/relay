import type { Metadata } from "next";
import Link from "next/link";
import { Icon, RelayMark } from "@/components/icons";
import "./globals.css";

export const metadata: Metadata = {
  title: "Relay · Workflow runtime",
  description: "Inspect workflow runs, dependencies, and step execution.",
};

export default function Layout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <a className="skip" href="#main">
          Skip to content
        </a>
        <aside className="sidebar">
          <Link href="/" className="brand" aria-label="Relay home">
            <RelayMark />
            <span>
              relay<span className="brand-caption">Workflow runtime</span>
            </span>
          </Link>
          <nav aria-label="Main navigation">
            <p className="nav-label">Workspace</p>
            <Link href="/" className="nav-item">
              <Icon name="workflow" />
              Workflow runs
              <Icon name="chevron-right" className="nav-chevron" />
            </Link>
            <Link href="/coding-sessions" className="nav-item">
              <Icon name="code" /> Coding sessions
              <Icon name="chevron-right" className="nav-chevron" />
            </Link>
          </nav>
          <div className="sidebar-foot">
            <span className="workspace-icon">
              <Icon name="monitor" />
            </span>
            <div>
              <strong>Local workspace</strong>
              <span>Development environment</span>
            </div>
          </div>
        </aside>
        <div className="workspace">
          <header className="topbar">
            <div className="breadcrumb">
              <Icon name="monitor" />
              <span>Local workspace</span>
              <Icon name="chevron-right" />
              <strong>Workflows</strong>
            </div>
            <span className="environment">Local</span>
          </header>
          <main id="main">{children}</main>
          <footer>
            <span>Relay</span>
            <span>Workflow execution, at a glance</span>
          </footer>
        </div>
      </body>
    </html>
  );
}
