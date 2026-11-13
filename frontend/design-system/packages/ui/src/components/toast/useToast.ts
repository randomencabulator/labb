"use client";

import type { ReactNode } from "react";
import { Toast as BaseToast } from "@base-ui/react/toast";

// Shows a toast: toast("Copied the link."), or toast("Couldn't reach the server.", { tone: "error" })
export function useToast() {
  const manager = BaseToast.useToastManager();
  return (message: ReactNode, options: { tone?: "default" | "error"; title?: ReactNode; timeout?: number } = {}) =>
    manager.add({
      description: message,
      title: options.title,
      type: options.tone === "error" ? "error" : undefined,
      timeout: options.timeout,
      // Errors interrupt; the rest wait their turn
      priority: options.tone === "error" ? "high" : "low",
    });
}
