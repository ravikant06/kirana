import { useState } from 'react'
import { api, signIn } from '../api.js'
import Problem from '../components/Problem.jsx'

// Seeded accounts (migration V10 gave every existing user this password; user 1 is the admin).
const DEMO_PASSWORD = 'kirana123'
const DEMO = [
  { label: 'Ravi', role: 'ADMIN', email: 'kumar.ravee101@gmail.com' },
  { label: 'Puja', role: 'SHOPPER', email: 'puja-kumari@gmail.com' },
]

// Sign in (email + password → RS256 token) or create a shopper account. AI Phase 5.
export default function Login({ notify }) {
  const [mode, setMode] = useState('signin')
  const [form, setForm] = useState({ name: '', email: '', password: '' })
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const fieldErr = (f) => error?.problem?.errors?.find((e) => e.field === f)?.message
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value })

  const submit = async (e) => {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      if (mode === 'signup') await api.auth.signUp(form)
      const s = await signIn(form.email, form.password)
      notify(`Signed in as ${s.user.name}`)
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-page">
      <div className="login-card card">
        <div className="login-brand">
          <span className="brand-word">kirana<span className="brand-dot">.</span></span>
          <p className="muted">{mode === 'signin' ? 'Sign in to shop and chat with the assistant.' : 'Create a shopper account.'}</p>
        </div>

        <form className="form" onSubmit={submit} noValidate>
          {mode === 'signup' && (
            <label>
              <span>Name</span>
              <input autoComplete="name" value={form.name} onChange={set('name')} placeholder="Asha Verma" />
              {fieldErr('name') && <em className="field-err">{fieldErr('name')}</em>}
            </label>
          )}
          <label>
            <span>Email</span>
            <input type="email" autoComplete="username" autoFocus value={form.email} onChange={set('email')} placeholder="you@example.com" />
            {fieldErr('email') && <em className="field-err">{fieldErr('email')}</em>}
          </label>
          <label>
            <span>Password</span>
            <input type="password" autoComplete={mode === 'signin' ? 'current-password' : 'new-password'}
                   value={form.password} onChange={set('password')} placeholder={mode === 'signup' ? 'At least 8 characters' : ''} />
            {fieldErr('password') && <em className="field-err">{fieldErr('password')}</em>}
          </label>
          {error && !error.problem?.errors?.length && <Problem error={error} compact />}
          <button className="btn btn-add login-submit" disabled={busy}>
            {busy ? 'Please wait…' : mode === 'signin' ? 'Sign in' : 'Create account'}
          </button>
        </form>

        <button className="btn-quiet login-switch" onClick={() => { setMode(mode === 'signin' ? 'signup' : 'signin'); setError(null) }}>
          {mode === 'signin' ? 'New here? Create an account' : 'Have an account? Sign in'}
        </button>

        {mode === 'signin' && (
          <div className="login-demo">
            <strong>Demo accounts</strong> <span className="muted">password <code>{DEMO_PASSWORD}</code></span>
            <div className="login-demo-list">
              {DEMO.map((d) => (
                <button key={d.email} type="button" className="login-demo-btn"
                        onClick={() => setForm({ ...form, email: d.email, password: DEMO_PASSWORD })}>
                  <span>{d.label}</span>
                  <span className={`role-badge role-${d.role.toLowerCase()}`}>{d.role}</span>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
