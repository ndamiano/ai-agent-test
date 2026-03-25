import { useState, useEffect } from 'react';
import Layout from './components/Layout';
import SettingsModal from './components/SettingsModal';
import { api } from './api/client';
import ErrorBoundary from './components/ErrorBoundary';

function App() {
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [systemStatus, setSystemStatus] = useState<{ connected: boolean; message: string }>({
    connected: false,
    message: 'LMStudio disconnected',
  });
  const [taskListRefreshKey, setTaskListRefreshKey] = useState(0);

  const handleTaskCreate = async (goal: string) => {
    try {
      const newTask = await api.createTask(goal);
      setSelectedTaskId(newTask.id);
      setTaskListRefreshKey(prev => prev + 1);
    } catch (error) {
      console.error('Failed to create task:', error);
    }
  };

  // Fetch task detail when selectedTaskId changes
  useEffect(() => {
    if (selectedTaskId) {
      const fetchTaskDetail = async () => {
        try {
          await api.getTask(selectedTaskId);
        } catch (error) {
          console.error('Failed to fetch task detail:', error);
        }
      };
      fetchTaskDetail();
    }
  }, [selectedTaskId]);

  // Fetch system status every 30 seconds
  useEffect(() => {
    const checkStatus = async () => {
      try {
        const status = await api.getStatus();
        setSystemStatus({
          connected: status.lmstudio_connected,
          message: status.lmstudio_connected ? 'LMStudio connected' : 'LMStudio disconnected',
        });
      } catch (error) {
        console.error(error);
        setSystemStatus({ connected: false, message: 'LMStudio disconnected' });
      }
    };

    checkStatus();
    const intervalId = setInterval(checkStatus, 30000);

    return () => clearInterval(intervalId);
  }, []);

  return (
    <div className="min-h-screen bg-gray-900">
      <ErrorBoundary>
        <Layout
          selectedTaskId={selectedTaskId}
          setSelectedTaskId={setSelectedTaskId}
          systemStatus={systemStatus}
          onTaskCreate={handleTaskCreate}
          onSettingsClick={() => setShowSettings(true)}
          taskListRefreshKey={taskListRefreshKey}
        />
        {showSettings && <SettingsModal onClose={() => setShowSettings(false)} />}
      </ErrorBoundary>
    </div>
  );
}

export default App;