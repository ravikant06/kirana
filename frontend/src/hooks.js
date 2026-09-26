import { useCallback, useEffect, useState } from 'react'

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
