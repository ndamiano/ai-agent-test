// A read view of the design text, derived from its shape and nothing else: the text stays the
// one thing that builds, and this only decides where the headings go. The designer writes a lead
// line, a "SYSTEMS: a, b, c." line, then one paragraph per system opening "NAME:" in capitals
// (prompts/design.txt). Text that has none of that — the user's own words when the designer
// answered nothing — is one unnamed section.

export interface DesignSection {
    name: string | null
    body: string
}

export interface DesignView {
    lead: string
    systems: string[]
    sections: DesignSection[]
}

const HEADING = /^([A-Z][A-Z0-9 &'/-]{0,60}):\s*(.*)$/
const LEAD = /^Build this\b/

export const parseDesign = (text: string): DesignView => {
    const view: DesignView = { lead: '', systems: [], sections: [] }
    let current: DesignSection | null = null
    const lines = text.split('\n')
    for (let i = 0; i < lines.length; i++) {
        const line = lines[i].trim()
        if (!line) continue
        const head = HEADING.exec(line)
        if (head && head[1] === 'SYSTEMS' && view.systems.length === 0) {
            view.systems = head[2].replace(/\.\s*$/, '').split(',').map(s => s.trim()).filter(Boolean)
            continue
        }
        if (!current && !view.lead && LEAD.test(line)) { view.lead = line; continue }
        if (head) {
            current = { name: head[1], body: head[2] }
            view.sections.push(current)
            continue
        }
        if (!current) {
            current = { name: null, body: line }
            view.sections.push(current)
            continue
        }
        current.body = current.body ? `${current.body}\n${line}` : line
    }
    return view
}
