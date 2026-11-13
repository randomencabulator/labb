import type { ReactNode } from "react";
import { CloseIcon } from "../icons/CloseIcon";

// A dialog's box drawn in place, without opening anything: for showing one in docs and previews
export function DialogPanel({ title, children, footer }: { title: ReactNode; children: ReactNode; footer?: ReactNode }) {
  return (
    <div className="lb-dialog static">
      <div className="lb-dialog-head">
        <h2 className="lb-dialog-title">{title}</h2>
        <span className="lb-button ghost icon" aria-hidden="true">
          <CloseIcon />
        </span>
      </div>
      <div className="lb-dialog-body">{children}</div>
      {footer != null && <div className="lb-dialog-foot">{footer}</div>}
    </div>
  );
}
