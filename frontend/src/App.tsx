import { useState } from 'react';
import Layout from './components/Layout';
import LoginScreen from './components/LoginScreen';
import SettingsModal from './components/SettingsModal';
import ErrorBoundary from './components/ErrorBoundary';
import { useAuth } from './contexts/AuthContext';

function App() {
  const { token } = useAuth();
  const [showSettings, setShowSettings] = useState(false);

  if (!token) {
    return (
      <div className="min-h-screen bg-gray-900">
        <ErrorBoundary>
          <LoginScreen />
        </ErrorBoundary>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-900">
      <ErrorBoundary>
        <Layout onSettingsClick={() => setShowSettings(true)} />
        {showSettings && <SettingsModal onClose={() => setShowSettings(false)} />}
      </ErrorBoundary>
    </div>
  );
}

export default App;
