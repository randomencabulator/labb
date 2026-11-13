# React app

`frontend/` is a React page, built with Vite and served as files. A change to its files is built and
served by itself a moment later: there's nothing to deploy. Any path that isn't a file gets
`index.html`, so the page can have addresses of its own. Start in `frontend/src/App.jsx`.

The page is built with [shadcn/ui](https://ui.shadcn.com) on Tailwind CSS: its components are in
`frontend/src/components/ui/` (button, card, input, select, dialog, tabs, table, sonner toasts and
more), as files of the app's own to use and change. More come from shadcn's site (`components.json`
is set up for its CLI). The look picked when the app was made is on `<html>` in `frontend/index.html`:
`data-color` (neutral, blue, emerald, violet, amber), `data-style` (compact, balanced, roomy: spacing,
corners and shadows) and `data-font` (geist, inter, jakarta, lora, mono); `frontend/src/themes.css`
has them all, so changing an attribute changes the whole app. Dark mode follows the system. `frontend/AGENTS.md` tells coding agents how the page is set up, its components and the rules for using the theme.

To use packages in the page, add them to `frontend/package.json` with exact versions: the build runs
`npm install` (there's no lockfile, since it can only be made by running npm). It has no server of
its own: for an API and a database, start from the Python or Node starter instead, or add a
`backend/` like theirs.
