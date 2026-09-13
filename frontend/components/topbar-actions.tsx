"use client";

import { useEffect, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

export function TopbarActions({ children }: { children: ReactNode }) {
  const [target, setTarget] = useState<HTMLElement | null>(null);
  useEffect(() => {
    setTarget(document.getElementById("topbar-actions"));
  }, []);
  return target ? createPortal(children, target) : null;
}
