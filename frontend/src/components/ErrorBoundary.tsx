import React, { Component } from 'react';

interface ErrorBoundaryState {
    hasError: boolean;
}

class ErrorBoundary extends Component<{ children: React.ReactNode }, ErrorBoundaryState> {
    constructor(props: { children: React.ReactNode }) {
        super(props);
        this.state = { hasError: false };
    }

    static getDerivedStateFromError(error: Error): ErrorBoundaryState {
        return { hasError: true };
    }

    componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
        console.error('Error caught by ErrorBoundary:', error, errorInfo);
    }

    render() {
        if (this.state.hasError) {
            return (
                <div className="min-h-screen bg-gray-900 flex items-center justify-center">
                    <div className="text-white text-center">
                        <h1 className="text-2xl font-semibold mb-4">Something went wrong</h1>
                        <p className="text-gray-400">
                            An error occurred in the application. Please refresh the page to continue.
                        </p>
                    </div>
                </div>
            );
        }

        return this.props.children;
    }
}

export default ErrorBoundary;