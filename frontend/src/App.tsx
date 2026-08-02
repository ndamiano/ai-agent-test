import Layout from './components/Layout'
import LoginScreen from './components/LoginScreen'
import Landing from './components/public/Landing'
import ErrorBoundary from './components/ErrorBoundary'
import { useAuth } from './contexts/AuthContext'
import { RouterProvider, useRouter } from './router'

// Signed out, the site is the pitch: the landing page with playable demos, and /login for the
// people who have an account. Signed in, every path belongs to the app.
const PublicSite = () => {
    const { path } = useRouter()
    return path === '/login' ? <LoginScreen /> : <Landing />
}

function App() {
    const { token } = useAuth()

    return (
        <div className="min-h-screen bg-ink">
            <ErrorBoundary>
                <RouterProvider>
                    {token ? <Layout /> : <PublicSite />}
                </RouterProvider>
            </ErrorBoundary>
        </div>
    )
}

export default App
