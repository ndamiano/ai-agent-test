import React from 'react';

interface AskBarProps {
    value: string;
    onChange: (value: string) => void;
    onSubmit: () => void;
    chatHistory: any[];
}

export const AskBar: React.FC<AskBarProps> = ({ value, onChange, onSubmit, chatHistory }) => {
    return (
        <div className="flex flex-col">
            <div className="flex-1 overflow-y-auto mb-2">
                {chatHistory.map((item, index) => (
                    <div key={index} className="mb-3">
                        <div className="flex items-start mb-1">
                            <div className="flex-shrink-0">
                                <div className="w-2 h-2 bg-blue-500 rounded-full"></div>
                            </div>
                            <div className="ml-2">
                                <div className="bg-blue-50 dark:bg-blue-900 text-blue-900 dark:text-blue-200 rounded-lg p-2 text-sm">
                                    {item.question}
                                </div>
                            </div>
                        </div>
                        <div className="flex items-start">
                            <div className="flex-shrink-0">
                                <div className="w-2 h-2 bg-green-500 rounded-full"></div>
                            </div>
                            <div className="ml-2">
                                <div className="bg-green-50 dark:bg-green-900 text-green-900 dark:text-green-200 rounded-lg p-2 text-sm">
                                    {item.answer}
                                </div>
                            </div>
                        </div>
                    </div>
                ))}
            </div>

            <div className="flex">
                <input
                    type="text"
                    value={value}
                    onChange={(e) => onChange(e.target.value)}
                    placeholder="Ask a question about the task..."
                    className="flex-1 px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-l-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                />
                <button
                    onClick={onSubmit}
                    disabled={!value.trim()}
                    className="px-4 py-2 bg-blue-500 hover:bg-blue-600 text-white rounded-r-lg disabled:opacity-50"
                >
                    Send
                </button>
            </div>
        </div>
    );
};