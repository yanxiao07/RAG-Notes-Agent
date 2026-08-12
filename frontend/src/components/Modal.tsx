import type { PropsWithChildren } from "react";
import { X } from "lucide-react";

type ModalProps = PropsWithChildren<{
  className?: string;
  title: string;
  onClose: () => void;
}>;

export function Modal({ children, className, title, onClose }: ModalProps) {
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        aria-labelledby="modal-title"
        aria-modal="true"
        className={className ? `modal ${className}` : "modal"}
        onMouseDown={(event) => event.stopPropagation()}
        role="dialog"
      >
        <header className="modal-header">
          <h2 id="modal-title">{title}</h2>
          <button
            aria-label="关闭弹窗"
            className="icon-button"
            onClick={onClose}
            title="关闭"
            type="button"
          >
            <X size={18} />
          </button>
        </header>
        {children}
      </section>
    </div>
  );
}
