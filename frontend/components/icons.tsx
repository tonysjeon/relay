import type { SVGProps } from "react";

type IconName =
  | "workflow"
  | "refresh"
  | "arrow-right"
  | "arrow-left"
  | "chevron-right"
  | "filter"
  | "clock"
  | "monitor"
  | "check"
  | "close"
  | "circle"
  | "code";

const paths: Record<IconName, React.ReactNode> = {
  workflow: (
    <>
      <rect x="3" y="9" width="6" height="6" rx="1.5" />
      <rect x="15" y="3" width="6" height="6" rx="1.5" />
      <rect x="15" y="15" width="6" height="6" rx="1.5" />
      <path d="M9 12h2a2 2 0 0 0 2-2V8a2 2 0 0 1 2-2M9 12h2a2 2 0 0 1 2 2v2a2 2 0 0 0 2 2" />
    </>
  ),
  refresh: (
    <>
      <path d="M20 7v5h-5M4 17v-5h5" />
      <path d="M6.1 6.1A8 8 0 0 1 19.5 10M4.5 14A8 8 0 0 0 18 18" />
    </>
  ),
  "arrow-right": <path d="M4 12h16m-6-6 6 6-6 6" />,
  "arrow-left": <path d="M20 12H4m6-6-6 6 6 6" />,
  "chevron-right": <path d="m9 5 7 7-7 7" />,
  filter: (
    <>
      <path d="M4 7h16M4 17h16" />
      <circle cx="9" cy="7" r="2" fill="currentColor" stroke="none" />
      <circle cx="15" cy="17" r="2" fill="currentColor" stroke="none" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </>
  ),
  monitor: (
    <>
      <rect x="3" y="4" width="18" height="13" rx="2" />
      <path d="M8 21h8m-4-4v4" />
    </>
  ),
  check: <path d="m5 12 4 4L19 6" />,
  close: <path d="m6 6 12 12M6 18 18 6" />,
  circle: <circle cx="12" cy="12" r="7" />,
  code: (
    <>
      <path d="m7 7-5 5 5 5m10-10 5 5-5 5m-4-13-2 16" />
    </>
  ),
};

export function Icon({
  name,
  ...props
}: SVGProps<SVGSVGElement> & { name: IconName }) {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...props}
    >
      {paths[name]}
    </svg>
  );
}

export function RelayMark() {
  return (
    <svg
      width="36"
      height="36"
      viewBox="0 0 40 40"
      fill="none"
      aria-hidden="true"
      focusable="false"
    >
      <rect width="40" height="40" rx="8" fill="#303841" />
      <path
        d="M14 20h4a4 4 0 0 0 4-4v-2m-8 6h4a4 4 0 0 1 4 4v2"
        stroke="#A9B7C5"
        strokeWidth="2.2"
      />
      <rect x="8" y="16" width="8" height="8" rx="2.5" fill="white" />
      <rect x="21" y="8" width="8" height="8" rx="2.5" fill="white" />
      <rect x="21" y="24" width="8" height="8" rx="2.5" fill="#A9B7C5" />
    </svg>
  );
}
