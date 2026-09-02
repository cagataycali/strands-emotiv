import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { AuthGate } from './AuthGate'
import './panels/right/consent.css'
import './theme.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AuthGate><App /></AuthGate>
  </React.StrictMode>,
)
