import type { SVGProps } from 'react'

type IconProps = SVGProps<SVGSVGElement>

const stroke = {
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.5,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  'aria-hidden': true,
} as const

export const ArrowIcon = (p: IconProps) => (
  <svg viewBox="0 0 12 12" {...stroke} {...p}><path d="M1 6h8M4.5 2.5 8 6l-3.5 3.5" /></svg>
)

export const ExpandIcon = (p: IconProps) => (
  <svg viewBox="0 0 16 16" {...stroke} {...p}><path d="M9.5 2.5h4v4M13.5 2.5 8.5 7.5M6.5 13.5h-4v-4M2.5 13.5l5-5" /></svg>
)

export const MenuIcon = ({ open, ...p }: IconProps & { open: boolean }) => (
  <svg viewBox="0 0 18 18" {...stroke} strokeWidth={1.6} {...p}>
    <path d={open ? 'M4 4l10 10M14 4 4 14' : 'M2 5h14M2 13h14'} />
  </svg>
)

export const PlayIcon = (p: IconProps) => (
  <svg viewBox="0 0 16 16" fill="currentColor" aria-hidden {...p}>
    <path d="M5.25 3.7v8.6c0 .62.68 1 1.2.66l6.5-4.3c.47-.31.47-1 0-1.32l-6.5-4.3c-.52-.34-1.2.04-1.2.66Z" />
  </svg>
)

export const PauseIcon = (p: IconProps) => (
  <svg viewBox="0 0 16 16" fill="currentColor" aria-hidden {...p}>
    <rect x="4.25" y="3.25" width="2.75" height="9.5" rx="0.9" />
    <rect x="9" y="3.25" width="2.75" height="9.5" rx="0.9" />
  </svg>
)

export const MailIcon = (p: IconProps) => (
  <svg viewBox="0 0 16 16" {...stroke} {...p}><rect x="2" y="3.5" width="12" height="9" rx="1.5" /><path d="m2.5 4.5 5.5 4 5.5-4" /></svg>
)

export const PhoneIcon = (p: IconProps) => (
  <svg viewBox="0 0 16 16" {...stroke} {...p}><path d="M3.5 2.5h2l1 3-1.5 1a7 7 0 0 0 3.5 3.5l1-1.5 3 1v2a1.5 1.5 0 0 1-1.5 1.5A10.5 10.5 0 0 1 2 4a1.5 1.5 0 0 1 1.5-1.5Z" /></svg>
)

/** A chat bubble with a handset, drawn in the same stroke as the set; reads as WhatsApp beside its label. */
export const WhatsAppIcon = (p: IconProps) => (
  <svg viewBox="0 0 16 16" {...stroke} {...p}>
    <path d="m2.25 13.75.8-2.75a6 6 0 1 1 2.1 2.05Z" />
    <path d="M6.1 5.4h.9l.45 1.3-.6.45a3.3 3.3 0 0 0 1.5 1.5l.45-.6 1.3.45v.9a.7.7 0 0 1-.7.7A4.5 4.5 0 0 1 5.4 6.1a.7.7 0 0 1 .7-.7Z" strokeWidth={1.1} />
  </svg>
)
