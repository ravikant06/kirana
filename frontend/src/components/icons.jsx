// Stroke icons for the shop UI (24px grid, currentColor). The chat dock has its own set.
const base = {
  width: 20, height: 20, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor',
  strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true,
}
const I = (paths) => function Icon({ size = 20, ...rest }) {
  return <svg {...base} width={size} height={size} {...rest}>{paths}</svg>
}

export const SearchIcon = I(<><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></>)
export const CartIcon = I(<><circle cx="9" cy="20" r="1.4" /><circle cx="18" cy="20" r="1.4" /><path d="M2.5 3h2.6l2.4 11.6a1.6 1.6 0 0 0 1.6 1.3h8.6a1.6 1.6 0 0 0 1.6-1.2L21.5 7H6.2" /></>)
export const BagIcon = I(<><path d="M5 8h14l-1.2 11.2a2 2 0 0 1-2 1.8H8.2a2 2 0 0 1-2-1.8Z" /><path d="M9 10V6a3 3 0 0 1 6 0v4" /></>)
export const UserIcon = I(<><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></>)
export const HomeIcon = I(<><path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1Z" /></>)
export const BoxIcon = I(<><path d="M21 8 12 3 3 8v8l9 5 9-5Z" /><path d="m3 8 9 5 9-5M12 13v8" /></>)
export const StoreIcon = I(<><path d="M3 9 4.5 4h15L21 9" /><path d="M3 9a3 3 0 0 0 6 0 3 3 0 0 0 6 0 3 3 0 0 0 6 0" /><path d="M5 12v8h14v-8" /><path d="M10 20v-5h4v5" /></>)
export const BoltIcon = I(<path d="M13 2 4 14h7l-1 8 9-12h-7Z" />)
export const CodeIcon = I(<><path d="m8 7-5 5 5 5M16 7l5 5-5 5" /></>)
export const ChevronDown = I(<path d="m6 9 6 6 6-6" />)
export const ChevronRight = I(<path d="m9 6 6 6-6 6" />)
export const ChevronLeft = I(<path d="m15 6-6 6 6 6" />)
export const XIcon = I(<path d="M18 6 6 18M6 6l12 12" />)
export const CheckIcon = I(<path d="m5 12 5 5 9-10" />)
export const ShieldIcon = I(<><path d="M12 3 4 6v6c0 4.5 3.4 8.3 8 9 4.6-.7 8-4.5 8-9V6Z" /><path d="m9 12 2 2 4-4" /></>)
export const ClockIcon = I(<><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>)
export const TruckIcon = I(<><path d="M3 6h11v10H3zM14 10h4l3 3v3h-7" /><circle cx="7" cy="18" r="1.8" /><circle cx="17.5" cy="18" r="1.8" /></>)
export const TrashIcon = I(<><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" /></>)
export const PlusIcon = I(<path d="M12 5v14M5 12h14" />)
export const MinusIcon = I(<path d="M5 12h14" />)
export const AlertIcon = I(<><circle cx="12" cy="12" r="9" /><path d="M12 8v5M12 16.5v.01" /></>)
export const SparkIcon = I(<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6" />)
export const GaugeIcon = I(<><path d="M4 18a9 9 0 1 1 16 0" /><path d="m12 14 4-5" /><circle cx="12" cy="14" r="1.6" /></>)
