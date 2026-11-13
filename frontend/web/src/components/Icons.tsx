// Small line icons, so they look the same whatever fonts the system has

const props = {
  width: 14,
  height: 14,
  viewBox: "0 0 16 16",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.5,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
};

export function PinIcon({ filled }: { filled: boolean }) {
  return (
    <svg {...props} fill={filled ? "currentColor" : "none"}>
      <path d="M8 1.8l1.9 3.9 4.3.6-3.1 3 .7 4.3L8 11.6l-3.8 2 .7-4.3-3.1-3 4.3-.6z" />
    </svg>
  );
}

export function PencilIcon() {
  return (
    <svg {...props}>
      <path d="M11 2.5l2.5 2.5L5.5 13H3v-2.5z" />
      <path d="M9.5 4l2.5 2.5" />
    </svg>
  );
}

export function CloseIcon() {
  return (
    <svg {...props}>
      <path d="M4 4l8 8M12 4l-8 8" />
    </svg>
  );
}

export function MenuIcon() {
  return (
    <svg {...props} width={16} height={16}>
      <path d="M2.5 4h11M2.5 8h11M2.5 12h11" />
    </svg>
  );
}

/** Showing or hiding the sidebar */
export function SidebarIcon() {
  return (
    <svg {...props} width={16} height={16}>
      <rect x="2" y="2.5" width="12" height="11" rx="2" />
      <path d="M6 2.5v11" />
    </svg>
  );
}

export function NewChatIcon() {
  return (
    <svg {...props}>
      <path d="M8 3v10M3 8h10" />
    </svg>
  );
}

