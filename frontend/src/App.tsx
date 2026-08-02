import Layout from './components/Layout'
import LoginScreen from './components/LoginScreen'
import ErrorBoundary from './components/ErrorBoundary'
import { useAuth } from './contexts/AuthContext'
import { RouterProvider } from './router'

function App() {
    const { token } = useAuth()

    return (
        <div className="min-h-screen bg-ink">
            <ErrorBoundary>
                {token ? <RouterProvider><Layout /></RouterProvider> : <LoginScreen />}
            </ErrorBoundary>
        </div>
    )
}

export default App
