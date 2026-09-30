// Inline SVG icons for the chat. 24px grid, stroke = currentColor, so CSS sets the colour.
const base = {
  width: 20, height: 20, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor',
  strokeWidth: 1.8, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true,
}

export const Sparkle = (p) => (
  <svg {...base} {...p}>
    <path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z" fill="currentColor" stroke="none" />
    <path d="M19 15.5l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z" fill="currentColor" stroke="none" />
  </svg>
)
export const Send = (p) => (
  <svg {...base} {...p}><path d="M12 19V5M5.5 11.5L12 5l6.5 6.5" /></svg>
)
export const Plus = (p) => (
  <svg {...base} {...p}><path d="M12 5v14M5 12h14" /></svg>
)
export const History = (p) => (
  <svg {...base} {...p}><path d="M3 12a9 9 0 1 0 3-6.7L3 8" /><path d="M3 3v5h5M12 7v5l3 2" /></svg>
)
export const Expand = (p) => (
  <svg {...base} {...p}><path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" /></svg>
)
export const Shrink = (p) => (
  <svg {...base} {...p}><path d="M4 14h6v6M20 10h-6V4M14 10l7-7M3 21l7-7" /></svg>
)
export const Close = (p) => (
  <svg {...base} {...p}><path d="M6 6l12 12M18 6L6 18" /></svg>
)
export const Doc = (p) => (
  <svg {...base} {...p}><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5M9 13h6M9 17h4" /></svg>
)
export const Search = (p) => (
  <svg {...base} {...p}><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" /></svg>
)
export const Trash = (p) => (
  <svg {...base} {...p}><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" /></svg>
)
export const Chevron = (p) => (
  <svg {...base} {...p}><path d="M9 6l6 6-6 6" /></svg>
)
export const Back = (p) => (
  <svg {...base} {...p}><path d="M15 6l-6 6 6 6" /></svg>
)
