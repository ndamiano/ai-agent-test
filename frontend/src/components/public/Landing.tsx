import React from 'react'
import DemoGallery from './DemoGallery'
import { Link } from '../../router'

const Landing: React.FC = () => (
    <div className="min-h-screen bg-ink">
        <header className="border-b border-edge bg-sunken px-6 py-3 flex items-center justify-between">
            <span className="font-display text-lg tracking-wide flex items-center gap-2.5">
                <span className="w-[18px] h-[18px] rotate-45 border-[1.5px] border-ember relative
                                 after:absolute after:inset-[3px] after:bg-ember after:opacity-50" />
                GameSummoner
            </span>
            <Link to="/login" className="text-sm text-slate hover:text-bone transition-colors">
                Sign in
            </Link>
        </header>

        <main className="max-w-5xl mx-auto px-6 py-14 flex flex-col gap-14">
            <section className="text-center flex flex-col items-center gap-4">
                <h1 className="font-display text-4xl md:text-5xl leading-tight max-w-2xl">
                    Describe a game and GameSummoner summons it into reality.
                </h1>
                <p className="text-slate max-w-xl leading-relaxed">
                    The game you imagine — a sentence, a paragraph, however you'd say it to a
                    friend — becomes a real, playable browser game. Art and all.
                </p>
            </section>

            <DemoGallery />
        </main>

        <footer className="border-t border-edge px-6 py-6">
            <div className="max-w-5xl mx-auto flex flex-wrap items-center justify-center gap-x-5 gap-y-2
                            text-xs text-dim">
                <span>© {new Date().getFullYear()} GameSummoner</span>
                <a href="/about.html" className="hover:text-slate transition-colors">About</a>
                <a href="/terms.html" className="hover:text-slate transition-colors">Terms</a>
                <a href="/privacy.html" className="hover:text-slate transition-colors">Privacy</a>
                <a href="mailto:support@gamesummoner.com" className="hover:text-slate transition-colors">
                    support@gamesummoner.com
                </a>
            </div>
        </footer>
    </div>
)

export default Landing
