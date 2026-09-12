import type { Metadata } from "next";
import Link from "next/link";
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
          <Link href="/" className="brand">
            <span className="mark">r</span>relay
            <span className="version">LOCAL</span>
          </Link>
          <p className="nav-label">WORKSPACE</p>
          <Link href="/" className="nav-item">
            <span aria-hidden="true">▤</span> Workflow runs
          </Link>
          <div className="sidebar-foot">
            <span className="mini-mark">↗</span>
            <strong>Built to keep going.</strong>
            <p>
              Persistent workflows.
              <br />
              Visible execution.
            </p>
          </div>
        </aside>
        <div className="workspace">
          <header className="topbar">
            <span>
              Workspace <span className="slash">/</span>{" "}
              <strong>Local environment</strong>
            </span>
            <span className="environment">Development</span>
          </header>
          <main id="main">{children}</main>
          <footer>
            RELAY <span>Workflow runtime</span>
          </footer>
        </div>
      </body>
    </html>
  );
}
