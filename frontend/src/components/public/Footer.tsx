import React from 'react'

const Footer: React.FC = () => (
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
)

export default Footer
