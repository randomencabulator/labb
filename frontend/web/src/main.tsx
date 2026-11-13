import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { AuthGate } from "./components/AuthPages";
import { BrowserLive } from "./components/BrowserLive";
import { segments } from "./router";
import "./styles.css";

// /browser/<thread id>: a chat's browser on a page of its own, for taking over from the terminal
const [root, browserThread] = segments(window.location.pathname);
const showBrowser = root === "browser" && browserThread;

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AuthGate>
      {/* A new App for each user, so nothing of the last one's chats stays */}
      {(user, onLogOut) => (showBrowser ? <div className="browser-page"><BrowserLive threadId={browserThread} interactive /></div> : <App key={user.id} user={user} onLogOut={onLogOut} />)}
    </AuthGate>
  </StrictMode>,
);
