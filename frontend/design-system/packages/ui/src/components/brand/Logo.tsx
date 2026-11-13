// The flask: currentColor outside, logo-inner inside
export function Logo({ size = 18 }: { size?: number }) {
  return (
    <svg className="lb-logo" width={size} height={size} viewBox="0 0 64 64" aria-hidden="true">
      <path d="M23 3 H41 V21 L58 54 A6 6 0 0 1 52.7 62 H11.3 A6 6 0 0 1 6 54 L23 21Z" fill="currentColor" />
      <path
        d="M15.5 40 H48.5 L54.5 52.5 A3 3 0 0 1 51.8 57 H12.2 A3 3 0 0 1 9.5 52.5Z"
        style={{ fill: "var(--lb-sys-color-logo-inner)" }}
      />
      <path
        d="M29 11 V19 L23 31 L26 26 L29 31 L32 26 L35 31 L38 26 L41 31 L35 19 V11"
        fill="none"
        strokeWidth={3.2}
        strokeLinejoin="round"
        strokeLinecap="round"
        style={{ stroke: "var(--lb-sys-color-logo-inner)" }}
      />
    </svg>
  );
}
