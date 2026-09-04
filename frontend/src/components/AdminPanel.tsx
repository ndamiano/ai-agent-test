import React from 'react'
import { ADMIN_TABS, Link, type AdminTab } from '../router'
import QueuesPage from './admin/QueuesPage'
import CostsPage from './admin/CostsPage'
import UsagePage from './admin/UsagePage'
import ViolationsPage from './admin/ViolationsPage'

const LABEL: Record<AdminTab, string> = {
    queues: 'Queues', costs: 'Costs', usage: 'Usage', violations: 'Violations',
}

const PAGE: Record<AdminTab, React.FC> = {
    queues: QueuesPage, costs: CostsPage, usage: UsagePage, violations: ViolationsPage,
}

const AdminPanel: React.FC<{ tab: AdminTab }> = ({ tab }) => {
    const Page = PAGE[tab]
    return (
        <div className="h-full overflow-y-auto p-6">
            <div className="max-w-4xl mx-auto space-y-4">
                <nav className="flex gap-1 border-b border-edge pb-2">
                    {ADMIN_TABS.map(t => (
                        <Link key={t} to={t === 'queues' ? '/admin' : `/admin/${t}`}
                            className={`px-3 py-1 rounded text-sm transition-colors ${
                                t === tab ? 'bg-bone/10 text-bone' : 'text-slate hover:text-bone'
                            }`}>
                            {LABEL[t]}
                        </Link>
                    ))}
                </nav>
                <Page />
            </div>
        </div>
    )
}

export default AdminPanel
