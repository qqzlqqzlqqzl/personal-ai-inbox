export const initialStatusPopover = { hovered: false, focused: false, pinned: false, dismissed: false }

export const isStatusPopoverOpen = ({ hovered, focused, pinned, dismissed }) =>
  pinned || (!dismissed && (hovered || focused))

export function updateStatusPopover(state, action) {
  switch (action) {
    case "enter": return { ...state, hovered: true, dismissed: false }
    case "leave": return { ...state, hovered: false }
    case "focus": return { ...state, focused: true, dismissed: false }
    case "blur": return { ...state, focused: false, pinned: false }
    case "toggle": return isStatusPopoverOpen(state)
      ? { ...state, pinned: false, dismissed: true }
      : { ...state, pinned: true, dismissed: false }
    case "dismiss": return { ...state, pinned: false, dismissed: true }
    default: return state
  }
}
