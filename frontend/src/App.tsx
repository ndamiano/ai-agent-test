import Layout from './components/Layout';
import LoginScreen from './components/LoginScreen';
import ErrorBoundary from './components/ErrorBoundary';
import { useAuth } from './contexts/AuthContext';

function App() {
  const { token } = useAuth();

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
        <Layout />
      </ErrorBoundary>
    </div>
  );
}

export default App;
