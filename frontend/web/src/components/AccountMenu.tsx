import { useEffect, useRef, useState } from "react";
import type { User } from "../api";
import { THEMES, type Theme } from "../theme";
import { GearIcon, LogOutIcon } from "./Icons";

const THEME_NAMES: Record<Theme, string> = { system: "System", light: "Light", dark: "Dark" };

type Props = {
  user: User;
  theme: Theme;
  onTheme: (theme: Theme) => void;
  onSettings: () => void;
  onLogOut: () => void;
};

/** You, at the bottom of the sidebar: who you are, Settings, the theme, and logging out (the menu opens upwards) */
export function AccountMenu({ user, theme, onTheme, onSettings, onLogOut }: Props) {
  const [open, setOpen] = useState(false);
  const menu = useRef<HTMLDivElement>(null);

  // Close on a click elsewhere, or Esc
  useEffect(() => {
    if (!open) return;
    const onClick = (event: MouseEvent) => {
      if (!menu.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && setOpen(false);
    window.addEventListener("mousedown", onClick);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onClick);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const choose = (action: () => void) => () => {
    setOpen(false);
    action();
  };

  return (
    <div className="account-menu" ref={menu}>
      <button
        type="button"
        className="account-button"
        aria-label={`Account: ${user.username}`}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <span className="avatar">{user.username.slice(0, 1).toUpperCase()}</span>
        <span className="who">
          <b>{user.username}</b>
          <span>{user.role}</span>
        </span>
        <GearIcon />
      </button>
      {open && (
        <div className="menu up" role="menu">
          <button type="button" role="menuitem" onClick={choose(onSettings)}>
            <GearIcon /> Settings
          </button>
          <div className="menu-theme" role="group" aria-label="Theme">
            {THEMES.map(choice => (
              <button
                key={choice}
                type="button"
                role="menuitemradio"
                aria-checked={theme === choice}
                className={theme === choice ? "on" : undefined}
                onClick={() => onTheme(choice)}
              >
                {THEME_NAMES[choice]}
              </button>
            ))}
          </div>
          <button type="button" role="menuitem" onClick={choose(onLogOut)}>
            <LogOutIcon /> Log out
          </button>
        </div>
      )}
    </div>
  );
}
