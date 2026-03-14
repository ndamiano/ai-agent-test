import React, { useState, useRef, useEffect } from 'react';

interface CommandBarProps {
    onTaskCreate: (goal: string) => void;
    isLoading?: boolean;
}

const CommandBar: React.FC<CommandBarProps> = ({ onTaskCreate, isLoading = false }) => {
    const [goal, setGoal] = useState('');
    const textareaRef = useRef<HTMLTextAreaElement>(null);

    useEffect(() => {
        const textarea = textareaRef.current;
        if (!textarea) return;
        textarea.style.height = 'auto';
        textarea.style.height = `${Math.min(textarea.scrollHeight, 120)}px`;
    }, [goal]);

    const handleSubmit = (e: React.FormEvent) => {
        e.preventDefault();
        if (!goal.trim()) return;
        onTaskCreate(goal.trim());
        setGoal('');
    };

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
            e.preventDefault();
            handleSubmit(e);
        }
    };

    return (
        <div className="bg-gray-800 border-b border-gray-700 px-4 py-3">
            <form onSubmit={handleSubmit} className="flex items-center gap-3">
                <div className="flex-1 min-w-0">
                    <textarea
                        ref={textareaRef}
                        className="w-full bg-gray-700 text-white border border-gray-600 rounded-lg px-4 py-3 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                        placeholder="What would you like me to do?"
                        value={goal}
                        onChange={(e) => setGoal(e.target.value)}
                        onKeyDown={handleKeyDown}
                        rows={1}
                        style={{ minHeight: '48px', maxHeight: '120px', lineHeight: '1.5' }}
                        disabled={isLoading}
                    />
                </div>
                <button
                    type="submit"
                    disabled={isLoading || !goal.trim()}
                    className="bg-blue-600 hover:bg-blue-700 disabled:bg-gray-600 disabled:cursor-not-allowed disabled:opacity-50 text-white px-4 py-3 rounded-lg text-sm font-medium transition-colors whitespace-nowrap"
                >
                    {isLoading ? 'Submitting...' : 'Submit'}
                </button>
            </form>
        </div>
    );
};

export default CommandBar;