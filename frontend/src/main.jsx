import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
// Self-hosted variable font (SIL Open Font License). A real typeface is the single
// biggest difference between a designed product and a default one, and self-hosting
// keeps it working on a slow rural connection with no third-party request.
import '@fontsource-variable/inter'
import App from './App.jsx'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
)
