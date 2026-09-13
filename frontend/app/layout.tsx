import type { Metadata } from "next";
import Link from "next/link";
import { Icon, RelayMark } from "@/components/icons";
import { WorkspaceNavigation, WorkspaceSection } from "@/components/workspace-navigation";
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
          <WorkspaceNavigation />
          <div className="sidebar-foot">
            <span className="local-indicator" aria-hidden="true" />
            <div>
              <strong>Local workspace</strong>
              <span>Development environment</span>
            </div>
          </div>
        </aside>
        <div className="workspace">
          <header className="topbar">
            <div className="topbar-inner">
              <div className="breadcrumb">
                <Icon name="monitor" />
                <span>Local workspace</span>
                <Icon name="chevron-right" />
                <WorkspaceSection />
              </div>
              <div className="topbar-right">
                <div id="topbar-actions" />
              </div>
            </div>
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
