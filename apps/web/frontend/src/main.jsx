import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { QueryClientProvider } from '@tanstack/react-query';
import './tokens.css';
import { queryClient } from './lib/query';
import { AuthProvider } from './lib/auth';
import { ThemeProvider } from './lib/theme';
import { installGlobalErrorHandlers } from './lib/logger';
import ErrorBoundary from './components/ErrorBoundary';
import App from './App';

// window.onerror / unhandledrejection / periodic flush (doc §6.1)
installGlobalErrorHandlers();

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <BrowserRouter>
          <AuthProvider>
            <ErrorBoundary>
              <App />
            </ErrorBoundary>
          </AuthProvider>
        </BrowserRouter>
      </ThemeProvider>
    </QueryClientProvider>
  </React.StrictMode>,
);
