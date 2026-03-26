import React, { useState, useEffect } from 'react'

export const FadeSlideIn: React.FC<{
    children: React.ReactNode
    delay?: number
    className?: string
}> = ({ children, delay = 0, className = '' }) => {
    const [visible, setVisible] = useState(false)

    useEffect(() => {
        const t = setTimeout(() => setVisible(true), delay)
        return () => clearTimeout(t)
    }, [delay])

    return (
        <div
            className={`transition-all duration-500 ease-out ${visible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-3'
                } ${className}`}
        >
            {children}
        </div>
    )
}
