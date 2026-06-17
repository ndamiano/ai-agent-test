import { useState } from 'react';
import Layout from './components/Layout';
import SettingsModal from './components/SettingsModal';
import ErrorBoundary from './components/ErrorBoundary';

function App() {
  const [showSettings, setShowSettings] = useState(false);

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
