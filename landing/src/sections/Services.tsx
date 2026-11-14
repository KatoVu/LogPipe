import type { ReactNode } from 'react'
import { ButtonLink } from '../components/Button'
import { MailIcon, PhoneIcon, WhatsAppIcon } from '../components/icons'
import { Heading, Lead, Points, Section } from '../components/Section'
import { EMAIL, PHONE, REPO, WHATSAPP, mailPlan } from '../links'

type Plan = { name: string; price: string; per?: string; blurb: string; items: string[]; cta: ReactNode; featured?: boolean }

const PLANS: Plan[] = [
  {
    name: 'Open source',
    price: 'Free',
    blurb: 'Clone it and run it yourself.',
    items: ['Every feature, on your own machines', 'Offline by default. Your logs never leave your network', 'Help through GitHub issues and the docs'],
    cta: <ButtonLink href={REPO} variant="secondary" arrow>View on GitHub</ButtonLink>,
  },
  {
    name: 'Base',
    price: '$30',
    per: '/ month',
    blurb: 'One environment, set up and run by us.',
    items: ['1 environment (e.g. production)', 'Up to 10 services, 2 GB of logs a day', 'Logs kept for 7 days', '200 AI answers a month', 'Email support, reply within 2 business days'],
    cta: <ButtonLink href={mailPlan('Base')} variant="secondary" arrow>Start with Base</ButtonLink>,
  },
  {
    name: 'Advanced',
    price: '$45',
    per: '/ month',
    blurb: 'More room, faster answers, custom work.',
    items: ['Up to 3 environments (production, staging, dev)', 'Up to 50 services, 10 GB of logs a day', 'Logs kept for 30 days', '2,000 AI answers a month on a faster model', 'One custom parser or integration a month', 'Email and WhatsApp support, same-day reply'],
    cta: <ButtonLink href={mailPlan('Advanced')} arrow>Start with Advanced</ButtonLink>,
    featured: true,
  },
]

const PROMISE = ['We host it', 'We install it', 'We fit it to your stack', 'We keep it running and connect it to the pipeline you already have']

/** Free self-hosting next to the two managed plans, with one way to reach us below. */
export function Services() {
  return (
    <Section id="services" route="store" rail="last">
      <header className="grid gap-x-8 gap-y-5 lg:grid-cols-12">
        <div className="lg:col-span-6"><Heading id="services-h">Run it yourself, or let us run it</Heading></div>
        <div className="lg:col-span-5 lg:col-start-8">
          <Lead>logpipe is open source and always will be. If you'd rather not operate it, we'll do it for you. You focus on your business. We handle the boring stuff.</Lead>
        </div>
      </header>

      <ul className="mt-14 grid gap-6 md:mt-20 lg:grid-cols-3">
        {PLANS.map(({ name, price, per, blurb, items, cta, featured }) => (
          <li
            key={name}
            className={`flex flex-col rounded-lg border p-7 sm:p-8 ${featured ? 'border-accent/45 bg-surface shadow-[0_24px_60px_-24px_color-mix(in_oklab,var(--color-accent)_35%,transparent)]' : 'border-line'}`}
          >
            <h3 className="text-xl font-semibold">{name}</h3>
            <p className="mt-4 flex items-baseline gap-2">
              <span className="text-[2.75rem] leading-none font-medium tracking-[-0.04em] tabular-nums">{price}</span>
              {per && <span className="text-muted">{per}</span>}
            </p>
            <p className={`mt-3 mb-7 ${featured ? 'text-ink/75' : 'text-muted'}`}>{blurb}</p>
            <Points items={items} />
            <div className="mt-auto pt-9">{cta}</div>
          </li>
        ))}
      </ul>

      <div className="mt-6 grid gap-6 rounded-lg border border-line p-7 sm:p-8 lg:grid-cols-12 lg:items-center">
        <div className="lg:col-span-7">
          <p className="text-lg font-semibold">Both plans: we handle the boring stuff</p>
          <p className="mt-2 text-muted">{PROMISE.join(' · ')}. Need more? Tell us what you run.</p>
        </div>
        <div className="flex flex-wrap gap-3 lg:col-span-5 lg:justify-end">
          <ButtonLink href={EMAIL}><MailIcon className="size-4" />Email us</ButtonLink>
          <ButtonLink href={WHATSAPP} target="_blank" rel="noopener noreferrer" variant="secondary"><WhatsAppIcon className="size-4" />WhatsApp</ButtonLink>
          <ButtonLink href={PHONE} variant="secondary"><PhoneIcon className="size-4" />+64 923266008</ButtonLink>
        </div>
      </div>
    </Section>
  )
}