export function MoreIcon() {
  return (
    <svg {...props}>
      <circle cx="3.5" cy="8" r="1.1" fill="currentColor" stroke="none" />
      <circle cx="8" cy="8" r="1.1" fill="currentColor" stroke="none" />
      <circle cx="12.5" cy="8" r="1.1" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function DownloadIcon() {
  return (
    <svg {...props}>
      <path d="M8 2.5v8M4.5 7L8 10.5 11.5 7M3 13.5h10" />
    </svg>
  );
}

export function ShareIcon() {
  return (
    <svg {...props}>
      <path d="M6.5 9.5l3-3M7 4.5l1-1a2.5 2.5 0 013.5 3.5l-1 1M9 11.5l-1 1a2.5 2.5 0 01-3.5-3.5l1-1" />
    </svg>
  );
}

export function CopyIcon() {
  return (
    <svg {...props} width={13} height={13}>
      <rect x="5" y="5" width="8.5" height="8.5" rx="1.5" />
      <path d="M11 5V3.5A1.5 1.5 0 0 0 9.5 2h-6A1.5 1.5 0 0 0 2 3.5v6A1.5 1.5 0 0 0 3.5 11H5" />
    </svg>
  );
}

export function RegenerateIcon() {
  return (
    <svg {...props} width={13} height={13}>
      <path d="M13 8a5 5 0 1 1-1.5-3.5M13 2.5v3h-3" />
    </svg>
  );
}

export function SearchIcon() {
  return (
    <svg {...props}>
      <circle cx="7" cy="7" r="4.5" />
      <path d="M10.5 10.5L14 14" />
    </svg>
  );
}

export function ArrowUpIcon() {
  return (
    <svg {...props}>
      <path d="M8 13V3M4 7l4-4 4 4" />
    </svg>
  );
}

export function ArrowDownIcon() {
  return (
    <svg {...props}>
      <path d="M8 3v10M4 9l4 4 4-4" />
    </svg>
  );
}

export function GearIcon() {
  return (
    <svg {...props}>
      <circle cx="8" cy="8" r="2" />
      <path d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.4 3.4l1.4 1.4M11.2 11.2l1.4 1.4M3.4 12.6l1.4-1.4M11.2 4.8l1.4-1.4" />
    </svg>
  );
}

export function LogOutIcon() {
  return (
    <svg {...props}>
      <path d="M6 13.5H3.5a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1H6M10.5 11l3-3-3-3M13.5 8H6" />
    </svg>
  );
}

export function ChatsIcon() {
  return (
    <svg {...props}>
      <path d="M2.5 3.5h11v7h-6l-3 2.5v-2.5h-2z" />
    </svg>
  );
}

export function TerminalIcon() {
  return (
    <svg {...props}>
      <rect x="1.5" y="2.5" width="13" height="11" rx="1.5" />
      <path d="M4.5 6l2 2-2 2M8.5 10.5h3" />
    </svg>
  );
}

/** A web page with a chart on it: the Artifacts page */
export function ArtifactIcon() {
  return (
    <svg {...props}>
      <rect x="2" y="2.5" width="12" height="11" rx="1" />
      <path d="M2 5.5h12M5 11.5V9M8 11.5V7.5M11 11.5V8.5" />
    </svg>
  );
}

export function AppsIcon() {
  return (
    <svg {...props}>
      <rect x="2.5" y="2.5" width="4.5" height="4.5" />
      <rect x="9" y="2.5" width="4.5" height="4.5" />
      <rect x="2.5" y="9" width="4.5" height="4.5" />
      <path d="M11.25 9v4.5M9 11.25h4.5" />
    </svg>
  );
}

export function NoteIcon() {
  return (
    <svg {...props}>
      <path d="M3 2.5h10v8l-3 3H3z" />
      <path d="M10 13.5v-3h3M5.5 5.5h5M5.5 8h5" />
    </svg>
  );
}

export function DocumentIcon() {
  return (
    <svg {...props}>
      <path d="M4 1.5h5.5l3 3v10H4z" />
      <path d="M9.5 1.5v3h3M6 8h4.5M6 10.5h4.5" />
    </svg>
  );
}

export function MemoryIcon() {
  return (
    <svg {...props}>
      <path d="M8 1.5l1.4 3.6 3.6 1.4-3.6 1.4L8 11.5 6.6 7.9 3 6.5l3.6-1.4z" />
      <path d="M12.5 11v3M11 12.5h3" />
    </svg>
  );
}

export function ChartIcon() {
  return (
    <svg {...props}>
      <path d="M2.5 13.5h11M4.5 11V7.5M8 11V4M11.5 11V9" />
    </svg>
  );
}

export function ChevronIcon({ open }: { open: boolean }) {
  return (
    <svg {...props} style={{ transform: open ? "rotate(90deg)" : undefined, transition: "transform .15s ease" }}>
      <path d="M6 4l4 4-4 4" />
    </svg>
  );
}

export function PaperclipIcon() {
  return (
    <svg {...props}>
      <path d="M13.5 7.5l-5.3 5.3a3.2 3.2 0 0 1-4.5-4.5l5.6-5.6a2.1 2.1 0 0 1 3 3l-5.6 5.6a1 1 0 0 1-1.5-1.5L10.4 4.6" />
    </svg>
  );
}

export function SendIcon() {
  return (
    <svg {...props} strokeWidth={2}>
      <path d="M8 13V3M3.5 7.5L8 3l4.5 4.5" />
    </svg>
  );
}

export function PauseIcon() {
  return (
    <svg {...props} stroke="none" fill="currentColor">
      <rect x="4" y="3.5" width="2.8" height="9" rx="1" />
      <rect x="9.2" y="3.5" width="2.8" height="9" rx="1" />
    </svg>
  );
}

export function PlayIcon() {
  return (
    <svg {...props} stroke="none" fill="currentColor">
      <path d="M5 3.3v9.4a.6.6 0 0 0 .9.5l7.4-4.7a.6.6 0 0 0 0-1L5.9 2.8a.6.6 0 0 0-.9.5z" />
    </svg>
  );
}

export function StopIcon() {
  return (
    <svg {...props} stroke="none" fill="currentColor">
      <rect x="4" y="4" width="8" height="8" rx="1.5" />
    </svg>
  );
}

export function CursorIcon() {
  return (
    <svg {...props}>
      <path d="M3 2.5l9.5 4.2-4 1.3-1.3 4z" />
      <path d="M8.8 8.3l3.7 3.7" />
    </svg>
  );
}

export function GlobeIcon() {
  return (
    <svg {...props}>
      <circle cx="8" cy="8" r="6.25" />
      <path d="M1.75 8h12.5M8 1.75c-3.25 3.5-3.25 9 0 12.5M8 1.75c3.25 3.5 3.25 9 0 12.5" />
    </svg>
  );
}

export function MicIcon() {
  return (
    <svg {...props}>
      <rect x="5.75" y="1.75" width="4.5" height="8" rx="2.25" />
      <path d="M3.5 7.5a4.5 4.5 0 0 0 9 0M8 12v2.25" />
    </svg>
  );
}

export function SpeakerIcon() {
  return (
    <svg {...props}>
      <path d="M2.5 6h2.5l3.5-3v10L5 10H2.5z" />
      <path d="M11 5.75a3 3 0 0 1 0 4.5M12.75 3.75a5.75 5.75 0 0 1 0 8.5" />
    </svg>
  );
}

export function VoiceIcon() {
  return (
    <svg {...props}>
      <path d="M3 7v2M5.5 5v6M8 2.5v11M10.5 5v6M13 7v2" />
    </svg>
  );
}

export function ChevronDownIcon() {
  return (
    <svg {...props}>
      <path d="M4 6l4 4 4-4" />
    </svg>
  );
}

export function CheckIcon() {
  return (
    <svg {...props} strokeWidth={2}>
      <path d="M3.5 8.5l3 3 6-7" />
    </svg>
  );
}

/** A handle to drag a row by: two columns of dots */
export function GripIcon() {
  return (
    <svg {...props} fill="currentColor" stroke="none">
      {[4, 8, 12].flatMap(y => [6, 10].map(x => <circle key={`${x}-${y}`} cx={x} cy={y} r="1.1" />))}
    </svg>
  );
}

export function EyeOffIcon() {
  return (
    <svg {...props}>
      <path d="M2 2l12 12M6.6 3.7A6.6 6.6 0 0 1 8 3.5c4.1 0 6.5 4.5 6.5 4.5a11 11 0 0 1-1.9 2.4M10 11.9a5.6 5.6 0 0 1-2 .6C3.9 12.5 1.5 8 1.5 8a11 11 0 0 1 2.4-2.9" />
      <path d="M6.6 6.6a2 2 0 0 0 2.8 2.8" />
    </svg>
  );
}

export function EyeIcon() {
  return (
    <svg {...props}>
      <path d="M1.5 8s2.4-4.5 6.5-4.5S14.5 8 14.5 8 12.1 12.5 8 12.5 1.5 8 1.5 8z" />
      <circle cx="8" cy="8" r="2" />
    </svg>
  );
}

export function FileIcon() {
  return (
    <svg {...props}>
      <path d="M4 1.5h5.5l3 3v10H4z" />
      <path d="M9.5 1.5v3h3" />
    </svg>
  );
}

export function AgentIcon() {
  return (
    <svg {...props}>
      <rect x="3" y="5" width="10" height="8" rx="2.5" />
      <path d="M8 2.5V5M6 9h.01M10 9h.01M1.5 9v1.5M14.5 9v1.5" />
    </svg>
  );
}

/** Studio: steps connected into a workflow */
export function EngineIcon() {
  return (
    <svg {...props}>
      <circle cx="8" cy="8" r="2.2" />
      <path d="M8 1.5v2.3M8 12.2v2.3M1.5 8h2.3M12.2 8h2.3M3.4 3.4 5 5M11 11l1.6 1.6M12.6 3.4 11 5M5 11l-1.6 1.6" />
    </svg>
  );
}

export function StudioIcon() {
  return (
    <svg {...props}>
      <rect x="1.5" y="2.5" width="5" height="4" rx="1" />
      <rect x="9.5" y="9.5" width="5" height="4" rx="1" />
      <path d="M6.5 4.5h2.5a1.5 1.5 0 0 1 1.5 1.5v3.5" />
    </svg>
  );
}

export function PcIcon() {
  return (
    <svg {...props}>
      <rect x="2.25" y="3" width="11.5" height="7.5" rx="1" />
      <path d="M6 13.5h4M8 10.5v3" />
    </svg>
  );
}

/** A server: what runs when the chat doesn't run on a PC */
export function ServerIcon() {
  return (
    <svg {...props}>
      <rect x="2.25" y="2.5" width="11.5" height="4.5" rx="1" />
      <rect x="2.25" y="9" width="11.5" height="4.5" rx="1" />
      <path d="M4.5 4.75h.01M4.5 11.25h.01" />
    </svg>
  );
}

export function ClockIcon() {
  return (
    <svg {...props}>
      <circle cx="8" cy="8" r="5.75" />
      <path d="M8 4.75V8l2.25 1.5" />
    </svg>
  );
}

export function BellIcon() {
  return (
    <svg {...props}>
      <path d="M4 11.5V7a4 4 0 0 1 8 0v4.5l1 1H3z" />
      <path d="M6.5 14a1.6 1.6 0 0 0 3 0" />
    </svg>
  );
}

export function SkillIcon() {
  return (
    <svg {...props}>
      <path d="M3.5 2.5h6l3 3v8h-9z" />
      <path d="M9.5 2.5v3h3M6 9.5l1.5 1.5L10.5 8" />
    </svg>
  );
}

/** labb's mark: a lab flask that's also a lightbulb, its filament hanging above the liquid. The
 * flask takes the color of the text around it (the accent, in the brand); its liquid and filament
 * are --logo-inner */
export function Logo({ size = 18 }: { size?: number }) {
  return (
    <svg className="logo" width={size} height={size} viewBox="0 0 64 64" aria-hidden="true">
      <path d="M23 3 H41 V21 L58 54 A6 6 0 0 1 52.7 62 H11.3 A6 6 0 0 1 6 54 L23 21Z" fill="currentColor" />
      <path d="M15.5 40 H48.5 L54.5 52.5 A3 3 0 0 1 51.8 57 H12.2 A3 3 0 0 1 9.5 52.5Z" style={{ fill: "var(--logo-inner)" }} />
      <path
        d="M29 11 V19 L23 31 L26 26 L29 31 L32 26 L35 31 L38 26 L41 31 L35 19 V11"
        fill="none"
        strokeWidth={3.2}
        strokeLinejoin="round"
        strokeLinecap="round"
        style={{ stroke: "var(--logo-inner)" }}
      />
    </svg>
  );
}

/** "labb" with its l and first b picked out: lab + lightbulb */
export function Wordmark() {
  return (
    <span className="wordmark">
      <span className="hl">l</span>a<span className="hl">b</span>b
    </span>
  );
}

/** The paths of each kind of Studio step's icon, in the same 16×16 line style */
const STEP_PATHS: Record<string, React.ReactNode> = {
  trigger: <path d="M5 3.25v9.5L12.5 8z" />,
  agent: (
    <>
      <rect x="3" y="5" width="10" height="8" rx="2.5" />
      <path d="M8 2.5V5M6 9h.01M10 9h.01M1.5 9v1.5M14.5 9v1.5" />
    </>
  ),
  prompt: <path d="M8 1.75 9.4 6.6 14.25 8 9.4 9.4 8 14.25 6.6 9.4 1.75 8 6.6 6.6z" />,
  skill: <path d="M3.5 2.5h6l3 3v8h-9zM9.5 2.5v3h3M6 9.5l1.5 1.5L10.5 8" />,
  tool: <path d="M10.5 2a3.25 3.25 0 0 0-3 4.5L2.25 11.75l2 2L9.5 8.5a3.25 3.25 0 0 0 4.5-3l-2 1.25-1.75-1-.25-2z" />,
  action: <path d="M9.25 1.5 3.5 9h4.25l-1 5.5L12.5 7H8.25z" />,
  python: <path d="M5.5 4.5 2 8l3.5 3.5M10.5 4.5 14 8l-3.5 3.5M9 3 7 13" />,
  condition: <path d="M2 8h3.5M5.5 8 9 4h5M5.5 8 9 12h5M12 2l2 2-2 2M12 10l2 2-2 2" />,
  foreach: <path d="M2.75 7.5a5 5 0 0 1 9-3M13.25 8.5a5 5 0 0 1-9 3M11.75 1.75v2.75H9M4.25 14.25V11.5H7" />,
  merge: <path d="M3 2.5c0 4 5 4 5 7v4M13 2.5c0 4-5 4-5 7M5.75 11.25 8 13.5l2.25-2.25" />,
  approval: (
    <>
      <circle cx="8" cy="8" r="5.75" />
      <path d="M5.5 8.25 7.25 10l3.25-3.75" />
    </>
  ),
  wait: <path d="M4 1.75h8M4 14.25h8M5 1.75c0 3.5 6 3.5 6 6.25S5 10.75 5 14.25M11 1.75c0 3.5-6 3.5-6 6.25s6 2.75 6 6.25" />,
  transform: <path d="M2 4.5h3.5l5 7H14M2 11.5h3.5l1.5-2M9 6.5l1.5-2H14M12 2.5l2 2-2 2M12 9.5l2 2-2 2" />,
  chat: <path d="M2.5 3h11v7.5h-6l-3.25 2.75v-2.75H2.5z" />,
  document: <path d="M4 1.5h5.5l3 3v10H4zM9.5 1.5v3h3M6 8h4.5M6 10.5h4.5" />,
  page: <path d="M2 2.5h12v11H2zM2 5.5h12M5 11.5V9M8 11.5V7.5M11 11.5V8.5" />,
  respond: <path d="M3.5 14.25V2.25M3.5 2.75h8.5l-2 3 2 3H3.5" />,
  transcribe: (
    <>
      <rect x="2.5" y="1.75" width="4" height="7" rx="2" />
      <path d="M1.5 6.5a3 3 0 0 0 6 0M4.5 9.5v2M9.5 5h5M9.5 8h5M9.5 11h3.5" />
    </>
  ),
  speak: (
    <>
      <path d="M2 6h2.5l3.5-3v10L4.5 10H2z" />
      <path d="M10.5 5.75a3 3 0 0 1 0 4.5M12.25 3.75a5.75 5.75 0 0 1 0 8.5" />
    </>
  ),
};

/** A Studio step's kind, as a line icon (the same everywhere, unlike emoji) */
export function StepIcon({ type, size = 14 }: { type: string; size?: number }) {
  return (
    <svg {...props} width={size} height={size} className={`step-kind-icon kind-${type}`}>
      {STEP_PATHS[type] ?? <circle cx="8" cy="8" r="2" />}
    </svg>
  );
}

export function ExternalIcon() {
  return (
    <svg {...props}>
      <path d="M9 2.5h4.5V7M13.5 2.5L7.5 8.5M11.5 9.5v3a1 1 0 0 1-1 1h-7a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1h3" />
    </svg>
  );
}

export function HammerIcon() {
  return (
    <svg {...props}>
      <path d="M8.5 6.5l-6 6 1 1 6-6M7 4l2-2 2.5.5L14 5l-2 2-1.5-1-1.5 1.5L7.5 6z" />
    </svg>
  );
}

export function FolderIcon() {
  return (
    <svg {...props}>
      <path d="M1.75 4.25a1 1 0 0 1 1-1h3l1.5 1.5h6a1 1 0 0 1 1 1v6.5a1 1 0 0 1-1 1h-10.5a1 1 0 0 1-1-1z" />
    </svg>
  );
}

export function TrashIcon() {
  return (
    <svg {...props}>
      <path d="M2.5 4.5h11M6 4.5V3a1 1 0 0 1 1-1h2a1 1 0 0 1 1 1v1.5M4 4.5l.7 8.6a1 1 0 0 0 1 .9h4.6a1 1 0 0 0 1-.9l.7-8.6" />
    </svg>
  );
}

export function DatabaseIcon() {
  return (
    <svg {...props}>
      <ellipse cx="8" cy="3.75" rx="5.25" ry="1.75" />
      <path d="M2.75 3.75v8.5c0 .97 2.35 1.75 5.25 1.75s5.25-.78 5.25-1.75v-8.5M2.75 8c0 .97 2.35 1.75 5.25 1.75S13.25 8.97 13.25 8" />
    </svg>
  );
}

export function UsersIcon() {
  return (
    <svg {...props}>
      <circle cx="6" cy="5.5" r="2.5" />
      <path d="M1.75 13.5c0-2.35 1.9-4 4.25-4s4.25 1.65 4.25 4M10.5 3.2a2.5 2.5 0 0 1 0 4.6M12 9.8c1.3.5 2.25 1.85 2.25 3.7" />
    </svg>
  );
}
