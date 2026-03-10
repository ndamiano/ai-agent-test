import React, { useState, useEffect } from 'react';
import { api } from '../api/client';
import { LoadingSpinner } from '../assets/LoadingSpinner';
import { MarkdownRenderer } from '../assets/MarkdownRenderer';
import { AskBar } from '../assets/AskBar';
import { TabBar } from '../assets/TabBar';

interface OutputPanelProps {
    taskId: string | null;
}

export const OutputPanel: React.FC<OutputPanelProps> = ({ taskId }) => {
    const [contextKeys, setContextKeys] = useState<string[]>([]);
    const [selectedTab, setSelectedTab] = useState<string | null>(null);
    const [content, setContent] = useState<string>('');
    const [loading, setLoading] = useState<boolean>(false);
    const [askInput, setAskInput] = useState<string>('');
    const [chatHistory, setChatHistory] = useState<{ question: string; answer: string }[]>([]);

    useEffect(() => {
        if (!taskId) {
            setContextKeys([]);
            setSelectedTab(null);
            setContent('');
            return;
        }

        fetchContextKeys();
    }, [taskId]);

    const fetchContextKeys = async () => {
        if (!taskId) return;

        setLoading(true);
        try {
            const response = await api.getTask(taskId);
            const keys = response.context_keys;
            setContextKeys(keys);
            if (keys.length > 0) {
                setSelectedTab(keys[0]);
                fetchContent(keys[0]);
            }
        } catch (error) {
            console.error('Failed to fetch task context keys:', error);
        } finally {
            setLoading(false);
        }
    };

    const fetchContent = async (key: string) => {
        if (!taskId) return;

        setLoading(true);
        try {
            const response = await api.getContextValue(taskId, key);
            setContent(response || '');
        } catch (error) {
            console.error('Failed to fetch context value:', error);
            setContent('Error loading content');
        } finally {
            setLoading(false);
        }
    };

    const handleTabChange = (key: string) => {
        setSelectedTab(key);
        fetchContent(key);
    };

    const handleAskSubmit = async () => {
        if (!taskId || !askInput.trim()) return;

        try {
            const response = await api.askTask(taskId, askInput);
            setChatHistory([...chatHistory, { question: askInput, answer: response.answer }]);
            setAskInput('');
        } catch (error) {
            console.error('Failed to ask task:', error);
        }
    };

    if (!taskId) {
        return (
            <div className="h-full flex flex-col p-4">
                <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-4">
                    Output
                </h3>
                <div className="flex-1 bg-gray-200 dark:bg-gray-700 rounded p-4 text-gray-600 dark:text-gray-400">
                    <p className="text-center text-gray-500 dark:text-gray-400">
                        Select a task to see output
                    </p>
                </div>
            </div>
        );
    }

    return (
        <div className="h-full flex flex-col p-4">
            <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-4">
                Output
            </h3>

            {loading && (
                <div className="flex-1 bg-gray-200 dark:bg-gray-700 rounded p-4 text-gray-600 dark:text-gray-400 flex items-center justify-center">
                    <LoadingSpinner />
                </div>
            )}

            {!loading && (
                <div className="flex-1 bg-gray-200 dark:bg-gray-700 rounded p-4 text-gray-600 dark:text-gray-400 flex flex-col">
                    {contextKeys.length === 0 ? (
                        <p className="text-center text-gray-500 dark:text-gray-400">
                            No output available for this task
                        </p>
                    ) : (
                        <>
                            <TabBar
                                tabs={contextKeys}
                                selectedTab={selectedTab!}
                                onTabChange={handleTabChange}
                            />

                            <div className="flex-1 mt-4">
                                {selectedTab && (
                                    <div className="space-y-4">
                                        {content.trim() === '' ? (
                                            <p className="text-gray-500 dark:text-gray-400">
                                                Agent hasn't produced output yet.
                                            </p>
                                        ) : (
                                            <MarkdownRenderer content={content} />
                                        )}
                                    </div>
                                )}
                            </div>

                            <div className="mt-4 pt-4 border-t border-gray-300 dark:border-gray-600">
                                <AskBar
                                    value={askInput}
                                    onChange={setAskInput}
                                    onSubmit={handleAskSubmit}
                                    chatHistory={chatHistory}
                                />
                            </div>
                        </>
                    )}
                </div>
            )}
        </div>
    );
};