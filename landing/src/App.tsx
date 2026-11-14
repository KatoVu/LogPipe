import { Ask, Detect, Explain, Parse } from './sections/Features'
import { Footer } from './sections/Footer'
import { Hero } from './sections/Hero'
import { Integrations } from './sections/Integrations'
import { Learn } from './sections/Learn'
import { Nav } from './sections/Nav'
import { Services } from './sections/Services'

export default function App() {
  return (
    <>
      <Nav />
      <main id="top">
        <Hero />
        <Parse />
        <Learn />
        <Detect />
        <Explain />
        <Ask />
        <Integrations />
        <Services />
      </main>
      <Footer />
    </>
  )
}
