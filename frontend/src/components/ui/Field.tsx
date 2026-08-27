import React from 'react'

const BASE = `w-full bg-sunken border border-edge rounded text-bone placeholder:text-dim
              focus:border-ember/60 focus:outline-none disabled:opacity-60 disabled:cursor-not-allowed`

export const TextInput: React.FC<React.InputHTMLAttributes<HTMLInputElement>> = ({
    className = '', ...rest
}) => <input className={`${BASE} text-sm px-3 py-2 ${className}`} {...rest} />

export const TextArea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(({
    className = '', ...rest
}, ref) => <textarea ref={ref} className={`${BASE} text-[15px] leading-relaxed px-3.5 py-3 resize-y ${className}`} {...rest} />)

export const SectionLabel: React.FC<{ children: React.ReactNode }> = ({ children }) => (
    <h3 className="text-xs font-mono uppercase tracking-[0.13em] text-dim">{children}</h3>
)
