import type { Metadata } from "next";
import type { ReactNode } from "react";
import { tokensCss } from "@labb/tokens/css";
import { ToastProvider } from "@labb/ui";
import "@labb/ui/styles.css";
import "./docs.css";
import { Nav } from "@/lib/Nav";

export const metadata: Metadata = {
  title: { default: "labb design system", template: "%s · labb design system" },
  description: "labb's colours, tokens and React components.",
};

// Sets the saved theme before the page paints, so it doesn't flash
const themeScript = `try{var t=localStorage.getItem("lb-theme");if(t==="light"||t==="dark")document.documentElement.dataset.theme=t}catch(e){}`;

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
        <style dangerouslySetInnerHTML={{ __html: tokensCss() }} />
      </head>
      <body className="lb-root">
        <ToastProvider>
          <div className="docs">
            <Nav />
            <main className="docs-main">{children}</main>
          </div>
        </ToastProvider>
      </body>
    </html>
  );
}
