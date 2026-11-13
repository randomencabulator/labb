"use client";

import type { ReactNode } from "react";
import { Toast as BaseToast } from "@base-ui/react/toast";
import { cx } from "../../utils";
import { CloseIcon } from "../icons/CloseIcon";

// Put one around the app, then useToast() anywhere under it
export function ToastProvider({ children, limit = 3, timeout = 5000 }: { children: ReactNode; limit?: number; timeout?: number }) {
  return (
    <BaseToast.Provider limit={limit} timeout={timeout}>
      {children}
      <BaseToast.Portal>
        <BaseToast.Viewport className="lb-toasts">
          <Toasts />
        </BaseToast.Viewport>
      </BaseToast.Portal>
    </BaseToast.Provider>
  );
}

function Toasts() {
  const { toasts } = BaseToast.useToastManager();
  return toasts.map((toast) => (
    <BaseToast.Root key={toast.id} toast={toast} className={cx("lb-toast", toast.type === "error" && "error")}>
      <BaseToast.Content className="lb-toast-content">
        {toast.title != null && <BaseToast.Title className="lb-toast-title" />}
        <BaseToast.Description className="lb-toast-description" />
      </BaseToast.Content>
      <BaseToast.Close className="lb-toast-close" aria-label="Dismiss">
        <CloseIcon />
      </BaseToast.Close>
    </BaseToast.Root>
  ));
}
