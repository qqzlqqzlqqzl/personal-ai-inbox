import { Modal } from "@arco-design/web-react"
import { IconClose } from "@arco-design/web-react/icon"
import { cloneElement, useLayoutEffect, useRef } from "react"

import "./AccessibleModal.css"

const isFocusableElement = (element) => {
  if (
    !(element instanceof HTMLElement) ||
    element === document.body ||
    element.hasAttribute("disabled") ||
    element.getAttribute("aria-disabled") === "true"
  ) {
    return false
  }

  return element.matches(
    'a[href], button, input, select, textarea, summary, [contenteditable="true"], [tabindex]',
  )
}

const canReceiveFocus = (element) =>
  isFocusableElement(element) && element.isConnected && element.getClientRects().length > 0

const AccessibleModal = ({
  afterClose,
  closeLabel,
  closable = true,
  dialogLabel,
  fallbackFocusSelector,
  returnFocusRef,
  modalRender,
  visible,
  ...props
}) => {
  const restoreFocusRef = useRef(null)
  const wasVisibleRef = useRef(false)

  useLayoutEffect(() => {
    if (visible && !wasVisibleRef.current) {
      const { activeElement } = document
      restoreFocusRef.current = isFocusableElement(activeElement) ? activeElement : null
    }
    wasVisibleRef.current = visible
  }, [visible])

  const handleAfterClose = () => {
    afterClose?.()

    // An explicit opener is captured before child focus locks run. If it was
    // removed, use the supplied fallback instead of a stale in-dialog target.
    const focusCandidates = [returnFocusRef ? returnFocusRef.current : restoreFocusRef.current]
    if (fallbackFocusSelector) {
      focusCandidates.push(...document.querySelectorAll(fallbackFocusSelector))
    }
    focusCandidates.find((element) => canReceiveFocus(element))?.focus({ preventScroll: true })
    restoreFocusRef.current = null
  }

  const renderModal = (modalNode) => {
    const labelledModal = dialogLabel
      ? cloneElement(modalNode, { "aria-label": dialogLabel })
      : modalNode
    return modalRender ? modalRender(labelledModal) : labelledModal
  }

  return (
    <Modal
      autoFocus
      focusLock
      {...props}
      afterClose={handleAfterClose}
      closable={closable}
      modalRender={renderModal}
      visible={visible}
      closeIcon={
        <button aria-label={closeLabel} className="accessible-modal-close-button" type="button">
          <IconClose aria-hidden="true" />
        </button>
      }
    />
  )
}

export default AccessibleModal
