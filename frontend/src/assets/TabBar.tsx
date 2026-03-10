import React from 'react';

interface TabBarProps {
    tabs: string[];
    selectedTab: string;
    onTabChange: (tab: string) => void;
}

export const TabBar: React.FC<TabBarProps> = ({ tabs, selectedTab, onTabChange }) => {
    return (
        <div className="flex space-x-2">
            {tabs.map((tab) => (
                <button
                    key={tab}
                    onClick={() => onTabChange(tab)}
                    className={`px-3 py-1 rounded-t-lg font-medium transition-colors ${selectedTab === tab
                            ? 'bg-gray-200 dark:bg-gray-700 text-blue-600 dark:text-blue-400'
                            : 'bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-700'
                        }`}
                >
                    {tab}
                </button>
            ))}
        </div>
    );
};