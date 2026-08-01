import React from 'react'

export type ButtonVariant = 'primary' | 'quiet' | 'ghost' | 'danger'
export type ButtonSize = 'sm' | 'md' | 'lg'

const VARIANT: Record<ButtonVariant, string> = {
    primary: 'bg-ember text-ember-ink hover:bg-ember/90 font-bold',
    quiet: 'bg-bone/[0.07] text-bone hover:bg-bone/[0.12] font-semibold',
    ghost: 'border border-edge text-bone hover:bg-bone/[0.05] font-semibold',
    danger: 'border border-fail/40 text-fail hover:bg-fail/10 font-semibold',
}

const SIZE: Record<ButtonSize, string> = {
    sm: 'text-xs px-2.5 py-1.5',
    md: 'text-sm px-4 py-2',
    lg: 'text-base px-6 py-2.5',
}

interface Props extends React.ButtonHTMLAttributes<HTMLButtonElement> {
    variant?: ButtonVariant
    size?: ButtonSize
}

export const Button: React.FC<Props> = ({
    variant = 'quiet', size = 'sm', className = '', ...rest
}) => (
    <button
        className={`rounded transition-colors disabled:opacity-40 disabled:cursor-not-allowed
                    ${VARIANT[variant]} ${SIZE[size]} ${className}`}
        {...rest}
    />
)

// An <a> that carries a button's weight — Play opens the staged game, which is a navigation.
export const LinkButton: React.FC<
    React.AnchorHTMLAttributes<HTMLAnchorElement> & { variant?: ButtonVariant; size?: ButtonSize }
> = ({ variant = 'primary', size = 'sm', className = '', ...rest }) => (
    <a
        className={`inline-flex items-center gap-1.5 rounded transition-colors
                    ${VARIANT[variant]} ${SIZE[size]} ${className}`}
        {...rest}
    />
)
