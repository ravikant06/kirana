import { useCallback, useEffect, useState } from 'react'

// Re-runs loader every `ms` while mounted; keeps the last good value, ignores errors.
export function usePoll(loader, ms, deps) {
  const [data, setData] = useState(null)
  useEffect(() => {
    let alive = true
    const tick = async () => {
      try {
        const d = await loader()
        if (alive) setData(d)
      } catch {
        /* keep the previous value */
      }
    }
    tick()
    const t = setInterval(tick, ms)
    return () => {
      alive = false
      clearInterval(t)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  return data
}

export function useLoad(loader, deps) {
  const [state, setState] = useState({ data: null, error: null, loading: true })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const run = useCallback(async () => {
    setState((s) => ({ ...s, loading: true, error: null }))
    try {
      const data = await loader()
      setState({ data, error: null, loading: false })
    } catch (error) {
      setState({ data: null, error, loading: false })
    }
  }, deps)
  useEffect(() => {
    run()
  }, [run])
  return { ...state, reload: run }
}
