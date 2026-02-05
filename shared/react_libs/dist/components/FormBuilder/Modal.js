import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/**
 * Modal Component
 *
 * Reusable modal wrapper with overlay, close button, and escape key handling.
 * Used by FormBuilder in modal mode.
 */
import { useEffect } from 'react';
export const Modal = ({ isOpen, onClose, title, children, className = '', closeOnOverlayClick = true, showCloseButton = true, }) => {
    useEffect(() => {
        const handleEscape = (e) => {
            if (e.key === 'Escape' && isOpen) {
                onClose();
            }
        };
        document.addEventListener('keydown', handleEscape);
        return () => document.removeEventListener('keydown', handleEscape);
    }, [isOpen, onClose]);
    useEffect(() => {
        if (isOpen) {
            document.body.style.overflow = 'hidden';
        }
        else {
            document.body.style.overflow = '';
        }
        return () => {
            document.body.style.overflow = '';
        };
    }, [isOpen]);
    if (!isOpen)
        return null;
    const handleOverlayClick = (e) => {
        if (closeOnOverlayClick && e.target === e.currentTarget) {
            onClose();
        }
    };
    return (_jsx("div", { className: "fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4", onClick: handleOverlayClick, children: _jsxs("div", { className: `card w-full max-w-2xl max-h-[90vh] overflow-y-auto ${className}`, children: [(title || showCloseButton) && (_jsxs("div", { className: "flex justify-between items-center mb-4", children: [title && _jsx("h2", { className: "text-xl font-bold text-gold-400", children: title }), showCloseButton && (_jsx("button", { type: "button", onClick: onClose, className: "text-gray-400 hover:text-white transition-colors", "aria-label": "Close modal", children: _jsx("svg", { className: "w-6 h-6", fill: "none", stroke: "currentColor", viewBox: "0 0 24 24", children: _jsx("path", { strokeLinecap: "round", strokeLinejoin: "round", strokeWidth: 2, d: "M6 18L18 6M6 6l12 12" }) }) }))] })), children] }) }));
};
//# sourceMappingURL=Modal.js.map