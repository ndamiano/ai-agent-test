import TaskListPanel from './TaskListPanel';
import ActivityFeedPanel from './ActivityFeedPanel';
import { OutputPanel } from './OutputPanel';
import CommandBar from '../assets/CommandBar';

interface LayoutProps {
    selectedTaskId: string | null;
    setSelectedTaskId: (id: string | null) => void;
    systemStatus: { connected: boolean; message: string };
    onTaskCreate: (goal: string) => void;
}

const Layout: React.FC<LayoutProps> = ({
    selectedTaskId,
    setSelectedTaskId,
    systemStatus,
    onTaskCreate,
}) => {
    return (
        <div className="h-screen flex flex-col overflow-hidden">

            {/* Header */}
            <div className="bg-gray-800 border-b border-gray-700 px-4 py-2 flex items-center justify-between">
                <div className="text-white font-semibold text-lg">AI Agent Test</div>
                <div className="flex items-center space-x-2">
                    <div
                        className={`w-3 h-3 rounded-full ${systemStatus.connected ? 'bg-green-500' : 'bg-red-500'
                            }`}
                    />
                    <span className="text-xs text-gray-400">
                        {systemStatus.message}
                    </span>
                </div>
            </div>
            {/* Command Bar */}
            <CommandBar onTaskCreate={onTaskCreate} />

            {/* Three panel layout */}
            <div className="flex-1 flex overflow-hidden">
                {/* Task List Panel */}
                <div className="w-72 bg-gray-100 dark:bg-gray-800 border-r border-gray-700">
                    <TaskListPanel
                        selectedTaskId={selectedTaskId}
                        onSelectTask={setSelectedTaskId}
                    />
                </div>

                {/* Activity Feed Panel */}
                <div className="flex-1 bg-gray-100 dark:bg-gray-800 border-r border-gray-700">
                    <ActivityFeedPanel taskId={selectedTaskId} />
                </div>

                {/* Output Panel */}
                <div className="w-96 bg-gray-100 dark:bg-gray-800">
                    <OutputPanel taskId={selectedTaskId} />
                </div>
            </div>
        </div>
    );
};

export default Layout;