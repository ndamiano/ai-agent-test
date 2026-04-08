import { useState, useEffect, useCallback } from 'react';
import Layout from './components/Layout';
import SettingsModal from './components/SettingsModal';
import { api } from './api/client';
import ErrorBoundary from './components/ErrorBoundary';
import { useWebSocket } from './contexts/WebSocketContext';
import type { Task } from './types';

function App() {
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [workingDirectory, setWorkingDirectory] = useState<string | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [tasksLoading, setTasksLoading] = useState(false);
  const [tasksError, setTasksError] = useState<string | null>(null);
  const { subscribe } = useWebSocket();

  const fetchTasks = useCallback(async () => {
    setTasksLoading(true);
    setTasksError(null);
    try {
      const fetchedTasks = await api.listTasks();
      setTasks(fetchedTasks.filter(t => t.status !== 'archived'));
    } catch (e) {
      setTasksError(e instanceof Error ? e.message : 'Failed to fetch tasks');
    } finally {
      setTasksLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchTasks();
    // Fetch working directory from settings
    api.getSettings().then(settings => {
      if (settings.working_directory) {
        setWorkingDirectory(settings.working_directory);
      }
    }).catch(console.error);
  }, [fetchTasks]);

  // Listen for WebSocket events to update task statuses in real-time
  useEffect(() => {
    const unsubscribers: Array<() => void> = [];

    tasks.forEach(task => {
      const unsubscribe = subscribe(task.id, (message) => {
        if (message.type === 'task_status') {
          setTasks(prev => prev.map(t =>
            t.id === message.task_id ? { ...t, ...message.task } : t
          ));
        } else if (message.type === 'task_completed' || message.type === 'task_failed') {
          setTasks(prev => prev.map(t =>
            t.id === message.task_id ? { ...t, status: message.type === 'task_completed' ? 'completed' : 'failed' } : t
          ));
        }
      });
      unsubscribers.push(unsubscribe);
    });

    return () => {
      unsubscribers.forEach(unsub => unsub());
    };
  }, [tasks.map(t => t.id).join(','), subscribe]);

  const handleTaskCreate = async (goal: string) => {
    try {
      const newTask = await api.createTask(goal, workingDirectory || undefined);
      setSelectedTaskId(newTask.id);
      setTasks(prev => [newTask, ...prev]);
    } catch (error) {
      console.error('Failed to create task:', error);
    }
  };

  return (
    <div className="min-h-screen bg-gray-900">
      <ErrorBoundary>
        <Layout
          selectedTaskId={selectedTaskId}
          setSelectedTaskId={setSelectedTaskId}
          onTaskCreate={handleTaskCreate}
          onSettingsClick={() => setShowSettings(true)}
          tasks={tasks}
          tasksLoading={tasksLoading}
          tasksError={tasksError}
          onRefreshTasks={fetchTasks}
        />
        {showSettings && <SettingsModal onClose={() => setShowSettings(false)} />}
      </ErrorBoundary>
    </div>
  );
}

export default App;