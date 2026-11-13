"use client";

import type { ReactElement, ReactNode } from "react";
import { Dialog as BaseDialog } from "@base-ui/react/dialog";
import { CloseIcon } from "../icons/CloseIcon";

// A dialog over the page, opened by its trigger or by `open`; Escape, a click outside and
// DialogClose close it, and focus stays inside while it's open
export function Dialog({
  trigger,
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
}: {
  trigger?: ReactElement;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  title: ReactNode;
  description?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <BaseDialog.Root open={open} onOpenChange={onOpenChange ? (next) => onOpenChange(next) : undefined}>
      {trigger && <BaseDialog.Trigger render={trigger} />}
      <BaseDialog.Portal>
        <BaseDialog.Backdrop className="lb-backdrop" />
        <BaseDialog.Popup className="lb-dialog">
          <div className="lb-dialog-head">
            <BaseDialog.Title className="lb-dialog-title">{title}</BaseDialog.Title>
            <BaseDialog.Close className="lb-button ghost icon" aria-label="Close">
              <CloseIcon />
            </BaseDialog.Close>
          </div>
          <div className="lb-dialog-body">
            {description != null && <BaseDialog.Description className="lb-dialog-description">{description}</BaseDialog.Description>}
            {children}
          </div>
          {footer != null && <div className="lb-dialog-foot">{footer}</div>}
        </BaseDialog.Popup>
      </BaseDialog.Portal>
    </BaseDialog.Root>
  );
}
