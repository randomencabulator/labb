import type { Metadata } from "next";
import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";
import "./globals.css";

// ?theme=dark or ?theme=light picks the colours (labb's chat shows the page in its own theme);
// set before the page paints, so it doesn't flash the other one
const theme = `try{var t=new URLSearchParams(location.search).get("theme");if(t==="dark"||t==="light")document.documentElement.dataset.theme=t}catch(e){}`;

// The page's title and description: change them for what it is
export const metadata: Metadata = {
  title: "Untitled",
  description: "Made with labb",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${GeistSans.variable} ${GeistMono.variable}`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: theme }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
