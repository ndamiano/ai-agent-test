import React from "react";

interface BounceDotsProps {
    color?: string;
    size?: number;
}

const BounceDots: React.FC<BounceDotsProps> = ({ color = "blue-500", size = 1.5 }) => {
    const sizeClass = `w-${size} h-${size}`;
    return (
        <div className="flex gap-1">
            <div className={`${sizeClass} rounded-full bg-${color} animate-bounce`} style={{ animationDelay: '0ms' }} />
            <div className={`${sizeClass} rounded-full bg-${color} animate-bounce`} style={{ animationDelay: '150ms' }} />
            <div className={`${sizeClass} rounded-full bg-${color} animate-bounce`} style={{ animationDelay: '300ms' }} />
        </div>
    );
};

export default BounceDots;
