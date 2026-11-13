# labb mobile

Mobile apps for labb.

## iOS

`ios/` is labb's iOS app (SwiftUI, iOS 18+, Swift 6, built with Xcode 26): the web chat's features with iOS patterns (tabs, navigation stacks, lists, sheets, swipe actions) rather than the web's layout. It talks to a labb server like the web app does. On first start it asks for the server's address (or an invite link, which also opens the invite's sign-up), then goes through the same sign-in: first-run setup, an invite or a password.

Tabs:
- **Chats**: chats, pinned ones and your PCs' sessions. Replies stream, and a broken connection joins the reply again. Also approvals and questions, attachments (photos and text files), and editing or writing a reply again. Each chat's menu has model, answer mode, Web, sharing and export.
- **Agents**: the list, an agent's page, and its editor.
- **Tasks**: schedules built from pickers, previewed by the server, plus runs.
- **Library**: documents, memories, notes and skills.
- **More**: PCs, Studio (runs, approvals, starting a workflow), engines, usage, settings (password, API keys, device sign-in, integrations) and, for admins, the server (users, invites, health, limits).

```bash
open ios/Labb.xcodeproj    # then pick a simulator and Run
```

Or from the terminal:

```bash
cd ios && xcodebuild -scheme Labb -destination 'generic/platform=iOS Simulator' build
```

The project uses synchronized folders, so files added under `ios/Labb/` are part of the app without editing the project. To run on a device, choose your team under Signing & Capabilities.

### How it's built

`ios/Labb/` (a synchronized folder: files added there are in the app without editing the project):
- `Core/`: `APIClient` (`API.swift`), the shared state, the models and the chat engine.
  - `APIClient`: the session cookie, `X-Labb: 1` on writes, errors from `detail`, and NDJSON events from `/chat`.
  - `AppModel`: the phase (server → sign in → ready), the saved server and the shared lists.
  - `Models.swift`: the server's JSON, with explicit snake_case keys.
  - `ChatEvent.swift`: the events and a port of the web's `turnsReducer`.
  - `ChatSession`: one chat's turns, sending, stopping and rejoining a reply through `/chat/live` / `/pcs/live`, as `useChat` does.
- `Features/<tab>/`: the screens.
- `Support/`: formatting, errors and the Markdown renderer.

When the server's API changes, change the app with it, as with `frontend/web/`.

Plain `http://` works for `localhost` / `127.0.0.1` (the simulator). Anything else should be https: App Transport Security blocks plain http to other hosts.

### Testing

`LabbUITests` walks through the app against a real server (signs up on a fresh one, then a chat, an agent, a task, a memory, usage and an invite), saving a screenshot at each step. Without a server it's skipped.

```bash
cd ios && TEST_RUNNER_LABB_SERVER=http://127.0.0.1:8000 xcodebuild test -scheme LabbUITests \
    -destination 'platform=iOS Simulator,name=iPhone 17 Pro'
```

The screenshots are in the result bundle (`xcrun xcresulttool export attachments`). The server needs a model that answers. Any model works, and the walkthrough waits for a reply containing "Lisbon in three days", so a stand-in OpenAI-compatible server returning a fixed reply keeps it quick.

### Its look: the design system

The app uses `LabbDesign`, the design system's Swift package, as a local package (`../../frontend/design-system/swift`), so it builds wherever the repository is checked out. Use its colours and tokens rather than literal values: `.labbRoot()` on the root view, `.foregroundStyle(.labb.text)`, `Color.labb.surface`, `LabbSpace.s8`, `.labbText(.base)`, `.buttonStyle(.labb(.primary))`, `.toggleStyle(.labb)`. Its colours follow light and dark by themselves.

The package's tokens are generated from the design system's JSON: after changing a token, run `npm run tokens` in `frontend/` and rebuild. See the design system docs' iOS page, or `frontend/design-system/AGENTS.md`, **The Swift package**.
