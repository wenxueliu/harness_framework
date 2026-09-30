export type ThemeMode = 'system' | 'light' | 'dark'

const STORAGE_KEY = 'harness-dashboard-theme'

function systemTheme(): 'light' | 'dark' {
  return typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: light)').matches
    ? 'light' : 'dark'
}

export function readThemeMode(): ThemeMode {
  if (typeof window === 'undefined') return 'system'
  const value = window.localStorage.getItem(STORAGE_KEY)
  return value === 'light' || value === 'dark' ? value : 'system'
}

export function applyTheme(mode: ThemeMode): void {
  const effective = mode === 'system' ? systemTheme() : mode
  document.documentElement.dataset.theme = effective
  window.localStorage.setItem(STORAGE_KEY, mode)
}
