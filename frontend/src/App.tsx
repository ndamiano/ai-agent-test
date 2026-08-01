import Layout from './components/Layout'
import LoginScreen from './components/LoginScreen'
import ErrorBoundary from './components/ErrorBoundary'
import { useAuth } from './contexts/AuthContext'

function App() {
    const { token } = useAuth()

    return (
        <div className="min-h-screen bg-ink">
            <ErrorBoundary>
                {token ? <Layout /> : <LoginScreen />}
            </ErrorBoundary>
        </div>
    )
}

export default App
